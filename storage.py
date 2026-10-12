"""
storage.py — accès aux notes sur le disque (AUCUNE dépendance à GTK).

`StorageManager` est le SEUL point d'accès au dossier de notes :
  * arborescence des dossiers et des notes (.md, .txt), sans fichiers cachés ni `.trash/` ;
  * lecture et écriture atomique des notes ;
  * création, renommage, déplacement, regroupement, import — avec un nom unique
    automatique (« Sans titre », « Sans titre 1 », « Sans titre 2 »…) ;
  * corbeille `.trash/` : mise à la corbeille, restauration, suppression définitive ;
  * recherche dans les titres et dans le contenu des notes.

Règles défensives appliquées à TOUTES les opérations :
  * chaque chemin reçu est résolu (`Path.resolve`) puis vérifié : il doit rester
    dans la racine — ni « .. », ni lien symbolique ne permettent d'en sortir ;
  * on ne crée rien dans la corbeille, on ne touche jamais à la racine elle-même ;
  * les noms saisis sont nettoyés (caractères interdits, noms réservés Windows…) ;
  * aucune opération n'écrase un fichier existant ;
  * toute erreur système devient une `StorageError` au message lisible, que
    l'interface affiche telle quelle. Aucune autre exception ne sort d'ici.

Ce module se teste et se réutilise indépendamment de l'interface.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Container, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from i18n import _

NOTE_EXTENSIONS = (".md", ".txt")
DEFAULT_EXTENSION = ".md"
# Dans un snap, Path.home() désigne ~/snap/lumina/<révision> : les notes vont
# dans le vrai dossier personnel ($SNAP_REAL_HOME, accessible via l'interface home).
DEFAULT_ROOT = Path(os.environ.get("SNAP_REAL_HOME") or Path.home()) / "LuminaNotes"
TRASH_DIR = ".trash"                  # caché (commence par un point) : absent de l'arborescence
TRASH_INDEX = ".lumina-trash.json"    # nom dans .trash -> emplacement d'origine et date
MAX_NAME_LENGTH = 200

# Caractères interdits dans un nom de fichier (Windows étant le plus strict).
_FORBIDDEN_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
# Noms réservés par Windows, quelle que soit l'extension (« CON.md » est invalide).
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL",
                   *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

# Extrait affiché sous un résultat de recherche : contexte avant / après l'occurrence.
SNIPPET_BEFORE = 24
SNIPPET_AFTER = 80


class StorageError(Exception):
    """Erreur lisible par l'utilisateur (affichable dans un toast)."""


# ====================================================================== données


@dataclass
class Node:
    """Un élément de l'arborescence (dossier ou note)."""

    path: Path
    is_dir: bool
    children: list[Node] = field(default_factory=list)

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


@dataclass(frozen=True)
class Snippet:
    """Extrait d'une note autour d'une occurrence de la recherche."""

    text: str     # une seule ligne
    start: int    # position de l'occurrence dans `text`
    end: int


# ====================================================================== fonctions pures


def is_note_file(path: Path) -> bool:
    return path.suffix.lower() in NOTE_EXTENSIONS


def clean_name(name: str) -> str:
    """
    Nettoie un nom saisi : retire les caractères interdits, les points et espaces
    de début / fin (fichiers cachés, « .. », restrictions Windows), limite la
    longueur et évite les noms réservés. Peut retourner une chaîne vide.
    """
    cleaned = _FORBIDDEN_CHARS.sub("", name).strip().strip(".").strip()
    cleaned = cleaned[:MAX_NAME_LENGTH].rstrip(". ")
    base = cleaned.split(".")[0]
    if base.upper() in _RESERVED_NAMES:   # « nul.md » -> « nul_.md »
        cleaned = base + "_" + cleaned[len(base):]
    return cleaned


def split_note_name(name: str, default_ext: str = DEFAULT_EXTENSION) -> tuple[str, str]:
    """« Plan » -> ("Plan", ".md") ; « Plan.txt » -> ("Plan", ".txt") ; « » -> ("Sans titre", ".md")."""
    cleaned = clean_name(name)
    stem, ext = cleaned, default_ext
    if cleaned.lower().endswith(NOTE_EXTENSIONS):
        dot = cleaned.rfind(".")
        stem, ext = clean_name(cleaned[:dot]), cleaned[dot:].lower()
    return stem or _("Sans titre"), ext


