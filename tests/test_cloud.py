import base64
import json
import time

import numpy as np
import pytest

from danwen import cloud, config, refine
from danwen.asr.cloud import CloudASRBackend
from danwen.config import CloudConfig, ConfigError
from danwen.refine import RefineError

GROQ_ASR = "/openai/v1/audio/transcriptions"
GROQ_CHAT = "/openai/v1/chat/completions"
CF_BASE = "/client/v4/accounts/ACC/ai"
CF_ASR = CF_BASE + "/run/@cf/openai/whisper-large-v3-turbo"
CF_CHAT = CF_BASE + "/v1/chat/completions"
AUDIO = np.full(16000, 0.1, np.float32)


def key(provider):
    return f"key-{provider}"


def groq(url, **kw):
    return CloudConfig(provider="groq", base_url=url + "/openai/v1", **kw)


def cloudflare(url, **kw):
    return CloudConfig(provider="cloudflare", account_id="ACC",
                       base_url=url + "/client/v4/accounts/{account_id}/ai", **kw)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud, "STATE_FILE", tmp_path / "cloud")
    monkeypatch.setattr(refine, "MODE_FILE", tmp_path / "mode")
    monkeypatch.setattr(cloud, "api_key", key)  # 測試不碰真正的鑰匙圈


def test_presets_and_validation():
    ep = cloud.resolve(CloudConfig())
    assert ep.base_url == "https://api.groq.com/openai/v1" and ep.asr_model == "whisper-large-v3-turbo"
    assert ep.llm_extra == {"reasoning_effort": "low"}
    with pytest.raises(ConfigError, match="account_id"):
        cloud.resolve(CloudConfig(provider="cloudflare"))
    cf = cloud.resolve(CloudConfig(provider="cloudflare", account_id="ACC"))
    assert cf.base_url == "https://api.cloudflare.com/client/v4/accounts/ACC/ai"
    assert cf.llm_base_url == cf.base_url + "/v1" and cf.asr_api == "cloudflare"
    with pytest.raises(ConfigError, match="base_url"):
        cloud.resolve(CloudConfig(provider="custom"))
    with pytest.raises(ConfigError):
        cloud.resolve(CloudConfig(provider="nope"))


def test_enable_state_file():
    cfg = CloudConfig(enabled=False)
    assert cloud.is_enabled(cfg) is False
    cloud.set_enabled(True)
    assert cloud.is_enabled(cfg) is True
    cloud.set_enabled(False)
    assert cloud.is_enabled(CloudConfig(enabled=True)) is False  # 執行中的切換優先於設定檔


def test_groq_style_transcription(fake_http):
    server = fake_http({GROQ_ASR: (200, {"text": " 今天開會。 "})})
    backend = CloudASRBackend(groq(server.url), api_key=key, terms=lambda: ["Kubernetes"])
    assert backend.transcribe(AUDIO, 16000) == "今天開會。"
    req = server.requests[0]
    assert req["headers"]["Authorization"] == "Bearer key-groq"
    body = req["body"]
    assert b'name="model"\r\n\r\nwhisper-large-v3-turbo' in body
    assert b'name="language"\r\n\r\nzh' in body
    assert "專有名詞：Kubernetes".encode() in body
    assert b"RIFF" in body  # 附上 WAV


def test_cloudflare_transcription(fake_http):
    server = fake_http({CF_ASR: (200, {"success": True, "result": {"text": "今天開會。"}})})
    assert CloudASRBackend(cloudflare(server.url), api_key=key).transcribe(AUDIO, 16000) == "今天開會。"
    req = server.requests[0]
    assert req["headers"]["Authorization"] == "Bearer key-cloudflare"
    payload = json.loads(req["body"])
    assert base64.b64decode(payload["audio"])[:4] == b"RIFF"
    assert payload["language"] == "zh"


@pytest.mark.parametrize("status, message", [(401, "拒絕了 API Key"), (429, "用量限制"), (500, "回應錯誤")])
def test_transcription_errors_are_friendly(fake_http, status, message):
    server = fake_http({GROQ_ASR: (status, {"error": "x"})})
    with pytest.raises(cloud.CloudError, match=message):
        CloudASRBackend(groq(server.url), api_key=key).transcribe(AUDIO, 16000)


