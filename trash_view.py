"""
trash_view.py — page « Corbeille » de la barre latérale.

Liste les éléments de `.trash` avec leur emplacement d'origine. Comme
pinned.py, le widget ne touche pas au disque : il reçoit des `TrashEntry` et
signale les interactions (menu contextuel) via une fonction de rappel. Le
bouton « Vider la corbeille » déclenche l'action `win.empty-trash`.
"""

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

from i18n import _  # noqa: E402
from storage import TrashEntry  # noqa: E402


class TrashView(Gtk.Box):
    def __init__(self, root: Path,
                 on_context: Callable[[TrashEntry, Gtk.Widget, float, float], None]):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._on_context = on_context
        self._entries: list[TrashEntry] = []
        self._root = root

        empty = Adw.StatusPage(icon_name="user-trash-symbolic",
                               title=_("La corbeille est vide"),
                               description=_("Les notes supprimées sont conservées ici "
                                             "jusqu'à ce que la corbeille soit vidée."),
                               vexpand=True)
        empty.add_css_class("compact")

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-activated", self._on_row_activated)
        scrolled = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      child=self.list_box)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vexpand=True)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(scrolled, "list")

        hint = Gtk.Label(label=_("Clic droit sur un élément pour le restaurer."),
                         wrap=True, xalign=0, margin_start=12, margin_end=12, margin_top=6)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        self._hint = hint

        empty_button = Gtk.Button(label=_("Vider la corbeille"), action_name="win.empty-trash",
                                  margin_start=12, margin_end=12, margin_top=6, margin_bottom=12)
        empty_button.add_css_class("destructive-action")

        self.append(hint)
        self.append(self.stack)
        self.append(empty_button)
        self.set_entries([])

    # ------------------------------------------------------------------ API

    def set_entries(self, entries: list[TrashEntry]) -> None:
        """Remplace la liste affichée (sans rien faire si elle n'a pas changé)."""
        if entries != self._entries:
            self._entries = list(entries)
            while (row := self.list_box.get_first_child()) is not None:
                self.list_box.remove(row)
            for entry in self._entries:
                self.list_box.append(self._make_row(entry))
        self.stack.set_visible_child_name("list" if self._entries else "empty")
        self._hint.set_visible(bool(self._entries))

    def focused_entry(self) -> TrashEntry | None:
        row = self.list_box.get_focus_child() or self.list_box.get_selected_row()
        return getattr(row, "trash_entry", None)

    # ------------------------------------------------------------------ interne

    def _location(self, entry: TrashEntry) -> str:
        try:
            parent = entry.original.parent.relative_to(self._root).as_posix()
        except ValueError:
            return entry.original.parent.name
        return parent if parent != "." else _("Racine")

    def _make_row(self, entry: TrashEntry) -> Gtk.ListBoxRow:
        icon = Gtk.Image.new_from_icon_name(
            "folder-symbolic" if entry.is_dir else "text-x-generic-symbolic")
        title = Gtk.Label(label=entry.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        location = Gtk.Label(label=self._location(entry), xalign=0,
                             ellipsize=Pango.EllipsizeMode.START)
        location.add_css_class("caption")
        location.add_css_class("dim-label")
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        labels.append(title)
        labels.append(location)

        box = Gtk.Box(spacing=8)
        box.append(icon)
        box.append(labels)
        row = Gtk.ListBoxRow(child=box, tooltip_text=_("Emplacement d'origine : {path}").format(
            path=self._location(entry)))
        row.trash_entry = entry

        # Menu contextuel : clic droit (souris) ou appui long (tactile).
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda _g, _n, x, y: self._on_context(entry, row, x, y))
        row.add_controller(click)
        long_press = Gtk.GestureLongPress(touch_only=True)
        long_press.connect("pressed", lambda _g, x, y: self._on_context(entry, row, x, y))
        row.add_controller(long_press)
        return row

    def _on_row_activated(self, _list_box, row) -> None:
        # Clic simple / Entrée : le même menu, ancré au milieu de la ligne.
        self._on_context(row.trash_entry, row, row.get_width() / 2, row.get_height() / 2)
