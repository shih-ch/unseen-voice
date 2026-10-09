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
