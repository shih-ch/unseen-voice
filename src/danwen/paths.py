"""XDG 目錄位置。"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "danwen"
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "danwen"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "danwen"

CONFIG_FILE = CONFIG_DIR / "config.yaml"
REPLACEMENTS_FILE = CONFIG_DIR / "replacements.yaml"
MODELS_DIR = CACHE_DIR / "models"
SOUNDS_DIR = CACHE_DIR / "sounds"
LOG_FILE = STATE_DIR / "danwen.log"

# 套件內附的預設檔（danwen init-config 會複製到 CONFIG_DIR）
DATA_DIR = Path(__file__).parent / "data"
