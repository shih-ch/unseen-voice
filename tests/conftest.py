import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture(autouse=True)
def isolate_user_environment(tmp_path, monkeypatch):
    """測試絕不碰使用者的真實環境：
    - 不讀真正的 GNOME 鑰匙圈（需要金鑰的測試自己換成假金鑰）
    - 方案、小紙條、歷史紀錄的狀態都放在暫存資料夾；不讀使用者的設定檔、替換字典與自訂小紙條
    - 只能連到本機的假服務；任何對外連線直接失敗（曾因此用真金鑰呼叫到 Groq）
    - 不呼叫真正的 pactl：曾因此把使用者的藍牙喇叭切到免持模式。預設回報「有麥克風」，其他操作一律失敗
    - 不呼叫真正的 bt-audio-profile 協調服務（它會切換真正的藍牙裝置）"""
    from danwen import btmic, cloud, dbus_service, httpclient, paths, refine
    from danwen.asr import cloud as asr_cloud

    def no_keyring(provider):
        raise cloud.CloudError("測試中不讀真正的鑰匙圈")

    monkeypatch.setattr(cloud, "api_key", no_keyring)

    from danwen import credentials

    class NoKeyring:
        def __init__(self, *args, **kwargs):
            raise credentials.CredentialError("測試中不讀真正的鑰匙圈")

    monkeypatch.setattr(credentials, "KeyringStore", NoKeyring)
    monkeypatch.setattr(cloud, "STATE_FILE", tmp_path / "isolated" / "cloud")
    monkeypatch.setattr(cloud, "MODELS_FILE", tmp_path / "isolated" / "models.json")
    monkeypatch.setattr(refine, "MODE_FILE", tmp_path / "isolated" / "mode")
    monkeypatch.setattr(refine, "LANGUAGE_FILE", tmp_path / "isolated" / "language")
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "isolated" / "history")
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / "isolated" / "config")
    monkeypatch.setattr(paths, "CONFIG_FILE", tmp_path / "isolated" / "config" / "config.yaml")
    monkeypatch.setattr(paths, "REPLACEMENTS_FILE", tmp_path / "isolated" / "config" / "replacements.yaml")
    monkeypatch.setattr(paths, "APPS_FILE", tmp_path / "isolated" / "config" / "apps.yaml")
    from danwen import suggest

    monkeypatch.setattr(suggest, "IGNORED_FILE", tmp_path / "isolated" / "dict-ignored.json")

    # 語音偵測：不載入真的模型；測試用的假錄音一律當作有說話（要測略過的測試自己換掉）
    from danwen import speech

    monkeypatch.setattr(speech.SpeechDetector, "prepare", lambda self: None)
    monkeypatch.setattr(speech.SpeechDetector, "speech_seconds", lambda self, audio, sr: audio.size / sr)
    monkeypatch.setattr(refine, "USER_PROMPTS_DIR", tmp_path / "isolated" / "config" / "prompts")

    def fake_pactl(*args):
        if args == ("get-default-source",):
            return "alsa_input.test-microphone\n"
        raise AssertionError(f"測試不可呼叫真正的 pactl：{' '.join(args)}")

    monkeypatch.setattr(btmic, "_pactl", fake_pactl)

    def no_bt_service(self, *args, **kwargs):
        raise btmic.ServiceUnavailable("測試中不呼叫真正的協調服務")

    monkeypatch.setattr(dbus_service.DBusService, "_bt_call", no_bt_service)

    real_post = httpclient.post

    def local_only_post(url, *args, **kwargs):
        host = urllib.parse.urlparse(url).hostname
        if host not in ("127.0.0.1", "localhost"):
            raise AssertionError(f"測試不可連到外部網路：{url}")
        return real_post(url, *args, **kwargs)

    for module in (httpclient, refine, asr_cloud):
        monkeypatch.setattr(module, "post", local_only_post)


class FakeLLM:
    """假的 LLM 服務：記下收到的請求，回覆預先設定的內容。"""

    def __init__(self, reply: str):
        self.reply = reply
        self.requests: list[tuple[str, dict, dict]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append((self.path, body, dict(self.headers)))
                if self.path == "/api/chat":
                    out = {"message": {"role": "assistant", "content": fake.reply}}
                elif self.path == "/v1/chat/completions":
                    out = {"choices": [{"message": {"role": "assistant", "content": fake.reply}}]}
                else:
                    out = {}
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_llm():
    servers = []

    def make(reply):
        servers.append(FakeLLM(reply))
        return servers[-1]

    yield make
    for s in servers:
        s.close()


class FakeHTTP:
    """假的 HTTP 服務：依路徑回傳預先設定的 (狀態碼, JSON)，並記下每個請求。"""

    def __init__(self, routes: dict[str, tuple[int, dict]]):
        self.routes = routes
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fake.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, payload = fake.routes.get(self.path, (404, {"error": "no route"}))
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake_http():
    servers = []

    def make(routes):
        servers.append(FakeHTTP(routes))
        return servers[-1]

    yield make
    for s in servers:
        s.close()
