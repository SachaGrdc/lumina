"""
pinned.py — section « Épinglées » tout en haut de la barre latérale.

Liste plate des notes épinglées, qui se déploie en douceur (Gtk.Revealer) dès
qu'il y en a au moins une. Le widget ne connaît ni le disque ni la fenêtre : il
reçoit des chemins et signale les interactions via des fonctions de rappel.
"""

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from i18n import _  # noqa: E402


class PinnedSection(Gtk.Revealer):
    def __init__(self, root: Path,
                 on_activate: Callable[[Path], None],
                 on_context: Callable[[Path, Gtk.Widget, float, float], None]):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                         transition_duration=200)
        self._root = root
        self._on_activate = on_activate
        self._on_context = on_context
        self._paths: list[Path] = []
        self._current: Path | None = None
        self._enabled = True

        title = Gtk.Label(label=_("Épinglées"), xalign=0, margin_start=12, margin_end=12,
                          margin_top=6, margin_bottom=2)
        title.add_css_class("caption-heading")
        title.add_css_class("dim-label")

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-activated", self._on_row_activated)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(title)
        box.append(self.list_box)
        self.set_child(box)

    # ------------------------------------------------------------------ API

    def set_pins(self, paths: list[Path]) -> None:
        """Remplace la liste affichée (sans rien faire si elle n'a pas changé)."""
        if paths != self._paths:
            self._paths = list(paths)
            while (row := self.list_box.get_first_child()) is not None:
                self.list_box.remove(row)
            for path in self._paths:
                self.list_box.append(self._make_row(path))
        self._update_reveal()
        self.set_current(self._current)

    def set_current(self, path: Path | None) -> None:
        """Met en évidence la note actuellement ouverte, si elle est épinglée."""
        self._current = path
        row = self.list_box.get_first_child()
        while row is not None:
            if getattr(row, "note_path", None) == path and path is not None:
                self.list_box.select_row(row)
                return
            row = row.get_next_sibling()
        self.list_box.unselect_all()

    def set_enabled(self, enabled: bool) -> None:
        """Masque la section (ex. pendant une recherche) sans oublier les épingles."""
        self._enabled = enabled
        self._update_reveal()

    def focused_path(self) -> Path | None:
        """Note ayant le focus clavier dans la liste (à défaut, la ligne sélectionnée)."""
        row = self.list_box.get_focus_child() or self.list_box.get_selected_row()
        return getattr(row, "note_path", None)

    # ------------------------------------------------------------------ interne

    def _update_reveal(self) -> None:
        self.set_reveal_child(bool(self._paths) and self._enabled)

    def _make_row(self, path: Path) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Image.new_from_icon_name("view-pin-symbolic"))
        box.append(Gtk.Label(label=path.stem, xalign=0, hexpand=True,
                             ellipsize=Pango.EllipsizeMode.END))
        try:
            tooltip = path.relative_to(self._root).as_posix()
        except ValueError:
            tooltip = path.name
        row = Gtk.ListBoxRow(child=box, tooltip_text=tooltip)
        row.note_path = path

        # Menu contextuel : clic droit (souris) ou appui long (tactile).
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda _g, _n, x, y: self._on_context(path, row, x, y))
        row.add_controller(click)
        long_press = Gtk.GestureLongPress(touch_only=True)  # sinon un clic prolongé à la souris ouvre le menu
        long_press.connect("pressed", lambda _g, x, y: self._on_context(path, row, x, y))
        row.add_controller(long_press)
        return row

    def _on_row_activated(self, _list_box, row) -> None:
        self._on_activate(row.note_path)
