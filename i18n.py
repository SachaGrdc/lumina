"""
i18n.py — traduction de l'interface (AUCUNE dépendance à GTK).

Le français est la langue source : les textes sont écrits en français dans le
code et passés à `_()`, qui renvoie leur traduction dans la langue active.
Les textes paramétrés utilisent des champs nommés : `_("« {name} » restauré").format(name=…)`.

Pour ajouter une langue : un dictionnaire de plus dans TRANSLATIONS, une entrée
dans LANGUAGE_NAMES, et sa valeur dans config.LANGUAGES.
"""

from __future__ import annotations

import locale
import os

SOURCE_LANGUAGE = "fr"
LANGUAGE_NAMES = {"fr": "Français", "en": "English"}   # noms natifs, jamais traduits
# Débuts de nom de locale reconnus : POSIX (« fr_FR.UTF-8 ») et Windows (« French_France »).
_LOCALE_PREFIXES = {"fr": ("fr", "french"), "en": ("en", "english")}

EN = {
    # --- fenêtre principale
    "Nouvelle note": "New note",
    "Nouveau dossier": "New folder",
    "Nouveau": "New",
    "Menu principal": "Main menu",
    "Corbeille": "Trash",
    "Préférences": "Preferences",
    "Rechercher…": "Search…",
    "Afficher / masquer la barre latérale (Ctrl+\\)": "Show / hide the sidebar (Ctrl+\\)",
    "Annuler": "Cancel",
    "Note épinglée": "Note pinned",
    "Note désépinglée": "Note unpinned",
    "Créé à la racine": "Created at the top level",
    "Créé dans : {folder}": "Created in: {folder}",
    "Sans titre": "Untitled",
    "Créer": "Create",
    "Renommer": "Rename",
    "Nouveau nom pour le dossier « {name} »": "New name for the folder “{name}”",
    "Nouveau nom pour la note « {name} »": "New name for the note “{name}”",
    "Épinglées": "Pinned",
    "Nouvel onglet": "New tab",
    "Ouvrir dans un nouvel onglet": "Open in New Tab",
    "Imprimer / Exporter en PDF…": "Print / Export as PDF…",
    "Impression impossible : {error}": "Could not print: {error}",
    "Ctrl+clic pour ouvrir le lien": "Ctrl+click to open the link",
    "Impossible d'ouvrir le lien : {error}": "Could not open the link: {error}",
    "« {name} » importé": "“{name}” imported",
    "{count} notes importées": "{count} notes imported",
    # --- glisser-déposer
    "Dossier « {name} » créé": "Folder “{name}” created",
    "« {name} » déplacé à la racine": "“{name}” moved to the top level",
    "« {name} » déplacé dans « {folder} »": "“{name}” moved to “{folder}”",
    # --- menus contextuels
    "Épingler": "Pin",
    "Désépingler": "Unpin",
    "Renommer…": "Rename…",
    "Mettre à la corbeille": "Move to Trash",
    "Restaurer la note": "Restore note",
    "Restaurer le dossier": "Restore folder",
    "Supprimer définitivement…": "Delete permanently…",
    # --- corbeille
    "« {name} » mis à la corbeille": "“{name}” moved to the trash",
    "« {name} » restauré": "“{name}” restored",
    "« {name} » n'est plus dans la corbeille.": "“{name}” is no longer in the trash.",
    "Supprimer « {name} » définitivement ?": "Permanently delete “{name}”?",
    "Cette action est irréversible.": "This cannot be undone.",
    "Supprimer": "Delete",
    "Corbeille vidée": "Trash emptied",
    "Vider la corbeille ?": "Empty the trash?",
    "Vider la corbeille": "Empty Trash",
    "1 élément sera supprimé définitivement.": "1 item will be permanently deleted.",
    "{count} éléments seront supprimés définitivement.":
        "{count} items will be permanently deleted.",
    "La corbeille est vide": "The trash is empty",
    "Les notes supprimées sont conservées ici jusqu'à ce que la corbeille soit vidée.":
        "Deleted notes are kept here until the trash is emptied.",
    "Clic droit sur un élément pour le restaurer.": "Right-click an item to restore it.",
    "Racine": "Top level",
    "Emplacement d'origine : {path}": "Original location: {path}",
    # --- préférences
    "Général": "General",
    "Langue": "Language",
    "Langue du système ({language})": "System language ({language})",
    "Stockage": "Storage",
    "Lumina affiche les notes de ce dossier. Changer de dossier ne déplace pas les notes existantes.":
        "Lumina shows the notes in this folder. Changing it does not move existing notes.",
    "Dossier des notes": "Notes folder",
    "Choisir un autre dossier…": "Choose another folder…",
    "Éditeur et apparence": "Editor and appearance",
    "Thème": "Theme",
    "Suivre le système": "Follow system",
    "Sombre": "Dark",
    "Clair": "Light",
    "Taille du texte": "Text size",
    "En points": "In points",
    "Numéros de ligne": "Line numbers",
    "Affiche le numéro de chaque ligne dans la marge": "Show the number of each line in the margin",
    "Impossible d'enregistrer la configuration : {error}": "Could not save the settings: {error}",
    # --- accueil
    "Bienvenue dans Lumina": "Welcome to Lumina",
    "Des notes simples, enregistrées en Markdown dans le dossier de votre choix. "
    "Vous pourrez le changer plus tard dans les préférences.":
        "Simple notes, saved as Markdown in the folder of your choice. "
        "You can change it later in the preferences.",
    "Choisir…": "Choose…",
    "Créer des notes d'exemple": "Create sample notes",
    "Trois notes pour découvrir Lumina": "Three notes to get to know Lumina",
    "Commencer": "Get started",
    # --- erreurs du stockage
    "Impossible de créer {path} : {error}": "Could not create {path}: {error}",
    "Lecture impossible : {error}": "Could not read the note: {error}",
    "Enregistrement impossible : {error}": "Could not save: {error}",
    "Création impossible : {error}": "Could not create: {error}",
    "Le dossier racine ne peut pas être renommé.": "The notes folder itself cannot be renamed.",
    "« {name} » existe déjà.": "“{name}” already exists.",
    "Renommage impossible : {error}": "Could not rename: {error}",
    "Le dossier racine ne peut pas être déplacé.": "The notes folder itself cannot be moved.",
    "« {name} » est introuvable.": "“{name}” could not be found.",
    "La destination n'est pas un dossier.": "The destination is not a folder.",
    "Un dossier ne peut pas être déplacé dans lui-même.": "A folder cannot be moved into itself.",
    "Déplacement impossible : {error}": "Could not move: {error}",
    "Impossible de regrouper une note avec elle-même.": "A note cannot be grouped with itself.",
    "Seules des notes peuvent être regroupées.": "Only notes can be grouped.",
    "Le dossier racine ne peut pas être supprimé.": "The notes folder itself cannot be deleted.",
    "Suppression impossible : {error}": "Could not delete: {error}",
    "Cet élément est déjà dans la corbeille.": "This item is already in the trash.",
    "Corbeille inaccessible : {error}": "The trash is not accessible: {error}",
    "Mise à la corbeille impossible : {error}": "Could not move to the trash: {error}",
    "Restauration impossible : {error}": "Could not restore: {error}",
    "Chemin en dehors du dossier de notes.": "Path outside the notes folder.",
    "Seuls les fichiers .md et .txt peuvent être importés.": "Only .md and .txt files can be imported.",
    "Importation impossible : {error}": "Could not import: {error}",
}

TRANSLATIONS: dict[str, dict[str, str]] = {"en": EN}

_current = SOURCE_LANGUAGE


def system_language() -> str:
    """Langue du système parmi celles disponibles (anglais si elle n'est pas gérée)."""
    code = ""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if value:
            code = value.split(":")[0]
            break
    if not code:
        try:
            code = locale.getlocale()[0] or ""   # Windows : « French_France »
        except ValueError:
            code = ""
    code = code.lower()
    for lang, prefixes in _LOCALE_PREFIXES.items():
        if code.startswith(prefixes):
            return lang
    return "en"


def set_language(choice: str) -> str:
    """`choice` : "system", "fr" ou "en". Retourne la langue effectivement appliquée."""
    global _current
    lang = system_language() if choice == "system" else choice
    _current = lang if lang in LANGUAGE_NAMES else "en"
    return _current


def current_language() -> str:
    return _current


def _(text: str) -> str:
    if _current == SOURCE_LANGUAGE:
        return text
    return TRANSLATIONS.get(_current, {}).get(text, text)
