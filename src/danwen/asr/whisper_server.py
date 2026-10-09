"""Backend B：透過 HTTP 呼叫 whisper.cpp server（Vulkan，large-v3-turbo q5_0）。

server 由 danwen-whisper.service 提供（install.sh --with-whisper 安裝），平常不常駐；
autostart 開啟時，第一次需要用到才啟動它。
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request

import numpy as np

from .. import models
from ..audio import resample, to_wav_bytes
from ..config import WhisperServerConfig
from ..httpclient import multipart
from .base import ASRBackend, ASRUnavailable

log = logging.getLogger(__name__)

UNIT = "danwen-whisper.service"
STARTUP_TIMEOUT_S = 120


class WhisperServerBackend(ASRBackend):
    name = "whisper_server"

    def __init__(self, cfg: WhisperServerConfig):
        self.cfg = cfg
        self.url = cfg.url.rstrip("/")
        self._started_unit = False

    def _alive(self) -> bool:
        try:
            urllib.request.urlopen(self.url + "/", timeout=1).close()
        except urllib.error.HTTPError:
            return True  # 有回應就代表 server 在跑
        except (urllib.error.URLError, OSError):
            return False
        return True

    def prepare(self) -> None:
        if self._alive():
            return
        if not self.cfg.autostart:
            raise ASRUnavailable(f"whisper.cpp server 沒有回應：{self.url}")
        state = subprocess.run(
            ["systemctl", "--user", "show", "-p", "LoadState", "--value", UNIT],
            capture_output=True, text=True,
        ).stdout.strip()
        if state != "loaded":
            raise ASRUnavailable("尚未安裝 backend B，請執行 ./install.sh --with-whisper")
        models.ensure(models.WHISPER_TURBO)
        log.info("啟動 %s", UNIT)
        subprocess.run(["systemctl", "--user", "start", UNIT], check=True)
        self._started_unit = True
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while not self._alive():
            if time.monotonic() > deadline:
                raise ASRUnavailable(f"{UNIT} 啟動逾時，請看 journalctl --user -u {UNIT}")
            time.sleep(0.5)
        # 暖機：剛啟動的 server 第一次推論要準備 GPU（Vulkan），約多花 6 秒，先在這裡跑掉
        self.transcribe(np.zeros(16000, dtype=np.float32), 16000)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        wav = to_wav_bytes(resample(audio, sample_rate, 16000), 16000)
        fields = {
            "response_format": "json",
            "temperature": "0",
            "language": self.cfg.language,
            "prompt": self.cfg.prompt,
        }
        body, content_type = multipart(fields, "file", "audio.wav", wav)
        req = urllib.request.Request(
            self.url + "/inference", data=body, headers={"Content-Type": content_type}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
                result = json.load(resp)
        except urllib.error.URLError as e:
            raise ASRUnavailable(f"whisper.cpp server 呼叫失敗：{e}") from e
        return str(result.get("text", "")).strip()

    def close(self) -> None:
        # 由我們啟動的 server 用完就關掉，不讓它常駐佔記憶體
        if self._started_unit:
            subprocess.run(["systemctl", "--user", "stop", UNIT], check=False)
            self._started_unit = False
