"""
printing.py — impression et export PDF d'une note (Gtk.PrintOperation).

Le dialogue d'impression natif propose aussi « Imprimer dans un fichier »
(PDF), nommé d'après la note. Mise en page Pango : titre en gras, texte brut
avec retour à la ligne automatique, numéro de page en pied de page.

Dépendance : le pont Cairo de PyGObject (Debian/Ubuntu : python3-gi-cairo).
"""

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo  # noqa: E402

from i18n import _  # noqa: E402

BODY_FONT = "Sans 11"
FOOTER_FONT = "Sans 8"
FOOTER_HEIGHT = 24       # espace réservé au numéro de page (unités du contexte d'impression)


class _Pagination:
    """Mise en page calculée dans « begin-print » et utilisée par « draw-page »."""

    def __init__(self):
        self.layout: Pango.Layout | None = None    # gardé en vie : les lignes y font référence
        self.pages: list[list[tuple[Pango.LayoutLine, float]]] = []   # (ligne, ligne de base)


def print_note(parent: Gtk.Window, title: str, text: str,
               on_error: Callable[[str], None]) -> None:
    """Ouvre le dialogue d'impression natif pour la note `title` / `text`."""
    op = Gtk.PrintOperation(job_name=title, embed_page_setup=True, allow_async=True)
    settings = Gtk.PrintSettings()
    settings.set(Gtk.PRINT_SETTINGS_OUTPUT_BASENAME, title)   # nom proposé pour le PDF
    op.set_print_settings(settings)

    pagination = _Pagination()
    op.connect("begin-print", _on_begin_print, pagination, title, text)
    op.connect("draw-page", _on_draw_page, pagination)

    def on_done(operation, result):
        parent._lumina_print = None
        if result == Gtk.PrintOperationResult.ERROR:
            try:
                operation.get_error()
            except GLib.Error as e:
                on_error(_("Impression impossible : {error}").format(error=e.message))

    op.connect("done", on_done)
    parent._lumina_print = op  # garde une référence tant que l'impression est en cours
    try:
        op.run(Gtk.PrintOperationAction.PRINT_DIALOG, parent)
    except GLib.Error as e:
        parent._lumina_print = None
        on_error(_("Impression impossible : {error}").format(error=e.message))


def _on_begin_print(op: Gtk.PrintOperation, context: Gtk.PrintContext,
                    pagination: _Pagination, title: str, text: str):
    layout = context.create_pango_layout()
    layout.set_font_description(Pango.FontDescription.from_string(BODY_FONT))
    layout.set_width(int(context.get_width() * Pango.SCALE))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_markup(f"<span weight='bold' size='x-large'>{GLib.markup_escape_text(title)}</span>"
                      f"\n\n{GLib.markup_escape_text(text)}", -1)

    # Découpage en pages, ligne par ligne : une ligne n'est jamais coupée entre deux pages.
    page_height = context.get_height() - FOOTER_HEIGHT
    pages: list[list[tuple[Pango.LayoutLine, float]]] = [[]]
    page_top = 0.0
    it = layout.get_iter()
    while True:
        _ink, logical = it.get_line_extents()
        top = logical.y / Pango.SCALE
        bottom = top + logical.height / Pango.SCALE
        if bottom - page_top > page_height and pages[-1]:
            pages.append([])
            page_top = top
        pages[-1].append((it.get_line_readonly(), it.get_baseline() / Pango.SCALE - page_top))
        if not it.next_line():
            break

    pagination.layout = layout
    pagination.pages = pages
    op.set_n_pages(len(pages))


def _on_draw_page(_op: Gtk.PrintOperation, context: Gtk.PrintContext, page_nr: int,
                  pagination: _Pagination):
    cr = context.get_cairo_context()
    cr.set_source_rgb(0, 0, 0)
    for line, baseline in pagination.pages[page_nr]:
        cr.move_to(0, baseline)
        PangoCairo.show_layout_line(cr, line)

    footer = context.create_pango_layout()
    footer.set_font_description(Pango.FontDescription.from_string(FOOTER_FONT))
    footer.set_text(f"{page_nr + 1} / {len(pagination.pages)}", -1)
    width, height = footer.get_pixel_size()
    cr.move_to((context.get_width() - width) / 2, context.get_height() - height)
    PangoCairo.show_layout(cr, footer)
