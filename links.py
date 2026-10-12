"""
links.py — liens hypertexte cliquables dans l'éditeur.

Les URL http(s) du texte sont soulignées et colorées (couleur d'accent) par un
Gtk.TextTag : rien n'est écrit dans la note, qui reste du texte brut.
Ctrl+clic ouvre le lien dans le navigateur par défaut ; au survol, avec Ctrl
enfoncé, le curseur devient une main.

`find_urls()` ne dépend pas de GTK.
"""

import re
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from i18n import _  # noqa: E402

URL_RE = re.compile(r"https?://[^\s<>\"'`]+")
RESCAN_DELAY_MS = 300                     # la détection attend une pause dans la frappe
_TRAILING = ".,;:!?*_'\""                 # ponctuation de fin de phrase, hors du lien
_CLOSING = {")": "(", "]": "["}


def find_urls(text: str) -> list[tuple[int, int]]:
    """Positions (début, fin) des URL de `text`, sans la ponctuation qui les suit."""
    spans = []
    for match in URL_RE.finditer(text):
        start, end = match.span()
        while end > start:
            last = text[end - 1]
            url = text[start:end]
            if last in _TRAILING:
                end -= 1
            # Parenthèse fermante sans ouvrante : [titre](https://…) en Markdown.
            elif last in _CLOSING and url.count(last) > url.count(_CLOSING[last]):
                end -= 1
            else:
                break
        if end - start > len("https://"):
            spans.append((start, end))
    return spans


class LinkSupport:
    """Détection, mise en forme et ouverture des liens d'un Gtk.TextView."""

    def __init__(self, view: Gtk.TextView, on_error: Callable[[str], None]):
        self._view = view
        self._buffer = view.get_buffer()
        self._on_error = on_error
        self._tag = self._buffer.create_tag("lumina-link", underline=Pango.Underline.SINGLE)
        self._rescan_source = 0
        self._pointer = False
        self._buffer.connect("changed", lambda _b: self._schedule())

        # Phase CAPTURE : le Ctrl+clic est intercepté avant que le TextView ne déplace le curseur.
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY,
                                 propagation_phase=Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_pressed)
        view.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        view.add_controller(motion)

        view.set_has_tooltip(True)
        view.connect("query-tooltip", self._on_query_tooltip)

        # Couleur d'accent (libadwaita >= 1.6) ; sinon le lien est seulement souligné.
        self._style = Adw.StyleManager.get_default()
        self._style_handlers = []
        if hasattr(Adw, "accent_color_to_standalone_rgba"):
            for prop in ("dark", "accent-color"):
                self._style_handlers.append(
                    self._style.connect(f"notify::{prop}", lambda *_a: self._update_color()))
            self._update_color()

    # ------------------------------------------------------------------ API

    def refresh(self) -> None:
        """Re-détecte immédiatement les liens de tout le tampon."""
        if self._rescan_source:
            GLib.source_remove(self._rescan_source)
            self._rescan_source = 0
        buffer = self._buffer
        start, end = buffer.get_bounds()
        buffer.remove_tag(self._tag, start, end)
        for a, b in find_urls(buffer.get_text(start, end, False)):
            buffer.apply_tag(self._tag, buffer.get_iter_at_offset(a), buffer.get_iter_at_offset(b))

    def close(self) -> None:
        """À appeler quand l'éditeur disparaît (onglet fermé)."""
        if self._rescan_source:
            GLib.source_remove(self._rescan_source)
            self._rescan_source = 0
        for handler in self._style_handlers:
            self._style.disconnect(handler)
        self._style_handlers = []

    # ------------------------------------------------------------------ interne

    def _schedule(self):
        if self._rescan_source:
            GLib.source_remove(self._rescan_source)
        self._rescan_source = GLib.timeout_add(RESCAN_DELAY_MS, self._on_rescan_timeout)

    def _on_rescan_timeout(self):
        self._rescan_source = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _update_color(self):
        rgba = Adw.accent_color_to_standalone_rgba(self._style.get_accent_color(),
                                                   self._style.get_dark())
        self._tag.set_property("foreground-rgba", rgba)

    def _url_at(self, x: float, y: float) -> str | None:
        """URL sous le point (x, y) en coordonnées du widget, ou None."""
        view = self._view
        bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        found, it = view.get_iter_at_location(bx, by)
        if not found or not it.has_tag(self._tag):
            return None
        start = it.copy()
        if not start.starts_tag(self._tag):
            start.backward_to_tag_toggle(self._tag)
        end = it.copy()
        end.forward_to_tag_toggle(self._tag)
        return self._buffer.get_text(start, end, False)

    @staticmethod
    def _ctrl_held(controller: Gtk.EventController) -> bool:
        return bool(controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK)

    def _on_pressed(self, gesture, _n_press, x, y):
        if not self._ctrl_held(gesture):
            return
        url = self._url_at(x, y)
        if url is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self._open(url)

    def _on_motion(self, controller, x, y):
        pointer = self._ctrl_held(controller) and self._url_at(x, y) is not None
        if pointer != self._pointer:
            self._pointer = pointer
            self._view.set_cursor_from_name("pointer" if pointer else "text")

    def _on_query_tooltip(self, _view, x, y, keyboard_mode, tooltip):
        if keyboard_mode or self._url_at(x, y) is None:
            return False
        tooltip.set_text(_("Ctrl+clic pour ouvrir le lien"))
        return True

    def _open(self, url: str):
        parent = self._view.get_root()
        if not hasattr(Gtk, "UriLauncher"):  # GTK < 4.10
            Gtk.show_uri(parent, url, Gdk.CURRENT_TIME)
            return

        def on_done(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error as e:
                self._on_error(_("Impossible d'ouvrir le lien : {error}").format(error=e.message))

        Gtk.UriLauncher(uri=url).launch(parent, None, on_done)
