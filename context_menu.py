"""
context_menu.py — menus contextuels (clic droit / appui long) de la sidebar.

Les entrées déclenchent les actions « win.item-* » et « win.trash-* » de la
fenêtre (voir window.py), avec le chemin de l'élément comme paramètre.
"""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from i18n import _  # noqa: E402


def _item(label: str, action: str, path: Path) -> Gio.MenuItem:
    item = Gio.MenuItem.new(label, None)
    item.set_action_and_target_value(f"win.{action}", GLib.Variant.new_string(str(path)))
    return item


def _section(menu: Gio.Menu, *items: Gio.MenuItem) -> None:
    section = Gio.Menu()
    for item in items:
        section.append_item(item)
    menu.append_section(None, section)


def build_empty_menu(root: Path) -> Gio.Menu:
    """Clic droit sur la zone vide de la barre latérale : création à la racine."""
    menu = Gio.Menu()
    _section(menu,
             _item(_("Nouvelle note…"), "item-new-note", root),
             _item(_("Nouveau dossier…"), "item-new-folder", root))
    return menu


def build_item_menu(path: Path, is_dir: bool, is_pinned: bool) -> Gio.Menu:
    """
    Menu d'un élément de l'arborescence ou des épingles :
      dossier : Nouvelle note ici · Nouveau dossier ici | Renommer | Supprimer
      note    : Ouvrir dans un nouvel onglet | Épingler · Renommer | Supprimer
    « Supprimer » met à la corbeille (annulable) ; la suppression définitive se
    fait depuis la corbeille.
    """
    menu = Gio.Menu()
    rename = _item(_("Renommer…"), "item-rename", path)
    if is_dir:
        _section(menu,
                 _item(_("Nouvelle note ici…"), "item-new-note", path),
                 _item(_("Nouveau dossier ici…"), "item-new-folder", path))
        _section(menu, rename)
    else:
        _section(menu, _item(_("Ouvrir dans un nouvel onglet"), "item-open-tab", path))
        _section(menu,
                 _item(_("Désépingler") if is_pinned else _("Épingler"), "item-pin", path),
                 rename)
    _section(menu, _item(_("Supprimer"), "item-delete", path))
    return menu


def build_trash_menu(trashed: Path, is_dir: bool) -> Gio.Menu:
    """Menu d'un élément de la corbeille (`trashed` : son chemin dans .trash)."""
    menu = Gio.Menu()
    restore = Gio.Menu()
    restore.append_item(_item(_("Restaurer le dossier") if is_dir else _("Restaurer la note"),
                              "trash-restore", trashed))
    menu.append_section(None, restore)
    danger = Gio.Menu()
    danger.append_item(_item(_("Supprimer définitivement…"), "trash-delete", trashed))
    menu.append_section(None, danger)
    return menu


def popup_menu(widget: Gtk.Widget, menu: Gio.MenuModel, x: float, y: float) -> None:
    """Affiche `menu` à la position (x, y) relative à `widget`."""
    popover = Gtk.PopoverMenu.new_from_model(menu)
    popover.set_parent(widget)
    popover.set_has_arrow(False)
    popover.set_halign(Gtk.Align.START)
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    popover.set_pointing_to(rect)

    # Détaché après coup : l'action choisie doit d'abord être résolue via `widget`.
    def on_closed(p):
        GLib.idle_add(lambda: (p.unparent(), GLib.SOURCE_REMOVE)[1])

    popover.connect("closed", on_closed)
    popover.popup()
