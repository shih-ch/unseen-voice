"""依目前的程式調整：規則檔、比對、daemon 的貼法與小紙條。"""

import os
import time

import numpy as np
import pytest

from danwen import cli, config, paths, refine
from danwen.apps import AppRules, FocusedApp, parse
from danwen.config import ConfigError
from danwen.daemon import Daemon, Job
from danwen.history import History
from danwen.output import key_names

TERMINAL = FocusedApp("org.gnome.Terminal", "gnome-terminal-server")
MAIL = FocusedApp("org.mozilla.Thunderbird", "thunderbird")
EDITOR = FocusedApp("org.gnome.TextEditor", "org.gnome.TextEditor")
AUDIO = np.full(16000, 0.1, np.float32)  # 1 秒、音量夠


def write_rules(text):
    paths.APPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.APPS_FILE.write_text(text, encoding="utf-8")


def test_bundled_rules():
    rules = AppRules()
    assert rules.path == paths.DATA_DIR / "apps.yaml"  # 使用者沒有自己的檔案時用內建的
    assert rules.lookup(TERMINAL).paste == "ctrl+shift+v"
    assert rules.lookup(FocusedApp("", "kitty")).paste == "ctrl+shift+v"  # 只有視窗類別也認得
    assert rules.lookup(FocusedApp("", "KITTY")) is not None  # 不分大小寫
    assert rules.lookup(MAIL) is None  # 內建只有終端機；郵件程式的規則是註解掉的範例
    assert rules.lookup(EDITOR) is None
    assert rules.lookup(None) is None
    for rule in rules.rules():
        if rule.mode:
            refine.load_prompt(rule.mode)  # 內建規則用到的小紙條都存在


def test_user_rules_override_and_reload():
    write_rules("rules:\n  - match: org.gnome.TextEditor\n    paste: none\n")
    rules = AppRules()
    assert rules.lookup(EDITOR).paste == "none" and rules.lookup(TERMINAL) is None
    write_rules("rules:\n  - match: [kitty]\n    mode: Email\n")
    later = time.time() + 5
    os.utime(paths.APPS_FILE, (later, later))
    assert rules.lookup(EDITOR) is None  # 存檔即生效
    assert rules.lookup(FocusedApp("", "Kitty")).mode == "Email"


def test_invalid_rules():
    with pytest.raises(ConfigError, match="paste 只能是"):
        parse({"rules": [{"match": ["x"], "paste": "ctrl+p"}]})
    with pytest.raises(ConfigError, match="缺少 match"):
        parse({"rules": [{"paste": "none"}]})
    with pytest.raises(ConfigError, match="未知的項目"):
        parse({"rules": [{"match": "x", "keys": "none"}]})
    write_rules("rules: [")
    assert AppRules().rules() == []  # 格式錯誤：記錄錯誤、當作沒有規則，不影響聽寫


def test_key_names():
    assert key_names("ctrl+shift+v") == ["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_V"]
    assert key_names("shift+insert") == ["KEY_LEFTSHIFT", "KEY_INSERT"]
    with pytest.raises(ValueError):
        key_names("ctrl+p")


class FakeBackend:
    name = "sensevoice"

    def transcribe(self, audio, sr):
        return "嗯，明天開會。"


class StubPaster:
    def __init__(self):
        self.pasted, self.keys = [], []

    def paste(self, text, keys="ctrl+v"):
        self.pasted.append(text)
        self.keys.append(keys)


class FakeDBus:
    """代替 D-Bus 服務：目前的程式由測試指定（真正的由 GNOME extension 提供）。"""

    def __init__(self, app):
        self.app = app

    def focused_app(self):
        return self.app

    def notify_state(self):
        pass

    def notify_history(self):
        pass


def make_daemon(tmp_path, fake_llm, app, reply="明天開會。"):
    cfg = config.Config()
    cfg.feedback.sounds = False
    cfg.feedback.notify_errors = False
    cfg.refine.base_url = fake_llm(reply).url
    cfg.cloud.plan = "local"
    d = Daemon(cfg)
    d.backend = FakeBackend()
    d.paster = StubPaster()
    d.history = History(tmp_path / "history", size=10)
    d.dbus = FakeDBus(app)
    return d


def test_terminal_gets_ctrl_shift_v(tmp_path, fake_llm):
    d = make_daemon(tmp_path, fake_llm, TERMINAL)
    d._process(Job(AUDIO, time.monotonic(), False))
    assert d.paster.keys == ["ctrl+shift+v"]
    d.dbus.app = EDITOR  # 一般程式
    d._process(Job(AUDIO, time.monotonic(), False))
    assert d.paster.keys[-1] == "ctrl+v"
    d.dbus = None  # 沒有 extension
    d._process(Job(AUDIO, time.monotonic(), False))
    assert d.paster.keys[-1] == "ctrl+v"


def test_paste_none_leaves_text_in_clipboard_and_notifies(tmp_path, fake_llm):
    write_rules("rules:\n  - match: org.gnome.TextEditor\n    paste: none\n")
    d = make_daemon(tmp_path, fake_llm, EDITOR)
    notices = []
    d.feedback.notice = notices.append
    d._process(Job(AUDIO, time.monotonic(), False))
    assert d.paster.keys == ["none"] and "請自己貼上" in notices[0]


def test_app_rule_switches_prompt_only_from_default(tmp_path, fake_llm):
    write_rules("rules:\n  - match: [thunderbird]\n    mode: Email\n")
    d = make_daemon(tmp_path, fake_llm, MAIL)
    assert d._choose_mode() == ("Email", True)  # 目前是預設小紙條（日常）：依程式換
    refine.set_mode("翻譯")
    assert d._choose_mode() == ("翻譯（英文）", False)  # 手動選了別張：照使用者選的
    refine.set_mode("日常")
    d.dbus.app = EDITOR
    assert d._choose_mode() == ("日常", False)
    d.app_rules = None  # output.app_rules: false
    d.dbus.app = MAIL
    assert d._choose_mode() == ("日常", False)


def test_refined_job_uses_mode_chosen_at_start(tmp_path, fake_llm):
    d = make_daemon(tmp_path, fake_llm, MAIL, reply="明天的會議改到四點。")
    d._process(Job(AUDIO, time.monotonic(), True, mode="Email"))
    assert d.history.get().mode == "Email"
    assert d.paster.pasted == ["明天的會議改到四點。"]


def test_cli_apps_lists_rules(capsys):
    assert cli.main(["apps"]) == 0
    out = capsys.readouterr().out
    assert "終端機：貼上用 ctrl+shift+v" in out
