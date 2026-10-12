#!/usr/bin/env python3
"""
Lumina — prise de notes ultra-minimaliste (GTK4 + Libadwaita).

Point d'entrée : crée l'application, applique les préférences, déclare les
raccourcis et choisit la première fenêtre (accueil ou fenêtre principale).

  main.py          application, préférences, raccourcis clavier  (ce fichier)
  window.py        fenêtre principale, assemble les modules ci-dessous
  onboarding.py    écran d'accueil du premier lancement
  preferences.py   fenêtre de préférences
  tabs.py          onglets de l'éditeur (Adw.TabView / Adw.TabBar)
  editor.py        contenu d'un onglet : éditeur, sauvegarde automatique
  links.py         liens cliquables (Ctrl+clic)
  printing.py      impression / export PDF (Gtk.PrintOperation)
  dnd.py           glisser-déposer dans la barre latérale, import de fichiers
  pinned.py        section « Épinglées »
  trash_view.py    page « Corbeille »
  context_menu.py  menus contextuels (clic droit)
  dialogs.py       dialogues (renommer, confirmer, choisir un dossier)
  line_numbers.py  numéros de ligne de l'éditeur
  styles.py        feuille de style CSS, thème, taille du texte
  storage.py       fichiers, dossiers, corbeille   — logique métier, sans GTK
  pins.py          notes épinglées                 — logique métier, sans GTK
  config.py        config.json                     — logique métier, sans GTK
  i18n.py          traductions (français / anglais) — sans GTK
  samples.py       notes d'exemple                  — sans GTK

Dépendances :
  Debian/Ubuntu : sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
  Fedora        : sudo dnf install python3-gobject gtk4 libadwaita
  Windows       : MSYS2 UCRT64 -> pacman -S mingw-w64-ucrt-x86_64-{gtk4,libadwaita,python-gobject}
"""

import sys
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

import i18n  # noqa: E402
from config import Config  # noqa: E402
from i18n import _  # noqa: E402
from onboarding import OnboardingWindow  # noqa: E402
from preferences import PreferencesWindow  # noqa: E402
from samples import samples_for  # noqa: E402
from storage import NoteStore, StorageError  # noqa: E402
from styles import apply_theme, install_css, set_editor_font_size  # noqa: E402
from window import LuminaWindow  # noqa: E402

# Doit correspondre au nom du fichier .desktop et de l'icône : c'est ainsi que GNOME
# associe la fenêtre à son lanceur (dock, Alt+Tab).
APP_ID = "com.github.lumina"

# Raccourcis globaux. « Suppr » n'y figure pas : il agit seulement dans la barre
# latérale (voir window.py) pour ne pas effacer de texte dans l'éditeur.
# Ctrl+\ demande AltGr+8 sur un clavier AZERTY : Ctrl+B et F9 servent d'alternatives.
ACCELERATORS = {
    "win.new-note": ["<Primary>n"],
    "win.new-folder": ["<Primary><Shift>n"],
    "win.focus-search": ["<Primary>f"],
    "win.toggle-sidebar": ["<Primary>backslash", "<Primary>b", "F9"],
    "win.rename": ["F2"],
    "win.save": ["<Primary>s"],   # facultatif : la sauvegarde est automatique
    "win.new-tab": ["<Primary>t"],
    "win.close-tab": ["<Primary>w"],
    "win.print": ["<Primary>p"],
    "app.preferences": ["<Primary>comma"],
}


