"""
editor.py — page d'édition d'une note (le contenu d'un onglet).

Chaque onglet possède son propre tampon : curseur, défilement, historique
d'annulation et sauvegarde automatique sont indépendants d'un onglet à l'autre.
Une page sans fichier (`path is None`) est un brouillon : sa note n'est créée
sur le disque qu'à la première frappe, par la fenêtre (`on_materialize`).
"""

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from i18n import _  # noqa: E402
from line_numbers import LineNumberGutter  # noqa: E402
from links import LinkSupport  # noqa: E402
from storage import StorageError, StorageManager  # noqa: E402

SAVE_DELAY_MS = 1000     # debouncing de la sauvegarde automatique
EDITOR_MARGIN = 40       # marges de l'éditeur (la gauche est réduite quand les numéros s'affichent)


class NotePage(Gtk.ScrolledWindow):
    __gtype_name__ = "LuminaNotePage"

    def __init__(self, store: StorageManager,
                 on_materialize: Callable[["NotePage"], bool],
                 on_error: Callable[[str], None]):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.store = store
        self.path: Path | None = None          # None : brouillon, pas encore de fichier
        self.draft_parent: Path = store.root   # dossier où le brouillon sera créé
        self._on_materialize = on_materialize
        self._on_error = on_error
        self._loading = False       # vrai pendant le chargement d'une note
        self._dirty = False         # modifications non enregistrées
        self._save_source = 0       # id du timeout de sauvegarde en attente

        # TextView centré, sans bordure, avec de larges marges symétriques.
        self.buffer = Gtk.TextBuffer()
        self.buffer.connect("changed", self._on_buffer_changed)
        self.text_view = Gtk.TextView(buffer=self.buffer, vexpand=True,
                                      wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                      top_margin=EDITOR_MARGIN, bottom_margin=EDITOR_MARGIN,
                                      left_margin=EDITOR_MARGIN, right_margin=EDITOR_MARGIN,
                                      pixels_inside_wrap=4, pixels_below_lines=2)
        self.text_view.add_css_class("lumina-editor")
        self.gutter = LineNumberGutter(self.text_view)
        self.links = LinkSupport(self.text_view, on_error)
        self.set_child(Adw.Clamp(maximum_size=760, tightening_threshold=600,
                                 vexpand=True, child=self.text_view))

    # ------------------------------------------------------------------ état

    @property
    def title(self) -> str:
        return self.path.stem if self.path is not None else _("Sans titre")

    def text(self) -> str:
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    def is_under(self, path: Path) -> bool:
        """Vrai si la note ouverte est `path` ou se trouve dans le dossier `path`."""
        return self.path is not None and (self.path == path or path in self.path.parents)

    def remap(self, old: Path, new: Path) -> bool:
        """Suit un renommage / déplacement de `old` vers `new` ; retourne True si la page est concernée."""
        current = self.path if self.path is not None else self.draft_parent
        try:
            relative = current.relative_to(old)
        except ValueError:
            return False
        if self.path is not None:
            self.path = new / relative
        else:
            self.draft_parent = new / relative
        return True

    def cursor_offset(self) -> int:
        return self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()

    def restore_cursor(self, offset: int) -> None:
        self.buffer.place_cursor(self.buffer.get_iter_at_offset(offset))
        # scroll_to_mark (et non scroll_to_iter) attend que la mise en page soit calculée.
        self.text_view.scroll_to_mark(self.buffer.get_insert(), 0.1, False, 0, 0)

    def set_line_numbers(self, enabled: bool) -> None:
        self.text_view.set_gutter(Gtk.TextWindowType.LEFT, self.gutter if enabled else None)
        self.text_view.set_left_margin(12 if enabled else EDITOR_MARGIN)

    # ------------------------------------------------------------------ contenu

    def load(self, path: Path) -> bool:
        """Ouvre `path` dans cette page (la note précédente est d'abord enregistrée)."""
        self.flush()
        try:
            text = self.store.read_note(path)
        except StorageError as e:
            self._on_error(str(e))
            return False
        self._set_text(text)
        self.path = path
        return True

    def start_draft(self, parent: Path) -> None:
        """Page blanche prête à être tapée ; le fichier n'est créé qu'à la première frappe
        (aucune note vide ne s'accumule si l'on ne tape rien)."""
        self.flush()
        self._set_text("")
        self.path = None
        self.draft_parent = parent

    def close(self) -> None:
        """Onglet fermé : enregistre et arrête les minuteurs."""
        self.flush()
        self.links.close()

    def _set_text(self, text: str):
        self._loading = True
        self.buffer.begin_irreversible_action()  # Ctrl+Z ne doit pas « décharger » la note
        self.buffer.set_text(text)
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.buffer.end_irreversible_action()
        self._loading = False
        self._dirty = False
        self.links.refresh()

    # ------------------------------------------------------------------ sauvegarde automatique

    def _on_buffer_changed(self, _buffer):
        if self._loading:
            return
        if self.path is None and not self._on_materialize(self):
            return
        self._dirty = True
        # Chaque frappe relance le minuteur : on n'écrit qu'après 1 s d'inactivité.
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(SAVE_DELAY_MS, self._on_save_timeout)

    def _on_save_timeout(self):
        self._save_source = 0
        self.flush()
        return GLib.SOURCE_REMOVE

    def flush(self) -> None:
        """Enregistre immédiatement si nécessaire (changement de note, fermeture…)."""
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_source = 0
        if not self._dirty or self.path is None:
            return
        try:
            self.store.write_note(self.path, self.text())
        except StorageError as e:
            self._on_error(str(e))
            return
        self._dirty = False
