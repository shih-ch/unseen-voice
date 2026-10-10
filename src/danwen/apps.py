"""依目前的程式調整：貼上用哪組按鍵、整理模式改用哪張小紙條。

Wayland 不讓一般程式知道目前是哪個視窗，所以由 GNOME extension 透過 D-Bus 告訴 danwen
（只有程式代號與視窗類別，沒有視窗標題）。沒有 extension 時一律用 Ctrl+V 與使用者選的小紙條。
規則在 ~/.config/danwen/apps.yaml（沒有時用內建的），存檔即生效。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import paths
from .config import ConfigError

log = logging.getLogger(__name__)

DEFAULT_PASTE = "ctrl+v"
# none＝只放進剪貼簿，不送按鍵（貼不進去的程式用）
PASTE_KEYS = ("ctrl+v", "ctrl+shift+v", "shift+insert", "none")


@dataclass(frozen=True)
class FocusedApp:
    app_id: str  # .desktop 的名稱，例如 org.gnome.Terminal
    wm_class: str  # 視窗類別，例如 gnome-terminal-server

    def names(self) -> set[str]:
        return {n.lower() for n in (self.app_id, self.wm_class) if n}

    def __str__(self) -> str:
        return self.app_id or self.wm_class or "（不明）"


@dataclass(frozen=True)
class AppRule:
    name: str
    match: tuple[str, ...]
    paste: str | None = None
    mode: str | None = None


def default_path() -> Path:
    return paths.APPS_FILE if paths.APPS_FILE.exists() else paths.DATA_DIR / "apps.yaml"


def parse(data: object, where: str = "apps.yaml") -> list[AppRule]:
    if not isinstance(data, dict) or not isinstance(data.get("rules", []), list):
        raise ConfigError(f"{where} 應該有一個 rules 清單")
    rules = []
    for i, item in enumerate(data.get("rules") or [], 1):
        if not isinstance(item, dict):
            raise ConfigError(f"{where} 第 {i} 條規則格式錯誤")
        unknown = set(item) - {"name", "match", "paste", "mode"}
        if unknown:
            raise ConfigError(f"{where} 第 {i} 條規則有未知的項目：{'、'.join(sorted(unknown))}")
        match = item.get("match")
        match = [match] if isinstance(match, str) else match
        if not match or not all(isinstance(m, str) and m for m in match):
            raise ConfigError(f"{where} 第 {i} 條規則缺少 match（程式代號清單）")
        paste = item.get("paste")
        if paste is not None and paste not in PASTE_KEYS:
            raise ConfigError(f"{where} 第 {i} 條規則的 paste 只能是 {'、'.join(PASTE_KEYS)}，不是 {paste}")
        mode = item.get("mode")
        rules.append(AppRule(str(item.get("name") or match[0]), tuple(match), paste, str(mode) if mode else None))
    return rules


class AppRules:
    """規則檔；修改後下次使用時自動重新載入。格式錯誤時記錄錯誤，當作沒有規則。"""

    def __init__(self, path: Path | None = None):
        self._fixed_path = path
        self._loaded: tuple[Path, float | None] | None = None
        self._rules: list[AppRule] = []

    @property
    def path(self) -> Path:
        return self._fixed_path or default_path()

    def rules(self) -> list[AppRule]:
        path = self.path
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if self._loaded == (path, mtime):
            return self._rules
        self._loaded = (path, mtime)
        self._rules = []
        if mtime is not None:
            try:
                self._rules = parse(yaml.safe_load(path.read_text(encoding="utf-8")) or {}, str(path))
                log.info("載入程式規則 %s（%d 條）", path, len(self._rules))
            except (yaml.YAMLError, ConfigError) as e:
                log.error("程式規則 %s 格式錯誤，暫不套用：%s", path, e)
        return self._rules

    def lookup(self, app: FocusedApp | None) -> AppRule | None:
        if app is None:
            return None
        names = app.names()
        return next((r for r in self.rules() if any(m.lower() in names for m in r.match)), None)
