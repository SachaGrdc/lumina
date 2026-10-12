"""
line_numbers.py — gouttière de numéros de ligne pour un Gtk.TextView.

Un Gtk.TextView n'affiche pas de numéros de ligne (contrairement à GtkSourceView,
qu'on évite d'ajouter comme dépendance) : ce widget se place dans la marge gauche
via `text_view.set_gutter(Gtk.TextWindowType.LEFT, gutter)` et dessine le numéro
de chaque ligne logique en face de sa première ligne affichée.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

PADDING = 12           # marge horizontale autour des numéros (px)
REDRAW_DELAY_MS = 60   # le TextView recalcule sa mise en page en différé


class LineNumberGutter(Gtk.Widget):
    __gtype_name__ = "LuminaLineNumberGutter"

    def __init__(self, view: Gtk.TextView):
        super().__init__()
        self.add_css_class("lumina-gutter")
        self._view = view
        self._digits = 1
        self._redraw_source = 0
        view.get_buffer().connect("changed", self._on_buffer_changed)

    # ------------------------------------------------------------------ redessin

    def _on_buffer_changed(self, buffer: Gtk.TextBuffer):
        digits = len(str(buffer.get_line_count()))
        if digits != self._digits:
            self._digits = digits
            self.queue_resize()
        self._schedule_redraw()

    def _schedule_redraw(self):
        self.queue_draw()
        if not self._redraw_source:  # second passage, une fois la mise en page validée
            self._redraw_source = GLib.timeout_add(REDRAW_DELAY_MS, self._on_redraw_timeout)

    def _on_redraw_timeout(self):
        self._redraw_source = 0
        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def do_css_changed(self, change):  # taille de police modifiée
        Gtk.Widget.do_css_changed(self, change)
        self.queue_resize()
        self._schedule_redraw()

    def do_size_allocate(self, width, height, baseline):  # texte re-coupé (largeur changée)
        self._schedule_redraw()

    def do_unrealize(self):
        if self._redraw_source:
            GLib.source_remove(self._redraw_source)
            self._redraw_source = 0
        Gtk.Widget.do_unrealize(self)

    # ------------------------------------------------------------------ GtkWidget

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            width, _h = self.create_pango_layout("0" * max(2, self._digits)).get_pixel_size()
            width += 2 * PADDING
            return width, width, -1, -1
        return 0, 0, -1, -1

    def do_snapshot(self, snapshot):
        view = self._view
        color = (self.get_color() if hasattr(self, "get_color")  # GTK >= 4.10
                 else self.get_style_context().get_color())
        width = self.get_width()
        layout = self.create_pango_layout("")
        point = Graphene.Point()

        # Seules les lignes visibles sont dessinées : coût constant, même pour une longue note.
        visible = view.get_visible_rect()
        it, _top = view.get_line_at_y(visible.y)
        bottom = visible.y + visible.height
        while True:
            y, _height = view.get_line_yrange(it)
            if y > bottom:
                break
            _x, wy = view.buffer_to_window_coords(Gtk.TextWindowType.LEFT, 0, y)
            layout.set_text(str(it.get_line() + 1), -1)
            text_width, _text_height = layout.get_pixel_size()
            point.init(width - PADDING - text_width, wy)
            snapshot.save()
            snapshot.translate(point)
            snapshot.append_layout(layout, color)
            snapshot.restore()
            if not it.forward_line():
                break
