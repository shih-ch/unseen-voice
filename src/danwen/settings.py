"""設定畫面（GNOME extension 的設定頁）可以改的項目，以及改寫 config.yaml 的某一行。

只改那一行的值，其他內容（使用者寫的註解、沒列在這裡的項目）原封不動；寫入後重新載入檢查，
有錯就還原。可以馬上生效的由 daemon 套用，其他的要重新啟動 danwen。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import config as config_mod
from .config import Config, ConfigError


@dataclass(frozen=True)
class Setting:
    key: str  # 「區段.名稱」，例如 feedback.sounds
    kind: type
    live: bool  # 不必重新啟動 danwen 就生效
    choices: tuple = ()


# 熱鍵選項：右側的修飾鍵與幾個少用的鍵（不要選 fcitx5 會用到的鍵）
HOTKEYS = {
    "KEY_RIGHTCTRL": "右 Ctrl", "KEY_RIGHTALT": "右 Alt", "KEY_RIGHTSHIFT": "右 Shift",
    "KEY_RIGHTMETA": "右 Super", "KEY_CAPSLOCK": "Caps Lock", "KEY_SCROLLLOCK": "Scroll Lock",
    "KEY_PAUSE": "Pause", "KEY_COMPOSE": "Menu", "KEY_F9": "F9", "KEY_F10": "F10", "KEY_F12": "F12",
}

SETTINGS = {s.key: s for s in (
    Setting("hotkey.key", str, False, tuple(HOTKEYS)),
    Setting("feedback.sounds", bool, True),
    Setting("feedback.notify_errors", bool, True),
    Setting("long_recording.enabled", bool, False),
    Setting("long_recording.refine", bool, True),
    Setting("audio.speech_check", bool, True),
    Setting("audio.bluetooth_mic", bool, True),
    Setting("audio.bluetooth_restore_s", float, True),
    Setting("refine.model", str, True),  # 本機 Ollama 的模型
    Setting("refine.local_fallback", bool, True),
    Setting("refine.context_clipboard", bool, True),
    Setting("refine.context_selection", bool, True),
    Setting("refine.context_to_cloud", bool, True),
    Setting("refine.terms_to_cloud", bool, True),
    Setting("output.app_rules", bool, True),
    Setting("history.size", int, False),
    Setting("history.keep_audio", bool, False),
)}


def get(cfg: Config, key: str):
    section, name = key.split(".")
    return getattr(getattr(cfg, section), name)


def values(cfg: Config) -> dict:
    return {key: get(cfg, key) for key in SETTINGS}


def coerce(key: str, value):
    """檢查並轉成正確的型別；不認得的項目或不合理的值丟出 ConfigError。"""
    setting = SETTINGS.get(key)
    if setting is None:
        raise ConfigError(f"設定畫面不能改「{key}」")
    if setting.kind is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{key} 要是 true 或 false")
        return value
    if setting.kind in (int, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ConfigError(f"{key} 要是 0 以上的數字")
        return setting.kind(value)
    if setting.choices and value not in setting.choices:
        raise ConfigError(f"{key} 只能是 {'、'.join(setting.choices)}")
    return str(value)


def _render(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return yaml.safe_dump(value, allow_unicode=True).splitlines()[0]


def write(path: Path, key: str, value) -> None:
    """把 config.yaml 裡「區段: 名稱:」那一行改成新的值（保留行尾註解）；沒有就加上。
    改完重新載入檢查，有錯就還原並丟出 ConfigError。"""
    section, name = key.split(".")
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = original.splitlines()
    rendered = _render(value)
    start = next((i for i, line in enumerate(lines) if re.match(rf"^{section}:\s*(#.*)?$", line)), None)
    if start is None:
        lines += ["", f"{section}:", f"  {name}: {rendered}"]
    else:
        # 區段到下一個頂層的行（頂層項目或頂層註解）為止
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
            end += 1
        found = next((i for i in range(start + 1, end) if re.match(rf"^\s+{name}:", lines[i])), None)
        if found is not None:
            match = re.match(rf"^(\s+{name}:\s*)(.*?)(\s+#.*)?$", lines[found])
            lines[found] = match.group(1) + rendered + (match.group(3) or "")
        else:
            last = end
            while last > start + 1 and not lines[last - 1].strip():
                last -= 1  # 加在區段最後一個項目後面，不要加在空行之後
            lines.insert(last, f"  {name}: {rendered}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        loaded = config_mod.load(path)
        if get(loaded, key) != value:
            raise ConfigError(f"寫入後讀到的 {key} 不對")
    except ConfigError:
        path.write_text(original, encoding="utf-8")
        raise
