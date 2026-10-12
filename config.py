"""
config.py — préférences de Lumina (AUCUNE dépendance à GTK).

Les réglages sont enregistrés dans `config.json`, dans le dossier de
configuration de l'utilisateur :
  Linux   : $XDG_CONFIG_HOME/lumina/config.json  (~/.config/lumina/config.json)
  Windows : %APPDATA%\\Lumina\\config.json
L'absence de ce fichier signale un premier lancement (écran d'accueil).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from storage import DEFAULT_ROOT

LANGUAGES = ("system", "fr", "en")
THEMES = ("system", "dark", "light")
FONT_SIZE_RANGE = (8, 32)   # points


def config_dir() -> Path:
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "Lumina"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "lumina"


CONFIG_FILE = config_dir() / "config.json"


@dataclass
class Config:
    notes_dir: str = str(DEFAULT_ROOT)
    language: str = "system"      # voir LANGUAGES
    theme: str = "system"         # voir THEMES
    font_size: int = 13           # points
    line_numbers: bool = False
    # Onglets ouverts à la fermeture, rouverts au lancement : [{"path": str, "cursor": int}…]
    tabs: list = field(default_factory=list)
    active_tab: int = 0

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> tuple["Config", bool]:
        """Retourne (config, premier_lancement). Un fichier illisible donne les valeurs par défaut."""
        if not path.exists():
            return cls(), True
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(), False
        config = cls()
        if isinstance(data, dict):
            for f in fields(cls):
                value = data.get(f.name)
                if isinstance(value, type(getattr(config, f.name))):
                    setattr(config, f.name, value)
        config._validate()
        return config, False

    def save(self, path: Path = CONFIG_FILE) -> None:
        """Écriture atomique (fichier temporaire puis remplacement)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _validate(self) -> None:
        if self.language not in LANGUAGES:
            self.language = "system"
        if self.theme not in THEMES:
            self.theme = "system"
        low, high = FONT_SIZE_RANGE
        self.font_size = min(max(self.font_size, low), high)
        if not self.notes_dir.strip():
            self.notes_dir = str(DEFAULT_ROOT)
        self.tabs = [{"path": t["path"],
                      "cursor": t["cursor"] if isinstance(t.get("cursor"), int) else 0}
                     for t in self.tabs if isinstance(t, dict) and isinstance(t.get("path"), str)]
        self.active_tab = max(self.active_tab, 0)