def make_snippet(text: str, start: int, end: int) -> Snippet:
    """Extrait la ligne contenant text[start:end], raccourcie autour de l'occurrence."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    a = max(line_start, start - SNIPPET_BEFORE)
    b = min(line_end, end + SNIPPET_AFTER)
    before = ("…" if a > line_start else "") + text[a:start].lstrip()
    after = text[end:b].rstrip() + ("…" if b < line_end else "")
    snippet = (before + text[start:end] + after).replace("\t", " ")
    return Snippet(snippet, len(before), len(before) + end - start)


def iter_notes(node: Node) -> Iterator[Path]:
    """Toutes les notes (pas les dossiers) de l'arbre `node`."""
    for child in node.children:
        if child.is_dir:
            yield from iter_notes(child)
        else:
            yield child.path


def filter_tree(root: Node, query: str, content_hits: Container[Path] = frozenset()) -> Node:
    """
    Copie élaguée de l'arbre : éléments dont le nom contient `query` (insensible
    à la casse) ou notes présentes dans `content_hits` (contenu correspondant),
    ainsi que leurs dossiers parents. Un dossier qui correspond garde tout son contenu.
    """
    needle = query.strip().casefold()
    if not needle:
        return root

    def walk(node: Node) -> Node | None:
        matches = needle in node.title.casefold()
        if not node.is_dir:
            return node if matches or node.path in content_hits else None
        if matches:
            return node
        kept = [c for c in (walk(child) for child in node.children) if c]
        return Node(node.path, True, kept) if kept else None

    return Node(root.path, True, [c for c in (walk(child) for child in root.children) if c])


# ====================================================================== StorageManager


