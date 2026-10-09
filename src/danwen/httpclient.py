"""HTTP 小工具：JSON 與 multipart 上傳（只用標準函式庫）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

from . import __version__

# 一定要帶自己的 User-Agent：Groq 等服務前面有 Cloudflare，會擋掉 Python 預設的 "Python-urllib/x.y"（error 1010）
USER_AGENT = f"danwen/{__version__}"


class HTTPError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def multipart(fields: dict[str, str], file_field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def post(url: str, body: bytes, content_type: str, timeout: float, headers: dict[str, str] | None = None) -> dict:
    """送出 POST 並解析 JSON 回應；失敗時丟出 HTTPError（附狀態碼與服務回傳的錯誤摘要）。"""
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": content_type, "User-Agent": USER_AGENT, **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise HTTPError(f"HTTP {e.code}：{detail}", e.code) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HTTPError(f"連線失敗：{getattr(e, 'reason', e)}") from e
    except json.JSONDecodeError as e:
        raise HTTPError("回應不是 JSON") from e
