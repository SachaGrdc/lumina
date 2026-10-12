"""
tabs.py — onglets de l'éditeur (Adw.TabView + Adw.TabBar).

Chaque onglet contient une `NotePage` (editor.py). La barre d'onglets se masque
tant qu'il n'y a qu'un onglet. Sans aucun onglet (tous fermés, ou rien à rouvrir
au lancement), `widget` affiche à la place la page « état vide » fournie ; il
rebascule sur l'éditeur dès qu'un onglet est ajouté.

Le widget ne connaît pas la fenêtre : il signale le changement d'onglet via
`on_selected` (None quand il n'y a plus d'onglet) et reçoit les pages toutes faites.
"""

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from editor import NotePage  # noqa: E402

TRANSITION_MS = 200      # fondu état vide <-> éditeur


class NoteTabs:
    def __init__(self, on_selected: Callable[[NotePage | None], None], empty_page: Gtk.Widget):
        self._on_selected = on_selected
        self.view = Adw.TabView()
        self.bar = Adw.TabBar(view=self.view, autohide=True)

        # Ce qu'on place dans la fenêtre : l'état vide OU les onglets.
        self.widget = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                transition_duration=TRANSITION_MS)
        self.widget.add_named(empty_page, "empty")
        self.widget.add_named(self.view, "tabs")
        self.view.connect("notify::n-pages", lambda *_a: self._update_visible())
        self._update_visible()
        # Ctrl+Début / Ctrl+Fin doivent rester des raccourcis de l'éditeur de texte.
        if hasattr(self.view, "remove_shortcuts"):  # libadwaita >= 1.2
            self.view.remove_shortcuts(Adw.TabViewShortcuts.CONTROL_HOME
                                       | Adw.TabViewShortcuts.CONTROL_END
                                       | Adw.TabViewShortcuts.CONTROL_SHIFT_HOME
                                       | Adw.TabViewShortcuts.CONTROL_SHIFT_END)
        self.view.connect("notify::selected-page", self._on_selected_changed)
        self.view.connect("close-page", self._on_close_page)

    # ------------------------------------------------------------------ lecture

    @property
    def current(self) -> NotePage | None:
        tab = self.view.get_selected_page()
        return tab.get_child() if tab is not None else None

    def pages(self) -> list[NotePage]:
        return [self.view.get_nth_page(i).get_child() for i in range(self.view.get_n_pages())]

    def find(self, path: Path) -> NotePage | None:
        for page in self.pages():
            if page.path == path:
                return page
        return None

    def session(self) -> tuple[list[dict], int]:
        """Onglets à rouvrir au prochain lancement : ([{path, cursor}…], index de l'onglet actif)."""
        tabs, active, current = [], 0, self.current
        for page in self.pages():
            if page.path is None:  # brouillon vide : rien à rouvrir
                continue
            if page is current:
                active = len(tabs)
            tabs.append({"path": str(page.path), "cursor": page.cursor_offset()})
        return tabs, active

    # ------------------------------------------------------------------ écriture

    def add(self, page: NotePage, select: bool = True) -> NotePage:
        tab = self.view.append(page)
        self._sync(tab)
        if select:
            self.view.set_selected_page(tab)
        return page

    def select(self, page: NotePage) -> None:
        self.view.set_selected_page(self.view.get_page(page))

    def close(self, page: NotePage) -> None:
        self.view.close_page(self.view.get_page(page))

    def sync_titles(self) -> None:
        """Titres et infobulles des onglets (après ouverture, renommage, déplacement…)."""
        for i in range(self.view.get_n_pages()):
            self._sync(self.view.get_nth_page(i))

    # ------------------------------------------------------------------ interne

    @staticmethod
    def _sync(tab: Adw.TabPage):
        page = tab.get_child()
        tab.set_title(page.title)
        tooltip = ""
        if page.path is not None:
            try:
                tooltip = page.path.relative_to(page.store.root).as_posix()
            except ValueError:
                tooltip = page.path.name
        tab.set_tooltip(tooltip)

    def _update_visible(self):
        self.widget.set_visible_child_name("tabs" if self.view.get_n_pages() else "empty")

    def _on_selected_changed(self, *_args):
        self._update_visible()
        self._on_selected(self.current)   # None : plus aucun onglet

    def _on_close_page(self, view: Adw.TabView, tab: Adw.TabPage):
        tab.get_child().close()   # enregistre et arrête les minuteurs de l'onglet
        view.close_page_finish(tab, True)
        return True  # fermeture prise en charge
