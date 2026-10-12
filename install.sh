#!/bin/sh
# install.sh — intègre Lumina au bureau GNOME pour l'utilisateur courant (sans sudo).
#
#   ./install.sh               installe le lanceur (.desktop) et l'icône
#   ./install.sh --uninstall   les retire
#
# Lumina reste lancé depuis ce dossier : le lanceur pointe vers main.py ici même.
# Pour faire de Lumina l'application par défaut des fichiers Markdown :
#   xdg-mime default com.github.lumina.desktop text/markdown
set -eu

APP_ID=com.github.lumina
SRC_DIR=$(cd "$(dirname "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
APPS_DIR="$DATA_HOME/applications"
ICON_ROOT="$DATA_HOME/icons/hicolor"
ICON_DIR="$ICON_ROOT/scalable/apps"
DESKTOP="$APPS_DIR/$APP_ID.desktop"
ICON="$ICON_DIR/$APP_ID.svg"

refresh_caches() {
    # Associations type MIME -> application (mimeinfo.cache) et cache d'icônes.
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q "$APPS_DIR" || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -f "$ICON_ROOT/index.theme" ]; then
        gtk-update-icon-cache -q -t -f "$ICON_ROOT" || true
    fi
}

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$DESKTOP" "$ICON"
    refresh_caches
    echo "Lumina a été retiré du menu des applications."
    exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 est introuvable." >&2
    exit 1
fi

# Ces caractères ont un sens particulier dans une ligne Exec (spécification freedesktop).
case "$SRC_DIR" in
    *[\\\"\`\$%]*)
        echo "Le chemin « $SRC_DIR » contient un caractère non pris en charge (\\ \" \` \$ %)." >&2
        echo "Déplacez le dossier de Lumina puis relancez install.sh." >&2
        exit 1 ;;
esac

mkdir -p "$APPS_DIR" "$ICON_DIR"
install -m 644 "$SRC_DIR/data/$APP_ID.svg" "$ICON"

# Chemin absolu de main.py, entre guillemets (il peut contenir des espaces).
MAIN=$(printf '%s' "$SRC_DIR/main.py" | sed 's/[&|]/\\&/g')
sed "s|^Exec=lumina %F$|Exec=python3 \"$MAIN\" %F|" "$SRC_DIR/$APP_ID.desktop" > "$DESKTOP"
chmod 644 "$DESKTOP"

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$DESKTOP" || true
fi
refresh_caches

echo "Lumina est installé : $DESKTOP"
echo "Il apparaît dans le menu des applications (une reconnexion peut être nécessaire)."
