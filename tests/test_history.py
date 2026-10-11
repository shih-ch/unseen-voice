import json
import stat
import time

import numpy as np
import pytest

from danwen import cli, config, paths, refine
from danwen.daemon import Daemon, Job
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
        self.keys = []

    def paste(self, text, keys="ctrl+v"):
        self.pasted.append(text)
        self.keys.append(keys)

    def close(self):
        pass


def make_daemon(tmp_path, llm_url):
    cfg = config.Config()
    cfg.feedback.sounds = False
    cfg.feedback.notify_errors = False
    cfg.refine.base_url = llm_url
    cfg.cloud.plan = "local"  # 這裡測本機流程；方案與雲端在 test_cloud.py
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
    d._process(Job(audio, time.monotonic(), False))
    d._process(Job(audio, time.monotonic(), True))
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
    cfg_file.write_text(f"refine:\n  base_url: {llm.url}\ncloud:\n  plan: local\n", encoding="utf-8")

    assert cli.main(["-c", str(cfg_file), "history", "redo", "1", "-m", "英文"]) == 0  # 英文＝翻譯（英文）
    assert copied == ["Our meeting is today."]
    new = h.get()
    assert new.id == 2 and new.redo_of == 1 and new.mode == "翻譯（英文）" and new.raw == "嗯，那個今天開會。"
    assert "已複製到剪貼簿" in capsys.readouterr().out


def test_daemon_translation_is_pasted_without_conversion(tmp_path, fake_llm):
    llm = fake_llm("学校で会議があります。")
    d = make_daemon(tmp_path, llm.url)
    d.set_mode("日文")
    d._process(Job(np.full(16000, 0.1, np.float32), time.monotonic(), True))
    assert d.paster.pasted == ["学校で会議があります。"]  # 沒被轉成「學」「會」
    assert d.history.get().mode == "翻譯（日文）"


def test_daemon_reads_context_only_when_enabled(tmp_path, fake_llm, monkeypatch):
    monkeypatch.setattr(refine, "MODE_FILE", tmp_path / "mode")
    monkeypatch.setattr("danwen.output.Clipboard.get_text", staticmethod(lambda: "參加者：黃保翕" + "。" * 3000))
    monkeypatch.setattr("danwen.output.Clipboard.get_selection", staticmethod(lambda: "選取的段落"))
    llm = fake_llm("今天開會。")
    d = make_daemon(tmp_path, llm.url)
    audio = np.full(16000, 0.1, np.float32)

    d._process(Job(audio, time.monotonic(), True))  # 預設關閉：不讀
    assert "參考資料" not in llm.requests[-1][1]["messages"][-1]["content"]

    d.cfg.refine.context_clipboard = True
    d.cfg.refine.context_max_chars = 50
    d._process(Job(audio, time.monotonic(), True))
    last = llm.requests[-1][1]["messages"][-1]["content"]
    assert "黃保翕" in last and "選取的段落" not in last
    assert last.count("。") <= 50  # 有截斷


def test_daemon_dbus_operations(tmp_path, fake_llm, monkeypatch):
    monkeypatch.setattr(refine, "MODE_FILE", tmp_path / "mode")
    copied = []
    monkeypatch.setattr("danwen.output.Clipboard.set_text", classmethod(lambda cls, t: copied.append(t)))
    llm = fake_llm("Our meeting is today.")
    d = make_daemon(tmp_path, llm.url)
    add(d.history, "嗯，今天開會。")

    assert ("翻譯", "整理後翻譯成選定的語言") in d.list_modes()
    d.set_language("英文")
    assert d.current_mode() == "翻譯"
    assert d.mode_properties() == {"Mode": "翻譯", "ModeLabel": "翻譯（英文）", "Language": "英文"}
    assert d.list_languages() == ["英文", "日文", "簡體中文"]
    assert json.loads(d.history_json(5))[0]["text"] == "嗯，今天開會。"
    assert d.copy_history(0) == "嗯，今天開會。"
    assert d.redo(0, "") == "Our meeting is today."  # 空字串＝目前的小紙條（翻譯（英文））
    assert copied == ["嗯，今天開會。", "Our meeting is today."]
    assert d.history.get().redo_of == 1 and d.history.get().mode == "翻譯（英文）"
    with pytest.raises(RuntimeError, match="尚未就緒"):
        d.start_long()  # 監聽還沒啟動