class LuminaApp(Adw.Application):
    def __init__(self):
        # HANDLES_OPEN : « Ouvrir avec Lumina » (gestionnaire de fichiers, fichier .desktop).
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.config = Config()
        self.first_run = False
        self._prefs: PreferencesWindow | None = None
        self._pending_files: list[Path] = []   # reçus pendant l'écran d'accueil

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.config, self.first_run = Config.load()
        i18n.set_language(self.config.language)

        Gtk.Window.set_default_icon_name(APP_ID)
        install_css()
        apply_theme(self.config.theme)
        set_editor_font_size(self.config.font_size)

        action = Gio.SimpleAction.new("preferences", None)
        action.connect("activate", lambda *_a: self.show_preferences())
        self.add_action(action)
        for name, accels in ACCELERATORS.items():
            self.set_accels_for_action(name, accels)

    def do_activate(self):
        win = self.props.active_window
        if win is not None:
            win.present()
        elif self.first_run:
            self._show_onboarding()
        else:
            self._open_main()

    def do_open(self, files, _n_files, _hint):
        """Fichiers passés en argument (Ouvrir avec…) : ouverts chacun dans un onglet."""
        paths = [Path(f.get_path()) for f in files if f.get_path() is not None]
        win = self.main_window()
        if win is None and self.first_run:  # ouverts une fois l'accueil terminé
            self._pending_files.extend(paths)
            self.activate()
            return
        if win is None:
            win = self._open_main()
            if win is None:
                return
        win.open_files(paths)
        win.present()

    # ================================================================== Fenêtres

    def main_window(self) -> LuminaWindow | None:
        for win in self.get_windows():
            if isinstance(win, LuminaWindow):
                return win
        return None

    def _open_main(self, state: dict | None = None) -> LuminaWindow | None:
        try:
            store = NoteStore(self.config.notes_dir)
        except StorageError as e:  # dossier inaccessible : on repasse par l'accueil
            self._show_onboarding(str(e))
            return None
        win = LuminaWindow(self, store)
        win.set_line_numbers(self.config.line_numbers)
        if state is not None:
            win.restore_state(state)
        else:  # onglets de la session précédente (curseur compris), ou une page blanche
            win.restore_tabs(self.config.tabs, self.config.active_tab)
        win.present()
        return win

    def _show_onboarding(self, error: str | None = None):
        win = OnboardingWindow(self, Path(self.config.notes_dir).expanduser(),
                               on_done=self._finish_onboarding)
        win.present()
        if error:
            win.toast_overlay.add_toast(Adw.Toast.new(error))

    def _finish_onboarding(self, folder: Path, create_samples: bool) -> str | None:
        """Appelé par « Commencer » ; retourne un message d'erreur, ou None si tout va bien."""
        try:
            store = NoteStore(folder)
            if create_samples:
                store.create_samples(samples_for(i18n.current_language()))
        except StorageError as e:
            return str(e)
        self.config.notes_dir = str(store.root)
        try:
            self.config.save()
        except OSError as e:
            return _("Impossible d'enregistrer la configuration : {error}").format(error=e)
        self.first_run = False

        onboarding = self.props.active_window
        win = self._open_main()  # avant de fermer l'accueil : l'application resterait sans fenêtre
        if win is not None and self._pending_files:
            win.open_files(self._pending_files)
        self._pending_files = []
        if isinstance(onboarding, OnboardingWindow):
            onboarding.destroy()
        return None

    def _rebuild_main_window(self, reopen_prefs: bool):
        """Recrée la fenêtre principale (nouvelle langue ou nouveau dossier de notes)."""
        if self._prefs is not None:
            self._prefs.destroy()
            self._prefs = None
        old = self.main_window()
        state = old.export_state() if old is not None else None
        if state is not None and old.store.root != Path(self.config.notes_dir).expanduser().resolve():
            state["tabs"] = []  # les notes ouvertes n'appartiennent pas au nouveau dossier
        new = self._open_main(state)
        if old is not None:
            old.destroy()
        if new is not None and reopen_prefs:
            self.show_preferences()
        return GLib.SOURCE_REMOVE

    # ================================================================== Préférences

    def show_preferences(self):
        parent = self.main_window()
        if parent is None:  # pas encore de fenêtre principale (accueil)
            return
        if self._prefs is None:
            self._prefs = PreferencesWindow(parent, self.config, self.update_setting)
            self._prefs.connect("close-request", self._on_prefs_closed)
        self._prefs.present()

    def _on_prefs_closed(self, window):
        if window is self._prefs:
            self._prefs = None
        return False

    def save_session(self, tabs: list[dict], active: int):
        """Onglets ouverts à la fermeture de la fenêtre (rouverts au prochain lancement)."""
        self.config.tabs, self.config.active_tab = tabs, active
        try:
            self.config.save()
        except OSError:
            pass  # la fenêtre se ferme : rien à afficher, la session est simplement perdue

    def update_setting(self, key: str, value: Any):
        """Enregistre une préférence dans config.json puis l'applique."""
        if getattr(self.config, key) == value:
            return
        setattr(self.config, key, value)
        try:
            self.config.save()
        except OSError as e:
            win = self.main_window()
            if win is not None:
                win.toast(_("Impossible d'enregistrer la configuration : {error}").format(error=e))

        if key == "theme":
            apply_theme(value)
        elif key == "font_size":
            set_editor_font_size(value)
        elif key == "line_numbers":
            for win in self.get_windows():
                if isinstance(win, LuminaWindow):
                    win.set_line_numbers(value)
        elif key in ("language", "notes_dir"):
            if key == "language":
                i18n.set_language(value)
            # Différé : on ne détruit pas la fenêtre de préférences pendant son propre signal.
            GLib.idle_add(self._rebuild_main_window, True)


def main() -> int:
    # Sinon le nom du programme serait « main.py » (classe de fenêtre X11, gestion des sessions).
    GLib.set_prgname(APP_ID)
    GLib.set_application_name("Lumina")
    return LuminaApp().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
