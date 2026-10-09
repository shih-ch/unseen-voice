import json
import stat
import time

import numpy as np
import pytest

from danwen import cli, config, paths, refine
from danwen.daemon import Daemon
from danwen.history import History


def add(h: History, text: str, **kw):
    return h.add(duration_s=1.0, backend="sensevoice", raw=text, text=text, **kw)


def test_add_list_and_prune(tmp_path):
    h = History(tmp_path, size=3)
    for i in range(5):
        add(h, f"第{i}句")
    entries = h.entries()
    assert [e.id for e in entries] == [5, 4, 3]  # 新的在前，只留 3 筆
    assert h.get().text == "第4句"
    assert h.get(3).text == "第2句"
    with pytest.raises(KeyError):
        h.get(1)


def test_files_are_private(tmp_path):
    h = History(tmp_path / "history", size=5, keep_audio=True)
    e = add(h, "秘密", audio=np.zeros(1600, np.float32))
    assert stat.S_IMODE((tmp_path / "history").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "history" / "history.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(h.audio_path(e).stat().st_mode) == 0o600


def test_audio_only_when_enabled_and_pruned_with_entry(tmp_path):
    h = History(tmp_path, size=1, keep_audio=False)
    assert add(h, "不存錄音", audio=np.zeros(1600, np.float32)).audio is None

    h = History(tmp_path / "a", size=1, keep_audio=True)
    first = add(h, "一", audio=np.zeros(1600, np.float32))
    assert h.audio_path(first).exists()
    add(h, "二", audio=np.zeros(1600, np.float32))
    assert not h.audio_path(first).exists()  # 超過保留筆數，錄音一起刪


def test_disabled_and_clear(tmp_path):
    assert add(History(tmp_path, size=0), "不保留") is None
    h = History(tmp_path / "b", size=5)
    add(h, "一")
    add(h, "二")
    assert h.clear() == 2
    assert h.entries() == []


def test_unknown_fields_are_ignored(tmp_path):
    # 之後的版本（或 GNOME extension）多加欄位時，舊版仍能讀
    (tmp_path / "history.json").write_text(
        json.dumps([{"id": 1, "time": "t", "duration_s": 1, "backend": "b", "raw": "r", "text": "t", "new": 1}]),
        encoding="utf-8",
    )
    assert History(tmp_path).get(1).raw == "r"


class FakeBackend:
    name = "fake"

    def transcribe(self, audio, sr):
        return "嗯，那個今天開會。"

    def close(self):
        pass


class StubPaster:
    def __init__(self):
        self.pasted = []

    def paste(self, text):
        self.pasted.append(text)

    def close(self):
        pass


def make_daemon(tmp_path, llm_url):
    cfg = config.Config()
    cfg.feedback.sounds = False
    cfg.feedback.notify_errors = False
    cfg.refine.base_url = llm_url
    d = Daemon(cfg)
    d.backend = FakeBackend()
    d.paster = StubPaster()
    d.history = History(tmp_path / "history", size=10)
    return d


def test_daemon_records_fast_and_refined_dictation(tmp_path, fake_llm, monkeypatch):
    monkeypatch.setattr(refine, "MODE_FILE", tmp_path / "mode")
    llm = fake_llm("今天開會。")
    d = make_daemon(tmp_path, llm.url)
    audio = np.full(16000, 0.1, np.float32)  # 1 秒、音量夠
    d._process(audio, time.monotonic(), False)
    d._process(audio, time.monotonic(), True)
    refined, fast = d.history.entries()
    assert fast.mode is None and fast.text == "嗯，那個今天開會。"
    assert refined.mode == "日常"
    assert refined.raw == "嗯，那個今天開會。" and refined.text == "今天開會。"
    assert d.paster.pasted == ["嗯，那個今天開會。", "今天開會。"]


def test_cli_redo_with_another_prompt(tmp_path, fake_llm, monkeypatch, capsys):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr(refine, "MODE_FILE", tmp_path / "mode")
    copied = []
    monkeypatch.setattr("danwen.output.Clipboard.set_text", classmethod(lambda cls, t: copied.append(t)))
    llm = fake_llm("Our meeting is today.")
    h = History(size=10)
    add(h, "嗯，那個今天開會。")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(f"refine:\n  base_url: {llm.url}\n", encoding="utf-8")

    assert cli.main(["-c", str(cfg_file), "history", "redo", "1", "-m", "英文"]) == 0
    assert copied == ["Our meeting is today."]
    new = h.get()
    assert new.id == 2 and new.redo_of == 1 and new.mode == "英文" and new.raw == "嗯，那個今天開會。"
    assert "已複製到剪貼簿" in capsys.readouterr().out


def test_daemon_reads_context_only_when_enabled(tmp_path, fake_llm, monkeypatch):
    monkeypatch.setattr(refine, "MODE_FILE", tmp_path / "mode")
    monkeypatch.setattr("danwen.output.Clipboard.get_text", staticmethod(lambda: "參加者：黃保翕" + "。" * 3000))
    monkeypatch.setattr("danwen.output.Clipboard.get_selection", staticmethod(lambda: "選取的段落"))
    llm = fake_llm("今天開會。")
    d = make_daemon(tmp_path, llm.url)
    audio = np.full(16000, 0.1, np.float32)

    d._process(audio, time.monotonic(), True)  # 預設關閉：不讀
    assert "參考資料" not in llm.requests[-1][1]["messages"][-1]["content"]

    d.cfg.refine.context_clipboard = True
    d.cfg.refine.context_max_chars = 50
    d._process(audio, time.monotonic(), True)
    last = llm.requests[-1][1]["messages"][-1]["content"]
    assert "黃保翕" in last and "選取的段落" not in last
    assert last.count("。") <= 50  # 有截斷
