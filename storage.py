"""
storage.py — logique métier de Lumina (AUCUNE dépendance à GTK).

Responsabilités :
  * parcourir récursivement le dossier de notes et produire un arbre de `Node` ;
  * créer / lire / écrire / renommer / supprimer notes et dossiers ;
  * filtrer l'arbre pour la recherche ;
  * garantir que toute opération reste confinée dans le dossier racine.

Ce module peut être testé et réutilisé indépendamment de l'interface.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

NOTE_EXTENSIONS = (".md", ".txt")
DEFAULT_EXTENSION = ".md"
DEFAULT_ROOT = Path.home() / "LuminaNotes"
DEFAULT_NAME = "Sans titre"

# Caractères interdits dans un nom de fichier (Windows étant le plus strict).
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

WELCOME_NOTE = """# Bienvenue dans Lumina

Une application de notes minimaliste : rien que vous et vos idées.

- Vos notes sont de simples fichiers `.md` dans ce dossier.
- Créez des dossiers et sous-dossiers à l'infini avec le bouton **+**.
- Tout est enregistré automatiquement pendant que vous écrivez.
"""


class StorageError(Exception):
    """Erreur lisible par l'utilisateur (affichable dans un toast)."""


@dataclass
class Node:
    """Un élément de l'arborescence (dossier ou note)."""

    path: Path
    is_dir: bool
    children: list["Node"] = field(default_factory=list)

    @property
    def title(self) -> str:
        """Nom affiché : les notes sont montrées sans leur extension."""
        return self.path.name if self.is_dir else self.path.stem


def filter_tree(root: Node, query: str) -> Node:
    """
    Retourne une copie élaguée de l'arbre ne contenant que les éléments dont le
    nom correspond à `query` (insensible à la casse), ainsi que leurs parents.
    Si un dossier correspond, tout son contenu est conservé.
    """
    needle = query.strip().casefold()
    if not needle:
        return root

    def walk(node: Node) -> Node | None:
        matches = needle in node.title.casefold()
        if not node.is_dir:
            return node if matches else None
        if matches:
            return node
        kept = [c for c in (walk(child) for child in node.children) if c]
        return Node(node.path, True, kept) if kept else None

    kept = [c for c in (walk(child) for child in root.children) if c]
    return Node(root.path, True, kept)


class NoteStore:
    """Accès au système de fichiers pour les notes."""

    def __init__(self, root: str | os.PathLike | None = None, seed: bool = True):
        self.root = Path(root or DEFAULT_ROOT).expanduser().resolve()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StorageError(f"Impossible de créer {self.root} : {e}") from e
        # Première utilisation : une note d'accueil.
        if seed and not any(self.root.iterdir()):
            (self.root / "Bienvenue.md").write_text(WELCOME_NOTE, encoding="utf-8")

    # ------------------------------------------------------------------ lecture

    def scan(self) -> Node:
        """Parcourt tout le dossier racine et retourne l'arbre complet."""
        return Node(self.root, True, self._scan_dir(self.root))

    def _scan_dir(self, directory: Path) -> list[Node]:
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            return []

        dirs: list[Node] = []
        files: list[Node] = []
        for entry in entries:
            if entry.name.startswith("."):  # fichiers cachés / temporaires
                continue
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    dirs.append(Node(path, True, self._scan_dir(path)))
                elif entry.is_file() and path.suffix.lower() in NOTE_EXTENSIONS:
                    files.append(Node(path, False))
            except OSError:
                continue

        def by_name(n: Node) -> str:
            return n.path.name.casefold()

        # Dossiers d'abord, puis notes, chacun trié alphabétiquement.
        return sorted(dirs, key=by_name) + sorted(files, key=by_name)

    def read_note(self, path: Path) -> str:
        path = self._resolve(path)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise StorageError(f"Lecture impossible : {e}") from e

    # ------------------------------------------------------------------ écriture

    def write_note(self, path: Path, text: str) -> None:
        """Écriture atomique : fichier temporaire puis remplacement."""
        path = self._resolve(path)
        tmp_name = None
        try:
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".lumina-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
            tmp_name = None
        except OSError as e:
            raise StorageError(f"Enregistrement impossible : {e}") from e
        finally:
            if tmp_name and os.path.exists(tmp_name):
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    def create_note(self, parent: Path, name: str) -> Path:
        parent = self._resolve(parent)
        base = self._sanitize(name)
        ext = DEFAULT_EXTENSION
        if base.lower().endswith(NOTE_EXTENSIONS):
            base, ext = base[: base.rfind(".")], base[base.rfind("."):]
        path = self._unique(parent, base, ext)
        try:
            path.touch(exist_ok=False)
        except OSError as e:
            raise StorageError(f"Création impossible : {e}") from e
        return path

    def create_folder(self, parent: Path, name: str) -> Path:
        parent = self._resolve(parent)
        path = self._unique(parent, self._sanitize(name), "")
        try:
            path.mkdir()
        except OSError as e:
            raise StorageError(f"Création impossible : {e}") from e
        return path

    def rename(self, path: Path, new_name: str) -> Path:
        path = self._resolve(path)
        if path == self.root:
            raise StorageError("Le dossier racine ne peut pas être renommé.")
        base = self._sanitize(new_name)
        ext = ""
        if not path.is_dir():
            ext = path.suffix
            if base.lower().endswith(NOTE_EXTENSIONS):
                base, ext = base[: base.rfind(".")], base[base.rfind("."):]
        target = path.with_name(base + ext)
        if target == path:
            return path
        if target.exists():
            raise StorageError(f"« {target.name} » existe déjà.")
        try:
            path.rename(target)
        except OSError as e:
            raise StorageError(f"Renommage impossible : {e}") from e
        return target

    def delete(self, path: Path) -> None:
        path = self._resolve(path)
        if path == self.root:
            raise StorageError("Le dossier racine ne peut pas être supprimé.")
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        except OSError as e:
            raise StorageError(f"Suppression impossible : {e}") from e

    # ------------------------------------------------------------------ helpers

    def _resolve(self, path: str | os.PathLike) -> Path:
        """Résout `path` et refuse tout ce qui sort du dossier racine."""
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise StorageError("Chemin en dehors du dossier de notes.") from None
        return resolved

    @staticmethod
    def _sanitize(name: str) -> str:
        cleaned = _INVALID_CHARS.sub("", name).strip().strip(".")
        return cleaned or DEFAULT_NAME

    @staticmethod
    def _unique(parent: Path, base: str, ext: str) -> Path:
        """« Sans titre.md », puis « Sans titre 2.md », « Sans titre 3.md »…"""
        candidate = parent / f"{base}{ext}"
        n = 2
        while candidate.exists():
            candidate = parent / f"{base} {n}{ext}"
            n += 1
        return candidate