def test_daemon_state_returns_to_idle_after_processing(tmp_path, fake_llm):
    d = make_daemon(tmp_path, fake_llm("x").url)
    d._set_state("processing")
    d._finish_processing()
    assert d.state == "idle"
    d._set_state("recording", "long")
    d._finish_processing()  # 正在錄下一段時不能被改成 idle
    assert d.state == "recording" and d.kind == "long"


class StubRecorder:
    def __init__(self):
        self.sources = []

    def start(self, source=None):
        self.sources.append(source)

    def level(self):
        return -30.0

    def stop(self):
        return np.zeros(0, np.float32)


@pytest.mark.parametrize("overlay, expect_notice", [(True, False), (False, True)])
def test_long_recording_notice_only_without_overlay(tmp_path, fake_llm, overlay, expect_notice):
    d = make_daemon(tmp_path, fake_llm("x").url)
    d.recorder = StubRecorder()
    notices = []
    d.feedback.info = notices.append
    d.dbus = type("FakeDBus", (), {"overlay_present": lambda self: overlay, "notify_state": lambda self: None,
                                    "notify_level": lambda self, *a: None})()
    from danwen.hotkey import Action

    d._on_action(Action.START_LONG)
    assert d.state == "recording" and d.kind == "long"
    assert bool(notices) is expect_notice


def test_short_long_recording_is_treated_as_accidental(tmp_path, fake_llm):
    d = make_daemon(tmp_path, fake_llm("x").url)
    one_second = np.full(16000, 0.1, np.float32)
    d._process(Job(one_second, time.monotonic(), False, "long"))  # 長錄音 1 秒：誤觸，不貼
    assert d.paster.pasted == []
    d._process(Job(one_second, time.monotonic(), False, "fast"))  # 按住錄音 1 秒：正常
    assert d.paster.pasted == ["嗯，那個今天開會。"]
    d._process(Job(np.full(32000, 0.1, np.float32), time.monotonic(), False, "long"))  # 長錄音 2 秒：正常
    assert len(d.paster.pasted) == 2


def test_search(tmp_path):
    h = History(tmp_path / "history", size=10)
    add(h, "明天下午三點開會。")
    h.add(duration_s=1.0, backend="sensevoice", raw="嗯，那個 PR 幫我 review", text="PR 幫我 review。", mode="日常")
    add(h, "週五下午交報告。")
    assert [e.text for e in h.search("下午")] == ["週五下午交報告。", "明天下午三點開會。"]  # 新的在前
    assert [e.text for e in h.search("下午 開會")] == ["明天下午三點開會。"]  # 每個關鍵字都要有
    assert [e.text for e in h.search("pr REVIEW")] == ["PR 幫我 review。"]  # 不分大小寫
    assert [e.text for e in h.search("那個")] == ["PR 幫我 review。"]  # 也比對辨識原文
    assert h.search("  ") == []


