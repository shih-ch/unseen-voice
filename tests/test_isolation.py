"""確認測試環境的保護有效：不讀真正的鑰匙圈、不連外部網路（曾因此用使用者的真金鑰呼叫到 Groq）。"""

import pytest

from danwen import cloud, config


def test_real_keyring_is_blocked():
    with pytest.raises(cloud.CloudError, match="測試中不讀真正的鑰匙圈"):
        cloud.api_key("groq")


def test_external_network_is_blocked(monkeypatch):
    monkeypatch.setattr(cloud, "api_key", lambda provider: "fake")  # 就算有金鑰
    refiner = cloud.make_refiner(config.Config())  # 真正的 Groq 網址
    with pytest.raises(AssertionError, match="測試不可連到外部網路"):
        refiner.refine("今天開會。", "日常")


def test_user_config_and_state_are_not_used():
    from pathlib import Path

    from danwen import paths, refine, suggest

    real = (Path.home() / ".config", Path.home() / ".local")
    for path in (paths.CONFIG_FILE, paths.REPLACEMENTS_FILE, paths.APPS_FILE, paths.HISTORY_DIR, refine.USER_PROMPTS_DIR,
                 refine.MODE_FILE, refine.LANGUAGE_FILE, cloud.STATE_FILE, cloud.MODELS_FILE, suggest.IGNORED_FILE):
        assert not path.is_relative_to(real[0]) and not path.is_relative_to(real[1]), path


def test_real_pactl_is_blocked():
    from danwen import btmic

    with pytest.raises(AssertionError, match="不可呼叫真正的 pactl"):
        btmic.BluetoothMic().acquire.__self__._pactl("set-card-profile", "bluez_card.x", "a2dp-sink")
    assert btmic.BluetoothMic().acquire() is None  # 測試環境一律當作「已經有麥克風」，不切換


def test_keyring_store_is_blocked():
    from danwen import credentials

    with pytest.raises(credentials.CredentialError, match="不讀真正的鑰匙圈"):
        credentials.KeyringStore()


def test_desktop_is_not_touched(monkeypatch):
    """切換小紙條的指令在沒有終端機時會發桌面通知：測試中不可真的發出、也不可碰剪貼簿或按鍵。"""
    import subprocess

    from danwen import cli, output, shortcuts

    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a[0]))
    assert cli.main(["mode", "next"]) == 0 and cli.main(["translate", "next"]) == 0
    assert spawned == []
    output.Clipboard.set_text("假剪貼簿")
    assert output.Clipboard.get_text() == "假剪貼簿"
    paster = output.Paster()
    paster.paste("貼上", keys="ctrl+shift+v")
    assert paster._keyboard.pressed == ["ctrl+shift+v"]
    with pytest.raises(AssertionError, match="gsettings"):
        shortcuts._gsettings("get", "org.gnome.settings-daemon.plugins.media-keys", "custom-keybindings")
