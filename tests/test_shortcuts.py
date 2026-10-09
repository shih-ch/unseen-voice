"""GNOME 快捷鍵：用假的 gsettings（記憶體裡的設定）測試，不碰真正的 GNOME 設定。"""

import ast
import shlex

from danwen.shortcuts import BASE_PATH, CUSTOM, MEDIA_KEYS, SHORTCUTS, ShortcutError, Shortcuts, normalize


class FakeGSettings:
    def __init__(self, custom: dict[str, dict[str, str]], builtin: dict[str, list[str]]):
        self.custom = {f"{BASE_PATH}{k}/": dict(v) for k, v in custom.items()}
        self.paths = list(self.custom)
        self.builtin = builtin  # schema → 該 schema list-recursively 的輸出行

    def __call__(self, *args):
        op, schema, *rest = args
        if schema == MEDIA_KEYS and rest[:1] == ["custom-keybindings"]:
            if op == "get":
                return repr(self.paths) if self.paths else "@as []"
            value = rest[1]
            self.paths = [] if value == "@as []" else ast.literal_eval(value)
            return ""
        if op == "list-recursively":
            if schema not in self.builtin:
                raise ShortcutError("no schema")
            return "\n".join(self.builtin[schema])
        path = schema.split(":", 1)[1]
        entry = self.custom.setdefault(path, {})
        if op == "get":
            return repr(entry.get(rest[0], ""))
        if op == "set":
            entry[rest[0]] = rest[1]
        elif op == "reset":
            entry.pop(rest[0], None)
        return ""


def existing():
    return {
        "custom0": {"name": "截圖", "binding": "<Shift>Print", "command": "gnome-screenshot"},
        "custom5": {"name": "藍牙", "binding": "<Super><Alt>3", "command": "bt"},  # 故意佔用 Super+Alt+3
    }


BUILTIN = {"org.gnome.desktop.wm.keybindings": ["org.gnome.desktop.wm.keybindings close ['<Alt>F4']",
                                                 "org.gnome.desktop.wm.keybindings x ['<Alt><Super>1']"]}


def test_normalize():
    assert normalize("<Primary><Alt>a") == normalize("<Alt><Control>A")
    assert normalize("<Super><Alt>m") != normalize("<Super>m")


def test_install_skips_taken_keys_and_keeps_existing():
    gs = FakeGSettings(existing(), BUILTIN)
    added, skipped = Shortcuts(gs).install("/home/u/.local/bin/danwen")
    reasons = {sc.binding: owner for sc, owner in skipped}
    assert "藍牙" in reasons["<Super><Alt>3"]  # 被使用者的自訂快捷鍵佔用
    assert "GNOME" in reasons["<Super><Alt>1"]  # 被內建快捷鍵佔用（修飾鍵順序不同也認得）
    assert len(added) == len(SHORTCUTS) - 2
    assert f"{BASE_PATH}custom0/" in gs.paths and f"{BASE_PATH}custom5/" in gs.paths  # 原有的還在
    nxt = gs.custom[f"{BASE_PATH}danwen-mode-next/"]
    assert nxt["command"] == "/home/u/.local/bin/danwen mode next" and nxt["binding"] == "<Super><Alt>m"


def test_install_twice_does_not_duplicate_and_remove_only_ours():
    gs = FakeGSettings(existing(), {})
    sc = Shortcuts(gs)
    sc.install("/x/danwen")
    sc.install("/x/danwen")
    assert len(gs.paths) == len(set(gs.paths)) == 2 + len(SHORTCUTS) - 1  # Super+Alt+3 被佔用
    removed = sc.remove()
    assert len(removed) == len(SHORTCUTS) - 1
    assert gs.paths == [f"{BASE_PATH}custom0/", f"{BASE_PATH}custom5/"]
    assert gs.custom[f"{BASE_PATH}custom5/"]["binding"] == "<Super><Alt>3"  # 沒被動到
    assert gs.custom[f"{BASE_PATH}danwen-mode-next/"] == {}  # 我們的已清空


def test_commands_quote_chinese_names():
    gs = FakeGSettings({}, {})
    Shortcuts(gs).install("/home/u/.local/bin/danwen")
    # GNOME 用 shell 規則解析指令；中文會被加上引號，解析後要是正確的參數
    command = gs.custom[f"{BASE_PATH}danwen-mode-2/"]["command"]
    assert shlex.split(command) == ["/home/u/.local/bin/danwen", "mode", "會議記錄"]
    assert CUSTOM.endswith("custom-keybinding")
