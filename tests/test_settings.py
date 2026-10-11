"""設定畫面：改寫 config.yaml 的某一行（保留註解）、daemon 套用、字典與字典建議。"""

import json
import shutil

import pytest

from danwen import config, paths, settings
from danwen.config import ConfigError


@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "config.yaml"
    shutil.copyfile(paths.DATA_DIR / "config.yaml", path)
    return path


def test_write_changes_only_that_line(cfg_file):
    before = cfg_file.read_text(encoding="utf-8").splitlines()
    settings.write(cfg_file, "feedback.sounds", False)
    settings.write(cfg_file, "audio.bluetooth_restore_s", 8.0)
    settings.write(cfg_file, "hotkey.key", "KEY_RIGHTALT")
    after = cfg_file.read_text(encoding="utf-8").splitlines()
    changed = [(a, b) for a, b in zip(before, after) if a != b]
    assert len(before) == len(after) and len(changed) == 3  # 註解與其他行都沒動
    assert ("  sounds: true", "  sounds: false") in changed
    loaded = config.load(cfg_file)
    assert loaded.feedback.sounds is False and loaded.audio.bluetooth_restore_s == 8
    assert loaded.hotkey.key == "KEY_RIGHTALT"


def test_write_keeps_trailing_comment_and_adds_missing(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("feedback:\n  sounds: true  # 我的註解\n\n# 頂層註解\nhistory:\n  size: 20\n", encoding="utf-8")
    settings.write(path, "feedback.sounds", False)
    settings.write(path, "feedback.notify_errors", False)  # 原本沒有：加在區段裡
    settings.write(path, "output.app_rules", False)  # 整個區段都沒有：加在最後
    text = path.read_text(encoding="utf-8")
    assert "  sounds: false  # 我的註解\n  notify_errors: false\n\n# 頂層註解" in text
    assert text.endswith("output:\n  app_rules: false\n")
    loaded = config.load(path)
    assert not loaded.feedback.notify_errors and not loaded.output.app_rules and loaded.history.size == 20


def test_invalid_values_are_rejected(cfg_file):
    with pytest.raises(ConfigError):
        settings.coerce("hotkey.key", "KEY_A")  # 不在選項裡
    with pytest.raises(ConfigError):
        settings.coerce("feedback.sounds", "yes")
    with pytest.raises(ConfigError):
        settings.coerce("history.size", -1)
    with pytest.raises(ConfigError, match="不能改"):
        settings.coerce("cloud.account_id", "x")  # 設定畫面不開放的項目
    assert settings.coerce("history.size", 30.0) == 30


def test_broken_write_is_rolled_back(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("feedback:\n  sounds: true\n  typo_key: 1\n", encoding="utf-8")  # 本來就有錯
    with pytest.raises(ConfigError):
        settings.write(path, "feedback.sounds", False)
    assert path.read_text(encoding="utf-8") == "feedback:\n  sounds: true\n  typo_key: 1\n"


def make_daemon(tmp_path, cfg_file):
    from danwen.daemon import Daemon
    from danwen.history import History

    cfg = config.load(cfg_file)
    cfg.feedback.sounds = False
    cfg.cloud.plan = "local"
    d = Daemon(cfg, cfg_file)
    d.history = History(tmp_path / "history", size=10)
    return d


def test_daemon_applies_live_settings_and_tracks_restart(tmp_path, cfg_file):
    d = make_daemon(tmp_path, cfg_file)
    assert d.set_setting("output.app_rules", "false") is False  # 馬上生效
    assert d.app_rules is None and config.load(cfg_file).output.app_rules is False
    assert d.set_setting("audio.bluetooth_mic", "false") is False and d.bt_mic is None
    assert d.set_setting("hotkey.key", '"KEY_RIGHTALT"') is True  # 熱鍵要重新啟動
    assert d.restart_needed() == ["hotkey.key"]
    assert d.set_setting("hotkey.key", '"KEY_RIGHTCTRL"') is False  # 改回原本的就不用重新啟動
    info = json.loads(d.settings_json())
    assert info["values"]["output.app_rules"] is False and info["hotkeys"]["KEY_RIGHTALT"] == "右 Alt"
    with pytest.raises(ConfigError):
        d.set_setting("feedback.sounds", '"大聲"')


def test_daemon_dictionary_and_suggestions(tmp_path, cfg_file, monkeypatch):
    monkeypatch.setattr(paths, "REPLACEMENTS_FILE", tmp_path / "replacements.yaml")
    d = make_daemon(tmp_path, cfg_file)
    d.set_dictionary_entry("無邦圖", "Ubuntu")  # 使用者還沒有字典檔：先複製內建的
    assert json.loads(d.dictionary_json())["無邦圖"] == "Ubuntu"
    assert d.post("我在無邦圖上面") == "我在Ubuntu上面"  # 馬上生效
    d.set_dictionary_entry("無邦圖", "")
    assert "無邦圖" not in json.loads(d.dictionary_json())

    for raw, t in (("你上次有提過怎麼修改籤島城市", "10:00:00"), ("你上次有提過要怎麼修改前島城市", "10:00:20")):
        entry = d.history.add(duration_s=1.0, backend="sensevoice", raw=raw, text=raw)
        assert entry.id
    items = json.loads(d.dict_suggestions_json(False))
    assert [(i["wrong"], i["right"], i["two_way"]) for i in items] == [("籤島", "前島", True)]
    assert items[0]["examples"][0].startswith("你上次有提過")
    d.ignore_suggestion("籤島", "前島", "重講")
    assert json.loads(d.dict_suggestions_json(False)) == []


def test_daemon_models(tmp_path, cfg_file, fake_llm, monkeypatch):
    from danwen import cloud

    monkeypatch.setattr(cloud, "ollama_models", lambda base_url: ["qwen3:4b-instruct-2507-q4_K_M", "qwen2.5:7b"])
    d = make_daemon(tmp_path, cfg_file)
    menu = json.loads(d.models_json())
    assert menu["groq"]["kinds"]["llm"]["current"] == "qwen/qwen3.8-27b"
    assert [m["id"] for m in menu["ollama"]["kinds"]["llm"]["choices"]] == ["qwen3:4b-instruct-2507-q4_K_M", "qwen2.5:7b"]
    d.set_model("groq", "llm", "openai/gpt-oss-120b")
    assert cloud.chosen_model("groq", "llm") == "openai/gpt-oss-120b"
    d.set_model("ollama", "llm", "qwen2.5:7b")  # 本機模型寫進設定檔，馬上生效
    assert d.cfg.refine.model == "qwen2.5:7b" and config.load(cfg_file).refine.model == "qwen2.5:7b"
    with pytest.raises(ConfigError):
        d.set_model("groq", "llm", "不存在的模型")

    reply = "我們明天下午四點開會，要討論兩件事：\n1. 預算\n2. 人力"
    d.cfg.refine.base_url = fake_llm(reply).url
    result = json.loads(d.test_model("ollama"))
    assert result["output"] == reply and result["seconds"] >= 0


def test_cli_models_set_and_list(tmp_path, cfg_file, monkeypatch, capsys):
    from danwen import cli, cloud

    monkeypatch.setattr(cloud, "ollama_models", lambda base_url: [])
    assert cli.main(["-c", str(cfg_file), "models", "set", "groq", "openai/gpt-oss-120b"]) == 0
    assert cli.main(["-c", str(cfg_file), "models"]) == 0
    out = capsys.readouterr().out
    assert "＊openai/gpt-oss-120b" in out and "Cloudflare 的整理模型" in out
    assert cli.main(["-c", str(cfg_file), "models", "set", "groq", "llama-3.3-70b-versatile"]) == 1
