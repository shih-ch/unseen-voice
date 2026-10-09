import pytest

from danwen import config, paths


def test_missing_file_gives_defaults(tmp_path):
    assert config.load(tmp_path / "none.yaml") == config.Config()


def test_bundled_template_matches_code_defaults():
    # data/config.yaml 是給使用者看的範本，要和程式內的預設值一致
    assert config.load(paths.DATA_DIR / "config.yaml") == config.Config()


def test_partial_override(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("asr:\n  backend: whisper_server\nhotkey:\n  hold_ms: 500\n", encoding="utf-8")
    cfg = config.load(f)
    assert cfg.asr.backend == "whisper_server"
    assert cfg.hotkey.hold_ms == 500
    assert cfg.hotkey.key == "KEY_RIGHTCTRL"


def test_unknown_key_is_an_error(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("hotkey:\n  hold_msec: 500\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="hotkey.hold_msec"):
        config.load(f)


def test_bench_runs_only_local_backends_by_default(monkeypatch):
    from danwen import bench, cli

    ran = []
    monkeypatch.setattr(bench, "run", lambda cfg, inputs, backends: ran.append(backends) or 0)
    assert cli.main(["bench"]) == 0
    assert cli.main(["bench", "-b", "C"]) == 0
    assert ran == [["sensevoice", "whisper_server"], ["cloud"]]  # 雲端會上傳錄音，要明確指定