class StorageManager:
    """Accès au dossier de notes ; toutes les erreurs sont des `StorageError`."""

    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root or DEFAULT_ROOT).expanduser().resolve()
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StorageError(_("Impossible de créer {path} : {error}").format(
                path=self.root, error=e)) from e
        if not self.root.is_dir():
            raise StorageError(_("La destination n'est pas un dossier."))
        # Contenu des notes déjà lues par la recherche : chemin -> (date de modification, texte).
        self._text_cache: dict[Path, tuple[int, str]] = {}

    @property
    def trash_dir(self) -> Path:
        return self.root / TRASH_DIR

    # ------------------------------------------------------------------ chemins

    def contains(self, path: str | os.PathLike) -> bool:
        """Vrai si `path` (une fois résolu) est la racine ou se trouve dedans."""
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):   # boucle de liens symboliques…
            return False
        return resolved == self.root or self.root in resolved.parents

    def in_trash(self, path: Path) -> bool:
        return path == self.trash_dir or self.trash_dir in path.parents

    def _inside(self, path: str | os.PathLike) -> Path:
        """Chemin résolu, garanti dans la racine."""
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError):
            raise StorageError(_("Chemin en dehors du dossier de notes.")) from None
        if resolved != self.root and self.root not in resolved.parents:
            raise StorageError(_("Chemin en dehors du dossier de notes."))
        return resolved

    def _item(self, path: str | os.PathLike) -> Path:
        """Note ou dossier existant de l'arborescence (ni la racine, ni la corbeille)."""
        resolved = self._inside(path)
        if resolved == self.root:
            raise StorageError(_("Le dossier racine ne peut pas être modifié."))
        if self.in_trash(resolved):
            raise StorageError(_("Cet élément est déjà dans la corbeille."))
        if not resolved.exists():
            raise StorageError(_("« {name} » est introuvable.").format(name=resolved.name))
        return resolved

    def _folder(self, path: str | os.PathLike) -> Path:
        """Dossier de destination : dans la racine, existant, hors de la corbeille."""
        resolved = self._inside(path)
        if self.in_trash(resolved):
            raise StorageError(_("Impossible de créer un élément dans la corbeille."))
        if not resolved.is_dir():
            raise StorageError(_("Le dossier « {name} » n'existe plus.").format(name=resolved.name))
        return resolved

    @staticmethod
    def unique_path(folder: Path, stem: str, ext: str) -> Path:
        """« Sans titre.md », puis « Sans titre 1.md », « Sans titre 2.md »…"""
        candidate = folder / f"{stem}{ext}"
        n = 1
        while candidate.exists() or candidate.is_symlink():
            candidate = folder / f"{stem} {n}{ext}"
            n += 1
        return candidate

    # ------------------------------------------------------------------ arborescence

    def scan(self) -> Node:
        """Parcourt tout le dossier de notes : dossiers d'abord, puis notes, par ordre alphabétique."""
        return Node(self.root, True, self._scan_folder(self.root))

    def _scan_folder(self, folder: Path) -> list[Node]:
        try:
            entries = list(folder.iterdir())
        except OSError:
            return []
        folders: list[Node] = []
        notes: list[Node] = []
        for entry in entries:
            if entry.name.startswith("."):   # fichiers cachés / temporaires, corbeille
                continue
            try:
                if entry.is_symlink():
                    # Un dossier lié pourrait boucler ; une note liée doit rester dans la racine.
                    if entry.is_dir() or not self.contains(entry):
                        continue
                if entry.is_dir():
                    folders.append(Node(entry, True, self._scan_folder(entry)))
                elif entry.is_file() and is_note_file(entry):
                    notes.append(Node(entry, False))
            except OSError:
                continue

        def by_name(node: Node) -> str:
            return node.path.name.casefold()

        return sorted(folders, key=by_name) + sorted(notes, key=by_name)

    # ------------------------------------------------------------------ lecture / écriture

    def read_note(self, path: Path) -> str:
        path = self._inside(path)
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise StorageError(_("Lecture impossible : {error}").format(error=e)) from e

    def write_note(self, path: Path, text: str) -> None:
        """Écriture atomique : fichier temporaire caché, puis remplacement."""
        path = self._inside(path)
        if self.in_trash(path) or path.is_dir():
            raise StorageError(_("Enregistrement impossible : {error}").format(error=path.name))
        tmp: Path | None = None
        try:
            fd, name = tempfile.mkstemp(dir=path.parent, prefix=".lumina-", suffix=".tmp")
            tmp = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(path)
            tmp = None
        except OSError as e:
            raise StorageError(_("Enregistrement impossible : {error}").format(error=e)) from e
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------ création

    def suggest_name(self, folder: Path, name: str, is_folder: bool) -> str:
        """
        Nom libre à proposer dans le dialogue de création : `name`, sinon « name 1 »,
        « name 2 »… (sans l'extension pour une note).
        """
        try:
            folder = self._folder(folder)
        except StorageError:
            return name
        if is_folder:
            return self.unique_path(folder, clean_name(name) or name, "").name
        stem, ext = split_note_name(name)
        return self.unique_path(folder, stem, ext).stem

    def create_note(self, folder: Path, name: str) -> Path:
        """Crée une note vide dans `folder` ; un nom déjà pris devient « Nom 1 », « Nom 2 »…"""
        folder = self._folder(folder)
        stem, ext = split_note_name(name)
        path = self.unique_path(folder, stem, ext)
        try:
            path.touch(exist_ok=False)
        except OSError as e:
            raise StorageError(_("Création impossible : {error}").format(error=e)) from e
        return path

    def create_folder(self, folder: Path, name: str) -> Path:
        """Crée un dossier dans `folder` ; un nom déjà pris devient « Nom 1 », « Nom 2 »…"""
        folder = self._folder(folder)
        path = self.unique_path(folder, clean_name(name) or _("Nouveau dossier"), "")
        try:
            path.mkdir()
        except OSError as e:
            raise StorageError(_("Création impossible : {error}").format(error=e)) from e
        return path

    def create_samples(self, samples: list[tuple[str, str]]) -> list[Path]:
        """Écrit les notes d'exemple (chemin relatif, contenu) sans écraser l'existant."""
        created = []
        for relative, text in samples:
            path = self._inside(self.root / relative)
            if path.exists():
                continue
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            except OSError as e:
                raise StorageError(_("Création impossible : {error}").format(error=e)) from e
            created.append(path)
        return created

    def import_note(self, src: Path, folder: Path) -> Path:
        """Copie un fichier externe (.md / .txt) dans `folder` ; l'original n'est pas modifié."""
        src = Path(src)
        if not src.is_file() or not is_note_file(src):
            raise StorageError(_("Seuls les fichiers .md et .txt peuvent être importés."))
        folder = self._folder(folder)
        target = self.unique_path(folder, clean_name(src.stem) or _("Sans titre"), src.suffix.lower())
        try:
            shutil.copy2(src, target)
        except OSError as e:
            raise StorageError(_("Importation impossible : {error}").format(error=e)) from e
        return target

    # ------------------------------------------------------------------ renommage / déplacement

    def rename(self, path: Path, new_name: str) -> Path:
        """Renomme une note (l'extension est conservée) ou un dossier ; retourne le nouveau chemin."""
        path = self._item(path)
        if path.is_dir():
            target = path.with_name(clean_name(new_name) or path.name)
        else:
            stem, ext = split_note_name(new_name, default_ext=path.suffix)
            target = path.with_name(stem + ext)
        # Comparaison des NOMS : sous Windows, Path("a") == Path("A") alors qu'un
        # changement de casse est bien un renommage.
        if target.name == path.name:
            return path
        if (target.exists() or target.is_symlink()) and not self._same_file(target, path):
            raise StorageError(_("« {name} » existe déjà.").format(name=target.name))
        try:
            return path.rename(target)
        except OSError as e:
            raise StorageError(_("Renommage impossible : {error}").format(error=e)) from e

    def move(self, src: Path, folder: Path) -> Path:
        """
        Déplace une note ou un dossier dans `folder` (renommé « Nom 1 » en cas de
        collision). Retourne le nouveau chemin, inchangé si l'élément y est déjà.
        """
        src = self._item(src)
        folder = self._folder(folder)
        if src.is_dir() and (folder == src or src in folder.parents):
            raise StorageError(_("Un dossier ne peut pas être déplacé dans lui-même."))
        if src.parent == folder:
            return src
        stem, ext = (src.name, "") if src.is_dir() else (src.stem, src.suffix)
        target = self.unique_path(folder, stem, ext)
        try:
            return Path(shutil.move(src, target))
        except OSError as e:
            raise StorageError(_("Déplacement impossible : {error}").format(error=e)) from e

    def group_notes(self, src: Path, target: Path) -> tuple[Path, Path, Path]:
        """
        Crée un dossier à côté de `target` (nommé d'après elle) et y range `target`
        et `src`. Retourne (dossier, nouveau_src, nouveau_target). En cas d'échec,
        l'état initial est restauré.
        """
        src, target = self._item(src), self._item(target)
        if src == target:
            raise StorageError(_("Impossible de regrouper une note avec elle-même."))
        if src.is_dir() or target.is_dir():
            raise StorageError(_("Seules des notes peuvent être regroupées."))

        folder = self.unique_path(target.parent, clean_name(target.stem) or _("Nouveau dossier"), "")
        try:
            folder.mkdir()
        except OSError as e:
            raise StorageError(_("Création impossible : {error}").format(error=e)) from e

        moved: list[tuple[Path, Path]] = []   # (ancien, nouveau) pour le retour arrière
        try:
            for item in (target, src):
                moved.append((item, self.move(item, folder)))
        except StorageError:
            for old, new in reversed(moved):
                try:
                    shutil.move(new, old)
                except OSError:
                    pass
            try:
                folder.rmdir()
            except OSError:
                pass
            raise
        return folder, moved[1][1], moved[0][1]

    @staticmethod
    def _same_file(a: Path, b: Path) -> bool:
        try:
            return a.samefile(b)
        except OSError:
            return False

    # ------------------------------------------------------------------ corbeille

    def move_to_trash(self, path: Path) -> TrashEntry:
        """Déplace une note ou un dossier dans `.trash/` (rien n'est effacé)."""
        path = self._item(path)
        try:
            self.trash_dir.mkdir(exist_ok=True)
        except OSError as e:
            raise StorageError(_("Corbeille inaccessible : {error}").format(error=e)) from e
        stem, ext = (path.name, "") if path.is_dir() else (path.stem, path.suffix)
        target = self.unique_path(self.trash_dir, stem, ext)
        try:
            shutil.move(path, target)
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
            names = [p.name for p in self.trash_dir.iterdir() if not p.name.startswith(".")]
        except OSError:
            return []
        index = self._read_trash_index()
        entries = []
        for name in names:
            info = index.get(name)
            info = info if isinstance(info, dict) else {}
            original = info.get("original")
            original = self.root / original if isinstance(original, str) else self.root / name
            deleted = info.get("deleted")
            entries.append(TrashEntry(original=original, trashed=self.trash_dir / name,
                                      deleted_at=deleted if isinstance(deleted, (int, float)) else 0.0))
        entries.sort(key=lambda e: (-e.deleted_at, e.trashed.name.casefold()))
        return entries

    def restore(self, entry: TrashEntry) -> Path:
        """Remet un élément de la corbeille à sa place (renommé en cas de collision)."""
        trashed = self._trashed(entry)
        original = Path(entry.original)
        # Index corrompu (hors racine, dans la corbeille) : retour à la racine.
        if not self.contains(original) or self.in_trash(self._inside(original)):
            original = self.root / trashed.name
        original = self._inside(original)
        stem, ext = (original.name, "") if trashed.is_dir() else (original.stem, original.suffix)
        try:
            original.parent.mkdir(parents=True, exist_ok=True)   # le dossier parent a pu disparaître
            target = self.unique_path(original.parent, stem, ext)
            shutil.move(trashed, target)
        except OSError as e:
            raise StorageError(_("Restauration impossible : {error}").format(error=e)) from e
        self._forget_trashed(trashed.name)
        return target

    def delete_from_trash(self, entry: TrashEntry) -> None:
        """Supprime définitivement un élément de la corbeille."""
        trashed = self._trashed(entry)
        try:
            if trashed.is_dir() and not trashed.is_symlink():
                shutil.rmtree(trashed)
            else:
                trashed.unlink()
        except OSError as e:
            raise StorageError(_("Suppression impossible : {error}").format(error=e)) from e
        self._forget_trashed(trashed.name)

    def empty_trash(self) -> int:
        """Supprime définitivement tout le contenu de la corbeille ; retourne le nombre d'éléments."""
        count = len(self.list_trash())
        if not self.trash_dir.exists():
            return 0
        try:
            shutil.rmtree(self.trash_dir)
        except OSError as e:
            raise StorageError(_("Suppression impossible : {error}").format(error=e)) from e
        return count

    def _trashed(self, entry: TrashEntry) -> Path:
        trashed = self._inside(entry.trashed)
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
            tmp.replace(path)
        except OSError:
            pass  # sans index, la restauration se fait à la racine

    def _forget_trashed(self, name: str) -> None:
        index = self._read_trash_index()
        if index.pop(name, None) is not None:
            self._write_trash_index(index)

    # ------------------------------------------------------------------ recherche plein texte

    def search_contents(self, paths: Iterable[Path], query: str) -> dict[Path, Snippet]:
        """
        Notes de `paths` dont le CONTENU contient `query` (insensible à la casse),
        avec un extrait autour de la première occurrence. Seules les notes modifiées
        depuis la recherche précédente sont relues sur le disque.
        """
        needle = query.strip()
        if not needle:
            return {}
        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        hits = {}
        for path in paths:
            text = self._cached_text(path)
            if text is None:
                continue
            match = pattern.search(text)
            if match:
                hits[path] = make_snippet(text, match.start(), match.end())
        return hits

    def _cached_text(self, path: Path) -> str | None:
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            self._text_cache.pop(path, None)
            return None
        cached = self._text_cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        self._text_cache[path] = (mtime, text)
        return text
