"""
styles.py — feuille de style CSS de Lumina et réglages d'apparence.

  install_css()             feuille fixe, installée au démarrage (voir main.py)
  set_editor_font_size(pt)  taille du texte de l'éditeur (préférences)
  apply_theme(theme)        "system" | "dark" | "light" via Adw.StyleManager
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk  # noqa: E402

CSS = """
textview.lumina-editor,
textview.lumina-editor text {
    background: transparent;
}

/* ---- Numéros de ligne (gouttière de l'éditeur) ---- */
textview.lumina-editor border {          /* marge du TextView qui accueille la gouttière */
    background: transparent;
}
.lumina-gutter {
    opacity: 0.45;
    font-feature-settings: "tnum";
}

/* ---- Glisser-déposer : retours visuels (couleurs d'accent Libadwaita) ----
   GTK donne l'état :drop(active) à tout widget dont la cible accepte le dépôt
   survolé, et le thème le dessine (cadre d'accent). Il est neutralisé sur nos
   cibles : le seul retour visuel est celui des classes ci-dessous, posées
   exactement sur l'élément visé. */
.lumina-row:drop(active),
.lumina-root-zone:drop(active),
.lumina-editor-zone:drop(active),
row:drop(active) {
    box-shadow: none;
    outline-color: transparent;   /* (pas « outline: none » : .lumina-drop-* rétablit la couleur) */
    background-image: none;
}
.lumina-row {
    border-radius: 8px;
    outline: 2px solid transparent;
    outline-offset: -2px;
    transition: background-color 150ms ease-out,
                outline-color 150ms ease-out,
                opacity 150ms ease-out;
}
.lumina-row.lumina-dragging {            /* l'élément en cours de déplacement */
    opacity: 0.4;
}
.lumina-row.lumina-drop-into {           /* dépôt DANS un dossier */
    background-color: alpha(@accent_bg_color, 0.18);
    outline-color: alpha(@accent_bg_color, 0.9);
}
.lumina-row.lumina-drop-group {          /* dépôt sur une note : création d'un dossier */
    background-color: alpha(@accent_bg_color, 0.32);
    outline-color: @accent_color;
}
.lumina-root-zone {
    transition: background-color 150ms ease-out, box-shadow 150ms ease-out;
}
.lumina-root-zone.lumina-drop-root {     /* dépôt à la racine */
    background-color: alpha(@accent_bg_color, 0.14);
    box-shadow: inset 0 0 0 2px alpha(@accent_bg_color, 0.8);
}
.lumina-editor-zone {
    transition: background-color 150ms ease-out, box-shadow 150ms ease-out;
}
.lumina-editor-zone.lumina-drop-open {   /* note déposée sur l'éditeur : nouvel onglet */
    background-color: alpha(@accent_bg_color, 0.06);
    box-shadow: inset 0 0 0 2px alpha(@accent_bg_color, 0.8);
}
"""

_THEMES = {
    "system": Adw.ColorScheme.DEFAULT,      # suit la préférence du système
    "dark": Adw.ColorScheme.FORCE_DARK,
    "light": Adw.ColorScheme.FORCE_LIGHT,
}

_font_provider: Gtk.CssProvider | None = None


def _load(provider: Gtk.CssProvider, css: str) -> None:
    if hasattr(provider, "load_from_string"):  # GTK >= 4.12
        provider.load_from_string(css)
    else:
        provider.load_from_data(css.encode())


def install_css() -> None:
    """Applique la feuille de style à tout l'écran (à appeler après le startup)."""
    provider = Gtk.CssProvider()
    _load(provider, CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def set_editor_font_size(points: int) -> None:
    """Taille du texte de l'éditeur et des numéros de ligne."""
    global _font_provider
    if _font_provider is None:
        _font_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _font_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
    _load(_font_provider,
          f"textview.lumina-editor, textview.lumina-editor text, .lumina-gutter "
          f"{{ font-size: {int(points)}pt; }}")


def apply_theme(theme: str) -> None:
    Adw.StyleManager.get_default().set_color_scheme(_THEMES.get(theme, Adw.ColorScheme.DEFAULT))