def test_cloudflare_reported_failure(fake_http):
    server = fake_http({CF_ASR: (200, {"success": False, "errors": [{"message": "bad"}]})})
    with pytest.raises(cloud.CloudError, match="Cloudflare"):
        CloudASRBackend(cloudflare(server.url), api_key=key).transcribe(AUDIO, 16000)


def test_cloud_refiner_groq_and_cloudflare(fake_http):
    answer = {"choices": [{"message": {"content": "<think>想一下</think>今天開會。"}}]}
    server = fake_http({GROQ_CHAT: (200, answer), CF_CHAT: (200, answer)})
    cfg = config.Config()
    cfg.cloud = groq(server.url)
    assert cloud.make_refiner(cfg).refine("嗯，今天開會。", "日常") == "今天開會。"  # 去掉 <think>
    sent = json.loads(server.requests[-1]["body"])
    assert server.requests[-1]["path"] == GROQ_CHAT
    assert sent["model"] == "openai/gpt-oss-20b" and sent["reasoning_effort"] == "low"
    assert server.requests[-1]["headers"]["Authorization"] == "Bearer key-groq"

    cfg.cloud = cloudflare(server.url)
    cloud.make_refiner(cfg).refine("嗯，今天開會。", "日常")
    assert server.requests[-1]["path"] == CF_CHAT
    assert json.loads(server.requests[-1]["body"])["model"] == "@cf/qwen/qwen3-30b-a3b-fp8"


def test_cloud_refiner_does_not_send_context_by_default(fake_http):
    server = fake_http({GROQ_CHAT: (200, {"choices": [{"message": {"content": "今天開會。"}}]})})
    cfg = config.Config()
    cfg.cloud = groq(server.url)
    cloud.make_refiner(cfg).refine("今天開會。", "日常", context={"clipboard": "機密"})
    assert "機密" not in server.requests[-1]["body"].decode()


def test_missing_key_becomes_refine_error(monkeypatch):
    def no_key(provider):
        raise cloud.CloudError("還沒設定")

    monkeypatch.setattr(cloud, "api_key", no_key)
    with pytest.raises(RefineError, match="還沒設定"):
        cloud.make_refiner(config.Config()).refine("今天開會。", "日常")


class LocalBackend:
    name = "sensevoice"

    def transcribe(self, audio, sr):
        return "本機的結果。"


class StubPaster:
    def __init__(self):
        self.pasted = []

    def paste(self, text):
        self.pasted.append(text)

    def close(self):
        pass


def make_daemon(tmp_path, cloud_cfg):
    from danwen.daemon import Daemon
    from danwen.history import History

    cfg = config.Config()
    cfg.feedback.sounds = False
    cfg.feedback.notify_errors = False
    cfg.cloud = cloud_cfg
    d = Daemon(cfg)
    d.backend = LocalBackend()
    d.paster = StubPaster()
    d.history = History(tmp_path / "history", size=10)
    d.cloud_asr._api_key = key
    notices = []
    d.feedback.notice = notices.append
    return d, notices


def test_daemon_uses_cloud_and_falls_back_to_local(tmp_path, fake_http):
    server = fake_http({GROQ_ASR: (200, {"text": "雲端的結果。"})})
    d, notices = make_daemon(tmp_path, groq(server.url))
    d._process(AUDIO, time.monotonic(), False)
    assert d.paster.pasted[-1] == "本機的結果。"  # 雲端沒開

    cloud.set_enabled(True)
    d._process(AUDIO, time.monotonic(), False)
    assert d.paster.pasted[-1] == "雲端的結果。"
    assert d.history.get().backend == "cloud:groq"

    server.routes[GROQ_ASR] = (503, {"error": "down"})
    d._process(AUDIO, time.monotonic(), False)
    assert d.paster.pasted[-1] == "本機的結果。"  # 雲端失敗改用本機，照樣貼上
    assert "雲端辨識失敗" in notices[-1]


def test_set_cloud_checks_key(tmp_path, monkeypatch):
    d, _ = make_daemon(tmp_path, CloudConfig())
    d.set_cloud(True)
    assert d.cloud_enabled()

    def no_key(provider):
        raise cloud.CloudError("還沒設定 Groq 的 API Key")

    monkeypatch.setattr(cloud, "api_key", no_key)
    cloud.set_enabled(False)
    with pytest.raises(cloud.CloudError, match="還沒設定"):
        d.set_cloud(True)
    assert not d.cloud_enabled()
