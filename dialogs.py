"""
dialogs.py — boîtes de dialogue (saisie d'un nom, confirmation, choix d'un dossier).

Adw.AlertDialog est utilisé s'il est disponible (libadwaita >= 1.5), sinon
Adw.MessageDialog. Le choix de dossier passe par Gtk.FileDialog (GTK >= 4.10),
sinon Gtk.FileChooserNative.
"""

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from i18n import _  # noqa: E402


def _make_dialog(parent: Gtk.Window, heading: str, body: str):
    """Retourne (dialogue, fonction d'affichage)."""
    if hasattr(Adw, "AlertDialog"):  # libadwaita >= 1.5
        dialog = Adw.AlertDialog(heading=heading, body=body)
        return dialog, lambda: dialog.present(parent)
    dialog = Adw.MessageDialog(transient_for=parent, heading=heading, body=body)
    return dialog, dialog.present


def ask_text(parent: Gtk.Window, heading: str, body: str, default: str,
             ok_label: str, on_ok: Callable[[str], None]) -> None:
    """
    Demande un texte (nom de note / dossier) ; `on_ok` reçoit la saisie, jamais
    vide : le bouton de validation est désactivé tant que le champ l'est.
    """
    entry = Gtk.Entry(text=default, activates_default=True)
    entry.connect("map", lambda w: w.grab_focus())  # le texte proposé est sélectionné

    dialog, show = _make_dialog(parent, heading, body)
    dialog.set_extra_child(entry)
    dialog.add_response("cancel", _("Annuler"))
    dialog.add_response("ok", ok_label)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")

    def update_ok(_entry=None):
        dialog.set_response_enabled("ok", bool(entry.get_text().strip()))

    entry.connect("changed", update_ok)
    update_ok()

    def on_response(_dialog, response):
        text = entry.get_text().strip()
        if response == "ok" and text:
            on_ok(text)

    dialog.connect("response", on_response)
    show()


def confirm(parent: Gtk.Window, heading: str, body: str, ok_label: str,
            on_ok: Callable[[], None]) -> None:
    """Demande confirmation d'une action destructive."""
    dialog, show = _make_dialog(parent, heading, body)
    dialog.add_response("cancel", _("Annuler"))
    dialog.add_response("ok", ok_label)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def on_response(_dialog, response):
        if response == "ok":
            on_ok()

    dialog.connect("response", on_response)
    show()


def choose_folder(parent: Gtk.Window, title: str, initial: Path | None,
                  on_chosen: Callable[[Path], None]) -> None:
    """Sélecteur de dossier natif ; `on_chosen` n'est appelé que si l'utilisateur valide."""
    start = Gio.File.new_for_path(str(initial)) if initial and initial.is_dir() else None

    if hasattr(Gtk, "FileDialog"):  # GTK >= 4.10
        dialog = Gtk.FileDialog(title=title, modal=True)
        if start is not None:
            dialog.set_initial_folder(start)

        def on_done(d, result):
            try:
                folder = d.select_folder_finish(result)
            except GLib.Error:  # annulé
                return
            if folder is not None and folder.get_path():
                on_chosen(Path(folder.get_path()))

        dialog.select_folder(parent, None, on_done)
        return

    chooser = Gtk.FileChooserNative(title=title, transient_for=parent, modal=True,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER)
    if start is not None:
        chooser.set_current_folder(start)

    def on_response(c, response):
        if response == Gtk.ResponseType.ACCEPT:
            folder = c.get_file()
            if folder is not None and folder.get_path():
                on_chosen(Path(folder.get_path()))
        c.destroy()

    chooser.connect("response", on_response)
    chooser.show()
    parent._lumina_chooser = chooser  # garde une référence tant que le dialogue est ouvert
