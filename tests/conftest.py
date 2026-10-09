import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


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
