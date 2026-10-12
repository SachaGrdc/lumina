<div align="center">

<img src="data/icons/hicolor/scalable/apps/com.github.lumina.svg" width="128" height="128" alt="Lumina">

# Lumina

**Un éditeur de notes Markdown fluide, minimaliste et natif pour l'écosystème GNOME.**

[![Licence : GPL-3.0](https://img.shields.io/badge/licence-GPL--3.0-blue.svg)](LICENSE)
[![GTK4](https://img.shields.io/badge/GTK-4-4a86cf.svg?logo=gtk)](https://www.gtk.org/)
[![Libadwaita](https://img.shields.io/badge/Libadwaita-1-3584e4.svg?logo=gnome)](https://gnome.pages.gitlab.gnome.org/libadwaita/)
[![Python 3](https://img.shields.io/badge/Python-3-3776ab.svg?logo=python&logoColor=white)](https://www.python.org/)

🇫🇷 Français · [🇬🇧 English](#-english)

</div>

## 🇫🇷 Français

### 📸 Aperçu

<!-- Capture d'écran à ajouter : docs/screenshots/main-window.png -->
<p align="center">
  <img src="docs/screenshots/main-window.png" alt="Fenêtre principale de Lumina" width="800">
</p>

### ✨ Fonctionnalités

- 📝 **Édition Markdown** : vos notes sont de simples fichiers `.md` ou `.txt`. L'éditeur affiche les liens cliquables (<kbd>Ctrl</kbd> + clic), la numérotation des lignes en option et des raccourcis clavier pour tout.
- 📁 **Organisation complète** : arborescence de dossiers et sous-dossiers, glisser-déposer, corbeille avec restauration.
- 📑 **Onglets et navigation** : plusieurs onglets, rouverts au lancement, notes épinglées et recherche globale dans toutes vos notes.
- 🎨 **Design natif** : intégration avec Libadwaita (thème sombre ou clair automatique, `Adw.StatusPage`), interface en français et en anglais.
- 💾 **Tranquillité d'esprit** : sauvegarde automatique en temps réel, sans bouton « Enregistrer ».
- 🖨️ **Impression et export PDF** de vos notes.

### ⌨️ Raccourcis clavier

| Action | Raccourci |
|---|---|
| Nouvelle note | <kbd>Ctrl</kbd> + <kbd>N</kbd> |
| Nouveau dossier | <kbd>Ctrl</kbd> + <kbd>Maj</kbd> + <kbd>N</kbd> |
| Rechercher | <kbd>Ctrl</kbd> + <kbd>F</kbd> |
| Afficher/masquer la barre latérale | <kbd>Ctrl</kbd> + <kbd>B</kbd> ou <kbd>F9</kbd> |
| Nouvel onglet / fermer l'onglet | <kbd>Ctrl</kbd> + <kbd>T</kbd> / <kbd>Ctrl</kbd> + <kbd>W</kbd> |
| Imprimer | <kbd>Ctrl</kbd> + <kbd>P</kbd> |
| Préférences | <kbd>Ctrl</kbd> + <kbd>,</kbd> |

### 📦 Installation

#### Snap

```bash
sudo snap install lumina
```

> 💡 Pour installer un paquet construit localement avec `./build-snap.sh` :
>
> ```bash
> sudo snap install --dangerous ./lumina_1.0.0_amd64.snap
> ```

#### Depuis les sources (Python)

Installez d'abord les dépendances :

```bash
# Debian / Ubuntu
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita
```

Puis :

```bash
git clone https://github.com/SachaGrdc/lumina.git
cd lumina
./install.sh      # ajoute Lumina au menu des applications
# Ou directement
python3 main.py
```

> ℹ️ `./install.sh --uninstall` retire le lanceur et l'icône.

### 📄 Licence

Lumina est distribué sous licence **[GPL-3.0](LICENSE)**.

---

<div align="center">

## 🇬🇧 English

**A smooth, minimalist and native Markdown note editor for the GNOME ecosystem.**

[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![GTK4](https://img.shields.io/badge/GTK-4-4a86cf.svg?logo=gtk)](https://www.gtk.org/)
[![Libadwaita](https://img.shields.io/badge/Libadwaita-1-3584e4.svg?logo=gnome)](https://gnome.pages.gitlab.gnome.org/libadwaita/)
[![Python 3](https://img.shields.io/badge/Python-3-3776ab.svg?logo=python&logoColor=white)](https://www.python.org/)

[🇫🇷 Français](#-français) · 🇬🇧 English

</div>

### 📸 Preview

<!-- Screenshot to add: docs/screenshots/main-window.png -->
<p align="center">
  <img src="docs/screenshots/main-window.png" alt="Lumina main window" width="800">
</p>

### ✨ Features

- 📝 **Markdown editing**: notes are plain `.md` or `.txt` files. The editor offers clickable links (<kbd>Ctrl</kbd> + click), optional line numbers and keyboard shortcuts for everything.
- 📁 **Full organization**: folder and subfolder tree, drag and drop, trash with restore.
- 📑 **Tabs & navigation**: multiple tabs restored on startup, pinned notes and global search across all your notes.
- 🎨 **Native design**: Libadwaita integration (automatic dark/light theme, `Adw.StatusPage`), French and English interface.
- 💾 **Peace of mind**: real-time autosave, no "Save" button needed.
- 🖨️ **Printing and PDF export** of your notes.

### ⌨️ Keyboard shortcuts

| Action | Shortcut |
|---|---|
| New note | <kbd>Ctrl</kbd> + <kbd>N</kbd> |
| New folder | <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>N</kbd> |
| Search | <kbd>Ctrl</kbd> + <kbd>F</kbd> |
| Toggle sidebar | <kbd>Ctrl</kbd> + <kbd>B</kbd> or <kbd>F9</kbd> |
| New tab / close tab | <kbd>Ctrl</kbd> + <kbd>T</kbd> / <kbd>Ctrl</kbd> + <kbd>W</kbd> |
| Print | <kbd>Ctrl</kbd> + <kbd>P</kbd> |
| Preferences | <kbd>Ctrl</kbd> + <kbd>,</kbd> |

### 📦 Installation

#### Snap

```bash
sudo snap install lumina
```

> 💡 To install a package built locally with `./build-snap.sh`:
>
> ```bash
> sudo snap install --dangerous ./lumina_1.0.0_amd64.snap
> ```

#### From source (Python)

Install the dependencies first:

```bash
# Debian / Ubuntu
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1
# Fedora
sudo dnf install python3-gobject gtk4 libadwaita
```

Then:

```bash
git clone https://github.com/SachaGrdc/lumina.git
cd lumina
./install.sh      # adds Lumina to the applications menu
# Or run it directly
python3 main.py
```

> ℹ️ `./install.sh --uninstall` removes the launcher and the icon.

### 📄 License

Lumina is distributed under the **[GPL-3.0](LICENSE)** license.
