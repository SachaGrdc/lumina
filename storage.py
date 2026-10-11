"""
storage.py — logique métier de Lumina (AUCUNE dépendance à GTK).

Responsabilités :
  * parcourir récursivement le dossier de notes et produire un arbre de `Node` ;
  * créer / lire / écrire / renommer / supprimer notes et dossiers ;
  * gérer la corbeille (`.trash`) : mise à la corbeille, restauration, vidage ;
  * filtrer l'arbre pour la recherche ;
  * garantir que toute opération reste confinée dans le dossier racine.

Ce module peut être testé et réutilisé indépendamment de l'interface.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from i18n import _

NOTE_EXTENSIONS = (".md", ".txt")
DEFAULT_EXTENSION = ".md"
DEFAULT_ROOT = Path.home() / "LuminaNotes"
TRASH_DIR = ".trash"   # caché (commence par un point) : absent de l'arborescence
# Index de la corbeille : nom dans .trash -> emplacement d'origine et date.
TRASH_INDEX = ".lumina-trash.json"

# Caractères interdits dans un nom de fichier (Windows étant le plus strict).
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


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


@dataclass(frozen=True)
class TrashEntry:
    """Élément mis à la corbeille : emplacement d'origine et emplacement actuel."""

    original: Path
    trashed: Path
    deleted_at: float = 0.0   # horodatage (secondes depuis l'epoch), 0 si inconnu

    @property
    def is_dir(self) -> bool:
        return self.trashed.is_dir()

    @property
    def title(self) -> str:
        return self.original.name if self.is_dir else self.original.stem


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

    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root or DEFAULT_ROOT).expanduser().resolve()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StorageError(_("Impossible de créer {path} : {error}").format(
                path=self.root, error=e)) from e

    def create_samples(self, samples: list[tuple[str, str]]) -> list[Path]:
        """Écrit les notes d'exemple (chemin relatif, contenu) sans écraser l'existant."""
        created = []
        for relative, text in samples:
            path = self._resolve(self.root / relative)
            if path.exists():
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            except OSError as e:
                raise StorageError(_("Création impossible : {error}").format(error=e)) from e
            created.append(path)
        return created

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
            if entry.name.startswith("."):  # fichiers cachés / temporaires, corbeille
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
            raise StorageError(_("Lecture impossible : {error}").format(error=e)) from e

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
            raise StorageError(_("Enregistrement impossible : {error}").format(error=e)) from e
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
            raise StorageError(_("Création impossible : {error}").format(error=e)) from e
        return path

    def create_folder(self, parent: Path, name: str) -> Path:
        parent = self._resolve(parent)
        path = self._unique(parent, self._sanitize(name), "")
        try:
            path.mkdir()
        except OSError as e:
            raise StorageError(_("Création impossible : {error}").format(error=e)) from e
        return path

    def rename(self, path: Path, new_name: str) -> Path:
        path = self._resolve(path)
        if path == self.root:
            raise StorageError(_("Le dossier racine ne peut pas être renommé."))
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
            raise StorageError(_("« {name} » existe déjà.").format(name=target.name))
        try:
            path.rename(target)
        except OSError as e:
            raise StorageError(_("Renommage impossible : {error}").format(error=e)) from e
        return target

    def move(self, src: Path, dest_folder: Path) -> Path:
        """
        Déplace une note ou un dossier à l'intérieur de `dest_folder`.
        En cas de collision de nom, le nouvel élément est renommé (« Plan 2.md »).
        Retourne le nouveau chemin (inchangé si l'élément y est déjà).
        """
        src = self._resolve(src)
        dest = self._resolve(dest_folder)
        if src == self.root:
            raise StorageError(_("Le dossier racine ne peut pas être déplacé."))
        if not src.exists():
            raise StorageError(_("« {name} » est introuvable.").format(name=src.name))
        if not dest.is_dir():
            raise StorageError(_("La destination n'est pas un dossier."))
        if src.is_dir() and (dest == src or src in dest.parents):
            raise StorageError(_("Un dossier ne peut pas être déplacé dans lui-même."))
        if src.parent == dest:
            return src

        base, ext = (src.name, "") if src.is_dir() else (src.stem, src.suffix)
        target = self._unique(dest, base, ext)
        try:
            shutil.move(str(src), str(target))
        except OSError as e:
            raise StorageError(_("Déplacement impossible : {error}").format(error=e)) from e
        return target

    def group_notes(self, src: Path, target: Path) -> tuple[Path, Path, Path]:
        """
        Crée un dossier à côté de `target` (nommé d'après elle) et y déplace
        `target` et `src`. Retourne (dossier, nouveau_src, nouveau_target).
        En cas d'échec, l'état initial est restauré.
        """
        src = self._resolve(src)
        target = self._resolve(target)
        if src == target:
            raise StorageError(_("Impossible de regrouper une note avec elle-même."))
        if src.is_dir() or target.is_dir():
            raise StorageError(_("Seules des notes peuvent être regroupées."))

        parent = target.parent
        folder = self._unique(parent, self._sanitize(target.stem), "")
        try:
            folder.mkdir()
        except OSError as e:
            raise StorageError(_("Création impossible : {error}").format(error=e)) from e

        moved: list[tuple[Path, Path]] = []  # (ancien, nouveau) pour le rollback
        try:
            for item in (target, src):
                moved.append((item, self.move(item, folder)))
        except StorageError:
            for old, new in reversed(moved):
                try:
                    shutil.move(str(new), str(old))
                except OSError:
                    pass
            try:
                folder.rmdir()
            except OSError:
                pass
            raise
        return folder, moved[1][1], moved[0][1]

    def delete(self, path: Path) -> None:
        path = self._resolve(path)
        if path == self.root:
            raise StorageError(_("Le dossier racine ne peut pas être supprimé."))
        try:
            shutil.rmtree(path) if path.is_dir() else path.unlink()
        except OSError as e:
            raise StorageError(_("Suppression impossible : {error}").format(error=e)) from e

    # ------------------------------------------------------------------ corbeille

    @property
    def trash_dir(self) -> Path:
        return self.root / TRASH_DIR

    def move_to_trash(self, path: Path) -> TrashEntry:
        """Déplace une note ou un dossier dans `.trash` (rien n'est effacé)."""
        path = self._resolve(path)
        if path == self.root:
            raise StorageError(_("Le dossier racine ne peut pas être supprimé."))
        if path == self.trash_dir or self.trash_dir in path.parents:
            raise StorageError(_("Cet élément est déjà dans la corbeille."))
        if not path.exists():
            raise StorageError(_("« {name} » est introuvable.").format(name=path.name))
        try:
            self.trash_dir.mkdir(exist_ok=True)
        except OSError as e:
            raise StorageError(_("Corbeille inaccessible : {error}").format(error=e)) from e

        base, ext = (path.name, "") if path.is_dir() else (path.stem, path.suffix)
        target = self._unique(self.trash_dir, base, ext)
        try:
            shutil.move(str(path), str(target))
        except OSError as e:
            raise StorageError(_("Mise à la corbeille impossible : {error}").format(error=e)) from e

        entry = TrashEntry(original=path, trashed=target, deleted_at=time.time())
        index = self._read_trash_index()
        index[target.name] = {"original": path.relative_to(self.root).as_posix(),
                              "deleted": entry.deleted_at}
        self._write_trash_index(index)
        return entry

    def list_trash(self) -> list[TrashEntry]:
        """Éléments de la corbeille, du plus récent au plus ancien."""
        try:
            with os.scandir(self.trash_dir) as it:
                names = [e.name for e in it if not e.name.startswith(".")]
        except OSError:
            return []
        index = self._read_trash_index()
        entries = []
        for name in names:
            info = index.get(name) if isinstance(index.get(name), dict) else {}
            original = info.get("original")
            original = self.root / original if isinstance(original, str) else self.root / name
            deleted = info.get("deleted")
            entries.append(TrashEntry(original=original, trashed=self.trash_dir / name,
                                      deleted_at=deleted if isinstance(deleted, (int, float)) else 0.0))
        entries.sort(key=lambda e: (-e.deleted_at, e.trashed.name.casefold()))
        return entries

    def restore(self, entry: TrashEntry) -> Path:
        """Remet un élément de la corbeille à sa place (renommé en cas de collision)."""
        trashed = self._check_trashed(entry)
        original = self._resolve(entry.original)
        if self.trash_dir in original.parents:  # index corrompu : retour à la racine
            original = self.root / trashed.name
        base, ext = ((original.name, "") if trashed.is_dir()
                     else (original.stem, original.suffix))
        try:
            original.parent.mkdir(parents=True, exist_ok=True)  # le dossier parent a pu disparaître
            target = self._unique(original.parent, base, ext)
            shutil.move(str(trashed), str(target))
        except OSError as e:
            raise StorageError(_("Restauration impossible : {error}").format(error=e)) from e
        self._forget_trashed(trashed.name)
        return target

    def delete_from_trash(self, entry: TrashEntry) -> None:
        """Supprime définitivement un élément de la corbeille."""
        trashed = self._check_trashed(entry)
        try:
            shutil.rmtree(trashed) if trashed.is_dir() else trashed.unlink()
        except OSError as e:
            raise StorageError(_("Suppression impossible : {error}").format(error=e)) from e
        self._forget_trashed(trashed.name)

    def empty_trash(self) -> int:
        """Supprime définitivement tout le contenu de la corbeille ; retourne le nombre d'éléments."""
        entries = self.list_trash()
        if not self.trash_dir.exists():
            return 0
        try:
            shutil.rmtree(self.trash_dir)
        except OSError as e:
            raise StorageError(_("Suppression impossible : {error}").format(error=e)) from e
        return len(entries)

    def _check_trashed(self, entry: TrashEntry) -> Path:
        trashed = self._resolve(entry.trashed)
        if trashed.parent != self.trash_dir or not trashed.exists():
            raise StorageError(_("« {name} » n'est plus dans la corbeille.").format(
                name=entry.trashed.name))
        return trashed

    def _read_trash_index(self) -> dict:
        try:
            data = json.loads((self.trash_dir / TRASH_INDEX).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_trash_index(self, index: dict) -> None:
        path = self.trash_dir / TRASH_INDEX
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # sans index, la restauration se fait à la racine

    def _forget_trashed(self, name: str) -> None:
        index = self._read_trash_index()
        if index.pop(name, None) is not None:
            self._write_trash_index(index)

    # ------------------------------------------------------------------ helpers

    def _resolve(self, path: str | os.PathLike) -> Path:
        """Résout `path` et refuse tout ce qui sort du dossier racine."""
        resolved = Path(path).expanduser().resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise StorageError(_("Chemin en dehors du dossier de notes.")) from None
        return resolved

    @staticmethod
    def _sanitize(name: str) -> str:
        cleaned = _INVALID_CHARS.sub("", name).strip().strip(".")
        return cleaned or _("Sans titre")

    @staticmethod
    def _unique(parent: Path, base: str, ext: str) -> Path:
        """« Sans titre.md », puis « Sans titre 2.md », « Sans titre 3.md »…"""
        candidate = parent / f"{base}{ext}"
        n = 2
        while candidate.exists():
            candidate = parent / f"{base} {n}{ext}"
            n += 1
        return candidate