def test_cli_history_search_and_show(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    h = History(size=10)
    add(h, "明天下午三點開會。")
    add(h, "週五交報告。")
    assert cli.main(["history", "search", "開會"]) == 0
    out = capsys.readouterr().out
    assert "找到 1 筆" in out and "#1" in out and "#2" not in out
    assert cli.main(["history", "search", "沒有這個"]) == 1
    assert cli.main(["history", "show", "2"]) == 0
    assert "週五交報告。" in capsys.readouterr().out
    assert cli.main(["history", "show", "abc"]) == 2


class FakeBtMic:
    def __init__(self, result):
        self.result, self.released = result, 0

    def acquire(self, on_switching=None):
        if isinstance(self.result, Exception):
            raise self.result
        if on_switching:
            on_switching()
        return self.result

    def release(self):
        self.released += 1


def test_bluetooth_mic_source_and_release(tmp_path, fake_llm):
    from danwen.hotkey import Action

    d = make_daemon(tmp_path, fake_llm("x").url)
    d.recorder = StubRecorder()
    d.bt_mic = FakeBtMic("bluez_input.50_4F.0")
    states = []
    d.dbus = type("FakeDBus", (), {"overlay_present": lambda self: True,
                                   "notify_state": lambda self: states.append((d.state, d.kind)),
                                   "notify_level": lambda self, *a: None})()
    d._on_action(Action.START)
    assert ("recording", "preparing") in states  # 切換藍牙模式時先提示
    assert d.recorder.sources == ["bluez_input.50_4F.0"] and d.state == "recording" and d.kind == "fast"
    d._on_action(Action.STOP)
    assert d.bt_mic.released == 1


def test_no_microphone_is_reported_and_stop_is_ignored(tmp_path, fake_llm):
    from danwen.btmic import NoMicrophone
    from danwen.hotkey import Action

    d = make_daemon(tmp_path, fake_llm("x").url)
    d.recorder = StubRecorder()
    d.bt_mic = FakeBtMic(NoMicrophone("找不到麥克風"))
    errors = []
    d.feedback.error = errors.append
    d._on_action(Action.START)
    assert errors == ["找不到麥克風"] and d.state == "idle" and d.recorder.sources == []
    d._on_action(Action.STOP)  # 放開熱鍵：沒有錄音可處理
    assert d.state == "idle" and d._jobs.empty()


def test_recorder_level():
    from danwen.audio import Recorder

    r = Recorder(16000)
    assert r.level() == float("-inf")  # 還沒有聲音
    r._chunks = [np.full(1600, 0.001, np.float32)]
    quiet = r.level()
    r._chunks.append(np.full(1600, 0.1, np.float32))
    assert r.level() == pytest.approx(-20.0, abs=0.1) and quiet < -50  # 只看最近 0.1 秒


def test_level_fraction():
    from danwen.daemon import level_fraction

    assert level_fraction(float("-inf")) == 0 and level_fraction(-60) == 0
    assert level_fraction(-35) == pytest.approx(0.5) and level_fraction(0) == 1


def test_levels_are_reported_while_recording(tmp_path, fake_llm):
    from danwen.hotkey import Action

    d = make_daemon(tmp_path, fake_llm("x").url)
    d.recorder = StubRecorder()
    levels = []
    d.dbus = type("FakeDBus", (), {"overlay_present": lambda self: True, "notify_state": lambda self: None,
                                   "notify_level": lambda self, level, heard: levels.append((level, heard))})()
    d._on_action(Action.START)
    time.sleep(0.35)
    d._on_action(Action.STOP)
    count = len(levels)
    time.sleep(0.25)
    assert count >= 2 and levels[0] == (pytest.approx(0.6), True)  # -30 dBFS：條長 0.6、有聲音
    assert len(levels) == count  # 停止錄音後不再回報


def test_noise_without_speech_is_not_transcribed(tmp_path, fake_llm):
    d = make_daemon(tmp_path, fake_llm("x").url)
    d.speech = type("NoSpeech", (), {"speech_seconds": lambda self, audio, sr: 0.0})()
    notices = []
    d.feedback.notice = notices.append
    d._process(Job(np.full(32000, 0.1, np.float32), time.monotonic(), False))  # 2 秒，只有雜音
    assert d.paster.pasted == [] and d.history.entries() == []
    assert "沒有偵測到說話" in notices[0]
    d._process(Job(np.full(9000, 0.1, np.float32), time.monotonic(), False))  # 0.56 秒：多半是誤觸，不通知
    assert len(notices) == 1
