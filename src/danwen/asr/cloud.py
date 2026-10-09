"""Backend C：雲端語音辨識。

- Groq、OpenAI、custom：OpenAI 相容的 /audio/transcriptions（multipart 上傳 WAV）
- Cloudflare Workers AI：/run/@cf/openai/whisper-large-v3-turbo（JSON，音檔以 base64 放在 audio 欄位）
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable

import numpy as np

from .. import cloud
from ..audio import resample, to_wav_bytes
from ..config import CloudConfig
from ..httpclient import HTTPError, multipart, post
from .base import ASRBackend


def friendly_error(error: HTTPError, service: str) -> cloud.CloudError:
    if error.status in (401, 403):
        return cloud.CloudError(f"{service} 拒絕了 API Key（金鑰錯誤或權限不足）")
    if error.status == 429:
        return cloud.CloudError(f"{service} 回報超過用量限制，請稍後再試")
    if error.status == 413:
        return cloud.CloudError(f"錄音太大，超過 {service} 的上限")
    if error.status is None:
        return cloud.CloudError(f"連不到 {service}（{error}）")
    return cloud.CloudError(f"{service} 回應錯誤（{error}）")


class CloudASRBackend(ASRBackend):
    def __init__(
        self,
        cfg: CloudConfig,
        api_key: Callable[[str], str] | None = None,
        terms: Callable[[], list[str]] | None = None,
    ):
        self.cfg = cfg
        self._api_key = api_key
        self._terms = terms

    @property
    def name(self) -> str:
        return f"cloud:{self.cfg.provider}"

    def prepare(self) -> None:
        cloud.resolve(self.cfg)  # 只檢查設定是否完整，不連線

    def _prompt(self) -> str:
        terms = self._terms() if self._terms else []
        return self.cfg.asr_prompt + (f" 專有名詞：{'、'.join(terms)}" if terms else "")

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        endpoint = cloud.resolve(self.cfg)
        wav = to_wav_bytes(resample(audio, sample_rate, 16000), 16000)
        key = (self._api_key or cloud.api_key)(endpoint.provider)  # 預設從鑰匙圈讀，每次呼叫時才讀
        headers = {"Authorization": f"Bearer {key}"}
        try:
            if endpoint.asr_api == "cloudflare":
                body = json.dumps(
                    {"audio": base64.b64encode(wav).decode(), "language": "zh", "initial_prompt": self._prompt()}
                ).encode()
                result = post(f"{endpoint.base_url}/run/{endpoint.asr_model}", body, "application/json",
                              self.cfg.timeout_s, headers)
                if not result.get("success", True):
                    raise cloud.CloudError(f"Cloudflare 回報錯誤：{result.get('errors')}")
                return str((result.get("result") or {}).get("text", "")).strip()
            fields = {
                "model": endpoint.asr_model,
                "language": "zh",
                "prompt": self._prompt(),
                "response_format": "json",
                "temperature": "0",
            }
            body, content_type = multipart(fields, "file", "audio.wav", wav)
            result = post(f"{endpoint.base_url}/audio/transcriptions", body, content_type,
                          self.cfg.timeout_s, headers)
            return str(result.get("text", "")).strip()
        except HTTPError as e:
            raise friendly_error(e, endpoint.label) from e
