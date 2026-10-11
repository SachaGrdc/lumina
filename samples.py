"""
samples.py — notes d'exemple proposées au premier lancement, par langue.

Chaque note est un couple (chemin relatif à la racine, contenu Markdown).
"""

SAMPLES = {
    "fr": [
        ("Bienvenue.md", """# Bienvenue dans Lumina

Une application de notes minimaliste : rien que vous et vos idées.

- Vos notes sont de simples fichiers `.md` dans le dossier de votre choix.
- Créez des dossiers et sous-dossiers à l'infini avec le bouton **+**.
- Tout est enregistré automatiquement pendant que vous écrivez.
- Faites glisser une note sur une autre pour les regrouper dans un dossier.
- Clic droit sur une note : épingler, renommer, mettre à la corbeille.
"""),
        ("Raccourcis clavier.md", """# Raccourcis clavier

- **Ctrl+N** : nouvelle note
- **Ctrl+Maj+N** : nouveau dossier
- **Ctrl+F** : rechercher
- **F2** : renommer
- **Suppr** (dans la barre latérale) : mettre à la corbeille
- **Ctrl+B** ou **F9** : afficher / masquer la barre latérale
- **Ctrl+,** : préférences
"""),
        ("Idées/Liste de courses.md", """# Liste de courses

Cette note est rangée dans le dossier « Idées ».

- [ ] Pain
- [ ] Café
- [ ] Un carnet tout neuf
"""),
    ],
    "en": [
        ("Welcome.md", """# Welcome to Lumina

A minimalist notes app: just you and your ideas.

- Your notes are plain `.md` files in the folder of your choice.
- Create folders and subfolders with the **+** button.
- Everything is saved automatically while you type.
- Drag a note onto another one to group them in a folder.
- Right-click a note to pin, rename or move it to the trash.
"""),
        ("Keyboard shortcuts.md", """# Keyboard shortcuts

- **Ctrl+N**: new note
- **Ctrl+Shift+N**: new folder
- **Ctrl+F**: search
- **F2**: rename
- **Delete** (in the sidebar): move to trash
- **Ctrl+B** or **F9**: show / hide the sidebar
- **Ctrl+,**: preferences
"""),
        ("Ideas/Shopping list.md", """# Shopping list

This note lives in the "Ideas" folder.

- [ ] Bread
- [ ] Coffee
- [ ] A brand-new notebook
"""),
    ],
}


def samples_for(language: str) -> list[tuple[str, str]]:
    return SAMPLES.get(language, SAMPLES["en"])
