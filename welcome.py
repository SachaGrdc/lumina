"""
welcome.py — dialogue d'accueil, affiché une seule fois (fanion `welcome_shown`
de config.json, géré par main.py).

Présente Lumina et ses fonctionnalités clés ; « Commencer » le ferme. Tout autre
moyen de le fermer (Échap, bouton de fermeture) compte aussi : `on_closed` est
appelé dans tous les cas, une seule fois.

Adw.Dialog (libadwaita >= 1.5), sinon une fenêtre modale Adw.Window.
"""

from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from i18n import _  # noqa: E402

APP_ICON = "com.github.lumina"         # installée par install.sh
FALLBACK_ICON = "document-edit-symbolic"
CONTENT_WIDTH = 460

# (icône symbolique, titre, description)
FEATURES = (
    ("folder-symbolic", "Organisation intuitive",
     "Arborescence de dossiers, corbeille et glisser-déposer."),
    ("tab-new-symbolic", "Navigation par onglets",
     "Ouvrez plusieurs notes simultanément (Ctrl + T)."),
    ("document-save-symbolic", "Sauvegarde automatique",
     "Vos modifications sont enregistrées en temps réel."),
    ("system-search-symbolic", "Recherche globale",
     "Retrouvez vos idées instantanément dans toutes vos notes."),
)


def show_welcome(parent: Gtk.Window, on_closed: Callable[[], None]) -> Gtk.Widget:
    """Affiche le dialogue d'accueil au-dessus de `parent` ; retourne le dialogue."""
    start = Gtk.Button(label=_("Commencer"), halign=Gtk.Align.CENTER)
    start.add_css_class("suggested-action")
    start.add_css_class("pill")
    content = _build_content(parent, start)

    header = Adw.HeaderBar()
    header.add_css_class("flat")
    header.set_title_widget(Gtk.Label())   # pas de titre : la page parle d'elle-même
    view = Adw.ToolbarView(content=content)
    view.add_top_bar(header)

    closed = False

    def notify_closed(*_args):
        nonlocal closed
        if not closed:
            closed = True
            on_closed()

    if hasattr(Adw, "Dialog"):   # libadwaita >= 1.5
        dialog = Adw.Dialog(child=view, content_width=CONTENT_WIDTH,
                            title=_("Bienvenue dans Lumina"))
        dialog.set_default_widget(start)
        dialog.connect("closed", notify_closed)
        start.connect("clicked", lambda _b: dialog.close())
        dialog.present(parent)
        return dialog

    window = Adw.Window(content=view, modal=True, transient_for=parent,
                        default_width=CONTENT_WIDTH, title=_("Bienvenue dans Lumina"))
    window.set_default_widget(start)
    window.connect("close-request", lambda _w: (notify_closed(), False)[1])
    start.connect("clicked", lambda _b: window.close())
    window.present()
    return window


def _build_content(parent: Gtk.Window, start: Gtk.Button) -> Gtk.Widget:
    features = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    features.add_css_class("boxed-list")
    for icon, title, description in FEATURES:
        row = Adw.ActionRow(title=_(title), subtitle=_(description), activatable=False)
        row.add_prefix(Gtk.Image(icon_name=icon, valign=Gtk.Align.CENTER))
        features.append(row)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
    box.append(features)
    box.append(start)

    theme = Gtk.IconTheme.get_for_display(parent.get_display())
    status = Adw.StatusPage(
        icon_name=APP_ICON if theme.has_icon(APP_ICON) else FALLBACK_ICON,
        title=_("Bienvenue dans Lumina"),
        description=_("Votre éditeur de notes Markdown fluide, minimaliste et pensé pour GNOME."),
        child=Adw.Clamp(maximum_size=CONTENT_WIDTH - 60, child=box))
    status.add_css_class("compact")
    return Gtk.ScrolledWindow(child=status, hscrollbar_policy=Gtk.PolicyType.NEVER,
                              propagate_natural_height=True)
