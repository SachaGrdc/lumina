"""
pins.py — notes épinglées (AUCUNE dépendance à GTK).

Les épingles sont enregistrées dans `<racine>/.lumina-pins.json` sous forme de
chemins relatifs à la racine, dans l'ordre où elles ont été ajoutées. Le nom
commence par un point : le fichier n'apparaît donc pas dans l'arborescence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PINS_FILE = ".lumina-pins.json"


class PinStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._file = self.root / PINS_FILE
        self._pins: list[Path] = self._load()

    # ------------------------------------------------------------------ lecture

    def paths(self) -> list[Path]:
        return list(self._pins)

    def is_pinned(self, path: Path) -> bool:
        return Path(path) in self._pins

    # ------------------------------------------------------------------ écriture

    def pin(self, path: Path) -> None:
        path = Path(path)
        if path not in self._pins:
            self._pins.append(path)
            self._save()

    def unpin(self, path: Path) -> None:
        path = Path(path)
        if path in self._pins:
            self._pins.remove(path)
            self._save()

    def toggle(self, path: Path) -> bool:
        """Épingle ou désépingle `path` ; retourne le nouvel état."""
        if self.is_pinned(path):
            self.unpin(path)
            return False
        self.pin(path)
        return True

    def remove_under(self, path: Path) -> list[Path]:
        """Retire `path` et tout ce qu'il contient ; retourne les épingles retirées."""
        path = Path(path)
        removed = [p for p in self._pins if p == path or path in p.parents]
        if removed:
            self._pins = [p for p in self._pins if p not in removed]
            self._save()
        return removed

    def remap(self, old: Path, new: Path) -> None:
        """Suit un renommage / déplacement : les épingles situées sous `old` passent sous `new`."""
        old, new = Path(old), Path(new)
        changed = False
        for i, p in enumerate(self._pins):
            try:
                relative = p.relative_to(old)
            except ValueError:
                continue
            self._pins[i] = new / relative
            changed = True
        if changed:
            self._save()

    def prune(self) -> None:
        """Oublie les notes qui n'existent plus (supprimées en dehors de Lumina)."""
        kept = [p for p in self._pins if p.is_file()]
        if kept != self._pins:
            self._pins = kept
            self._save()

    # ------------------------------------------------------------------ disque

    def _load(self) -> list[Path]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        pins: list[Path] = []
        for rel in data:
            if isinstance(rel, str):
                path = self.root / rel
                if path not in pins:
                    pins.append(path)
        return pins

    def _save(self) -> None:
        data = []
        for p in self._pins:
            try:
                data.append(p.relative_to(self.root).as_posix())
            except ValueError:
                continue
        tmp = self._file.with_name(self._file.name + ".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._file)
        except OSError:
            pass  # les épingles restent en mémoire ; on réessaiera au prochain changement
