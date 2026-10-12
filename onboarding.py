"""
onboarding.py — réglage du premier lancement (pas encore de config.json),
affiché derrière le dialogue d'accueil (welcome.py).

Permet de choisir le dossier des notes et de créer quelques notes d'exemple.
La fenêtre ne touche pas au disque : « Commencer » appelle
`on_done(dossier, créer_exemples)`, qui retourne un message d'erreur ou None.
"""

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from dialogs import choose_folder  # noqa: E402
from i18n import _  # noqa: E402


def _has_visible_content(folder: Path) -> bool:
    try:
        return any(not p.name.startswith(".") for p in folder.iterdir())
    except OSError:
        return False


class OnboardingWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, default_dir: Path,
                 on_done: Callable[[Path, bool], str | None]):
        super().__init__(application=app, title="Lumina",
                         default_width=620, default_height=680)
        self._folder = default_dir
        self._on_done = on_done

        # Dossier des notes
        self.folder_row = Adw.ActionRow(title=_("Dossier des notes"))
        self.folder_row.add_css_class("property")
        choose = Gtk.Button(label=_("Choisir…"), valign=Gtk.Align.CENTER)
        choose.connect("clicked", self._on_choose)
        self.folder_row.add_suffix(choose)
        self.folder_row.set_activatable_widget(choose)

        # Notes d'exemple
        if hasattr(Adw, "SwitchRow"):  # libadwaita >= 1.4
            self.samples_row = Adw.SwitchRow(title=_("Créer des notes d'exemple"))
            self._samples_switch = self.samples_row
        else:
            self.samples_row = Adw.ActionRow(title=_("Créer des notes d'exemple"))
            self._samples_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            self.samples_row.add_suffix(self._samples_switch)
            self.samples_row.set_activatable_widget(self._samples_switch)
        self.samples_row.set_subtitle(_("Trois notes pour découvrir Lumina"))

        group = Adw.PreferencesGroup()
        group.add(self.folder_row)
        group.add(self.samples_row)

        start = Gtk.Button(label=_("Commencer"), halign=Gtk.Align.CENTER)
        start.add_css_class("suggested-action")
        start.add_css_class("pill")
        start.connect("clicked", self._on_start)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        box.append(group)
        box.append(start)

        status = Adw.StatusPage(
            icon_name="folder-documents-symbolic",
            title=_("Où ranger vos notes ?"),   # « Bienvenue » : dialogue d'accueil (welcome.py)
            description=_("Des notes simples, enregistrées en Markdown dans le dossier "
                          "de votre choix. Vous pourrez le changer plus tard dans les préférences."),
            child=Adw.Clamp(maximum_size=440, child=box),
            vexpand=True)

        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_title_widget(Gtk.Label())  # pas de titre : la page parle d'elle-même

        self.toast_overlay = Adw.ToastOverlay(child=status)
        view = Adw.ToolbarView(content=self.toast_overlay)
        view.add_top_bar(header)
        self.set_content(view)
        self.set_default_widget(start)
        self._set_folder(default_dir)

    def _set_folder(self, folder: Path):
        self._folder = folder
        self.folder_row.set_subtitle(str(folder))
        # Les exemples sont proposés d'office seulement pour un dossier vide.
        self._samples_switch.set_active(not _has_visible_content(folder))

    def _on_choose(self, _button):
        choose_folder(self, _("Dossier des notes"), self._folder, self._set_folder)

    def _on_start(self, _button):
        error = self._on_done(self._folder, self._samples_switch.get_active())
        if error:
            self.toast_overlay.add_toast(Adw.Toast.new(error))
