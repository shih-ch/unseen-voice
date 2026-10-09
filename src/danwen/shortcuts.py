"""GNOME 自訂快捷鍵：切換小紙條（`danwen shortcuts install／remove／status`）。

只新增、移除 danwen 自己的項目（路徑名稱以 danwen- 開頭），不動使用者原有的自訂快捷鍵。
安裝前檢查按鍵有沒有被 GNOME 內建或其他自訂快捷鍵佔用，佔用的就略過。
"""

from __future__ import annotations

import ast
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM = MEDIA_KEYS + ".custom-keybinding"
BASE_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
PREFIX = "danwen-"
# 檢查衝突時要看的內建快捷鍵
BUILTIN_SCHEMAS = (
    "org.gnome.desktop.wm.keybindings",
    "org.gnome.shell.keybindings",
    "org.gnome.mutter.keybindings",
    "org.gnome.mutter.wayland.keybindings",
    MEDIA_KEYS,
)


@dataclass(frozen=True)
class Shortcut:
    key: str  # 路徑名稱（danwen-xxx）
    name: str
    binding: str
    args: tuple[str, ...]  # danwen 之後的參數


SHORTCUTS = (
    Shortcut("danwen-mode-next", "但聞人語：下一張小紙條", "<Super><Alt>m", ("mode", "next")),
    Shortcut("danwen-mode-1", "但聞人語：小紙條 日常", "<Super><Alt>1", ("mode", "日常")),
    Shortcut("danwen-mode-2", "但聞人語：小紙條 會議記錄", "<Super><Alt>2", ("mode", "會議記錄")),
    Shortcut("danwen-mode-3", "但聞人語：小紙條 Email", "<Super><Alt>3", ("mode", "Email")),
    Shortcut("danwen-mode-4", "但聞人語：小紙條 Slack", "<Super><Alt>4", ("mode", "Slack")),
    Shortcut("danwen-mode-5", "但聞人語：小紙條 英文", "<Super><Alt>5", ("mode", "英文")),
)


class ShortcutError(RuntimeError):
    pass


def _gsettings(*args: str) -> str:
    try:
        result = subprocess.run(["gsettings", *args], capture_output=True, text=True, timeout=10)
    except FileNotFoundError as e:
        raise ShortcutError("找不到 gsettings（不是 GNOME 桌面？）") from e
    if result.returncode != 0:
        raise ShortcutError(f"gsettings {' '.join(args)} 失敗：{result.stderr.strip()}")
    return result.stdout.strip()


def _parse_strv(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("@as"):
        raw = raw[3:].strip()
    return list(ast.literal_eval(raw))


def _format_strv(items: list[str]) -> str:
    return "[" + ", ".join(repr(i) for i in items) + "]" if items else "@as []"


def normalize(binding: str) -> str:
    """比對用：不分大小寫，<Primary> 與 <Control>、<Ctrl> 視為相同，修飾鍵順序不影響。"""
    text = binding.lower().replace("<primary>", "<control>").replace("<ctrl>", "<control>")
    mods = sorted(part + ">" for part in text.split(">")[:-1])
    return "".join(mods) + text.split(">")[-1]


def danwen_command() -> str:
    """快捷鍵要用的 danwen 完整路徑（GNOME 執行快捷鍵時的 PATH 通常沒有 ~/.local/bin）。"""
    found = shutil.which("danwen")
    local = Path.home() / ".local/bin/danwen"
    if local.exists():
        return str(local)
    if found:
        return found
    raise ShortcutError("找不到已安裝的 danwen 指令，請先執行 install.sh")


class Shortcuts:
    def __init__(self, gsettings: Callable[..., str] = _gsettings):
        self._gs = gsettings

    def _paths(self) -> list[str]:
        return _parse_strv(self._gs("get", MEDIA_KEYS, "custom-keybindings"))

    def _set_paths(self, paths: list[str]) -> None:
        self._gs("set", MEDIA_KEYS, "custom-keybindings", _format_strv(paths))

    def _get(self, path: str, key: str) -> str:
        return ast.literal_eval(self._gs("get", f"{CUSTOM}:{path}", key))

    def taken(self) -> dict[str, str]:
        """目前已被使用的按鍵（正規化後）→ 用途。不含 danwen 自己的項目。"""
        used: dict[str, str] = {}
        for schema in BUILTIN_SCHEMAS:
            try:
                listing = self._gs("list-recursively", schema)
            except ShortcutError:
                continue  # 這台機器沒有這個 schema
            for line in listing.splitlines():
                parts = line.split(" ", 2)
                if len(parts) < 3 or "<" not in parts[2]:
                    continue
                try:
                    values = ast.literal_eval(parts[2].replace("@as ", ""))
                except (ValueError, SyntaxError):
                    continue
                for value in values if isinstance(values, list) else [values]:
                    if isinstance(value, str) and value:
                        used.setdefault(normalize(value), f"GNOME：{parts[1]}")
        for path in self._paths():
            if PREFIX in path:
                continue
            binding = self._get(path, "binding")
            if binding:
                used[normalize(binding)] = f"自訂快捷鍵：{self._get(path, 'name')}"
        return used

    def install(self, command: str) -> tuple[list[Shortcut], list[tuple[Shortcut, str]]]:
        """新增 danwen 的快捷鍵；回傳（已設定的, [(略過的, 原因)]）。重複執行會更新指令與按鍵。"""
        used = self.taken()
        paths = self._paths()
        added, skipped = [], []
        for sc in SHORTCUTS:
            path = f"{BASE_PATH}{sc.key}/"
            owner = used.get(normalize(sc.binding))
            if owner:
                skipped.append((sc, owner))
                continue
            schema = f"{CUSTOM}:{path}"
            self._gs("set", schema, "name", sc.name)
            self._gs("set", schema, "command", " ".join(shlex.quote(p) for p in (command, *sc.args)))
            self._gs("set", schema, "binding", sc.binding)
            if path not in paths:
                paths.append(path)
            added.append(sc)
        self._set_paths(paths)
        return added, skipped

    def remove(self) -> list[str]:
        """移除 danwen 的快捷鍵（只動路徑名稱以 danwen- 開頭的項目）；回傳移除的名稱。"""
        paths = self._paths()
        ours = [p for p in paths if p.rstrip("/").rsplit("/", 1)[-1].startswith(PREFIX)]
        removed = []
        for path in ours:
            removed.append(self._get(path, "name"))
            for key in ("name", "command", "binding"):
                self._gs("reset", f"{CUSTOM}:{path}", key)
        self._set_paths([p for p in paths if p not in ours])
        return removed

    def status(self) -> list[tuple[str, str, str]]:
        """danwen 目前設定的快捷鍵：（名稱, 按鍵, 指令）。"""
        rows = []
        for path in self._paths():
            if path.rstrip("/").rsplit("/", 1)[-1].startswith(PREFIX):
                rows.append((self._get(path, "name"), self._get(path, "binding"), self._get(path, "command")))
        return rows
