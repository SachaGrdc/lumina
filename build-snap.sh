#!/bin/sh
# build-snap.sh — construit le paquet Snap de Lumina (snap/snapcraft.yaml).
#
#   ./build-snap.sh            construit lumina_<version>_<arch>.snap
#   ./build-snap.sh --clean    nettoie l'environnement de construction puis reconstruit
#
# Prérequis : sudo snap install snapcraft --classic
#             sudo snap install lxd && sudo lxd init --auto && sudo usermod -aG lxd "$USER"
set -eu

cd "$(dirname "$0")"

if ! command -v snapcraft >/dev/null 2>&1; then
    echo "snapcraft est introuvable. Installez-le avec :" >&2
    echo "  sudo snap install snapcraft --classic" >&2
    exit 1
fi

if [ "${1:-}" = "--clean" ]; then
    snapcraft clean
fi

# 1. Construction (dans un conteneur LXD Ubuntu 24.04, imposé par base: core24).
snapcraft pack

# 2. Fichier produit et commande d'installation locale.
VERSION=$(sed -n "s/^version: *['\"]\{0,1\}\([^'\"]*\)['\"]\{0,1\} *$/\1/p" snap/snapcraft.yaml)
ARCH=$(dpkg --print-architecture 2>/dev/null || echo amd64)
SNAP_FILE="lumina_${VERSION}_${ARCH}.snap"

if [ ! -f "$SNAP_FILE" ]; then
    echo "Construction terminée, mais $SNAP_FILE est introuvable." >&2
    exit 1
fi

echo
echo "Paquet construit : $SNAP_FILE"
echo
echo "Pour l'installer localement (paquet non signé, d'où --dangerous) :"
echo "  sudo snap install --dangerous ./$SNAP_FILE"
echo
echo "Puis lancer :  lumina   (ou depuis le menu des applications)"
echo "Désinstaller : sudo snap remove lumina"
