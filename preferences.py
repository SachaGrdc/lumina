"""
preferences.py — fenêtre de préférences (Adw.PreferencesWindow).

Sections : Général (langue) · Stockage (dossier des notes) · Éditeur et apparence
(thème, taille du texte, numéros de ligne).

La fenêtre ne fait qu'afficher la configuration : chaque modification est
transmise à `on_change(clé, valeur)` (voir LuminaApp.update_setting dans main.py),
qui l'enregistre et l'applique.
"""

from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

import i18n  # noqa: E402
from config import FONT_SIZE_RANGE, LANGUAGES, THEMES, Config  # noqa: E402
from dialogs import choose_folder  # noqa: E402
from i18n import _  # noqa: E402


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, parent: Gtk.Window, config: Config,
                 on_change: Callable[[str, Any], None]):
        super().__init__(transient_for=parent, modal=True, title=_("Préférences"),
                         search_enabled=False, default_width=560, default_height=620)
        self._config = config
        self._on_change = on_change

        page = Adw.PreferencesPage(title=_("Préférences"), icon_name="preferences-system-symbolic")
        page.add(self._build_general())
        page.add(self._build_storage())
        page.add(self._build_editor())
        self.add(page)

    # ================================================================== Sections

    def _build_general(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Général"))
        system = i18n.LANGUAGE_NAMES[i18n.system_language()]
        labels = [_("Langue du système ({language})").format(language=system),
                  i18n.LANGUAGE_NAMES["fr"], i18n.LANGUAGE_NAMES["en"]]
        group.add(self._combo_row(_("Langue"), None, labels, LANGUAGES, "language"))
        return group

    def _build_storage(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title=_("Stockage"),
            description=_("Lumina affiche les notes de ce dossier. "
                          "Changer de dossier ne déplace pas les notes existantes."))
        row = Adw.ActionRow(title=_("Dossier des notes"), subtitle=self._config.notes_dir)
        row.add_css_class("property")
        button = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=_("Choisir un autre dossier…"))
        button.add_css_class("flat")
        button.connect("clicked", self._on_choose_folder)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        group.add(row)
        return group

    def _build_editor(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=_("Éditeur et apparence"))
        group.add(self._combo_row(_("Thème"), None,
                                  [_("Suivre le système"), _("Sombre"), _("Clair")],
                                  THEMES, "theme"))
        group.add(self._spin_row(_("Taille du texte"), _("En points"), "font_size",
                                 *FONT_SIZE_RANGE))
        group.add(self._switch_row(_("Numéros de ligne"),
                                   _("Affiche le numéro de chaque ligne dans la marge"),
                                   "line_numbers"))
        return group

    # ================================================================== Lignes génériques

    def _combo_row(self, title: str, subtitle: str | None, labels: list[str],
                   values: tuple[str, ...], key: str) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
        if subtitle:
            row.set_subtitle(subtitle)
        current = getattr(self._config, key)
        row.set_selected(values.index(current) if current in values else 0)
        row.connect("notify::selected",
                    lambda r, _p: self._on_change(key, values[r.get_selected()]))
        return row

    def _spin_row(self, title: str, subtitle: str, key: str, low: int, high: int):
        value = getattr(self._config, key)
        if hasattr(Adw, "SpinRow"):  # libadwaita >= 1.4
            row = Adw.SpinRow.new_with_range(low, high, 1)
            row.set_title(title)
            row.set_subtitle(subtitle)
            row.set_value(value)
            row.connect("notify::value", lambda r, _p: self._on_change(key, int(r.get_value())))
            return row
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        spin = Gtk.SpinButton.new_with_range(low, high, 1)
        spin.set_valign(Gtk.Align.CENTER)
        spin.set_value(value)
        spin.connect("value-changed", lambda s: self._on_change(key, int(s.get_value())))
        row.add_suffix(spin)
        row.set_activatable_widget(spin)
        return row

    def _switch_row(self, title: str, subtitle: str, key: str):
        active = getattr(self._config, key)
        if hasattr(Adw, "SwitchRow"):  # libadwaita >= 1.4
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=active)
            row.connect("notify::active", lambda r, _p: self._on_change(key, r.get_active()))
            return row
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        switch = Gtk.Switch(active=active, valign=Gtk.Align.CENTER)
        switch.connect("notify::active", lambda s, _p: self._on_change(key, s.get_active()))
        row.add_suffix(switch)
        row.set_activatable_widget(switch)
        return row

    # ================================================================== Dossier des notes

    def _on_choose_folder(self, _button):
        current = Path(self._config.notes_dir).expanduser()
        choose_folder(self, _("Dossier des notes"), current, self._on_folder_chosen)

    def _on_folder_chosen(self, folder: Path):
        folder = folder.expanduser().resolve()
        if folder != Path(self._config.notes_dir).expanduser().resolve():
            self._on_change("notes_dir", str(folder))
