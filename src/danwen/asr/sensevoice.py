"""Backend A：sherpa-onnx + SenseVoice-Small int8，CPU 執行，語言固定 zh。"""

from __future__ import annotations

import numpy as np

from .. import models
from ..config import SenseVoiceConfig
from .base import ASRBackend


class SenseVoiceBackend(ASRBackend):
    name = "sensevoice"

    def __init__(self, cfg: SenseVoiceConfig):
        self.cfg = cfg
        self._recognizer = None

    def prepare(self) -> None:
        if self._recognizer is not None:
            return
        import sherpa_onnx

        model_dir = models.ensure(models.SENSEVOICE)
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_dir / "model.int8.onnx"),
            tokens=str(model_dir / "tokens.txt"),
            num_threads=self.cfg.num_threads,
            language="zh",
            use_itn=self.cfg.use_itn,
        )
        # 暖機：第一次推論較慢，先在啟動時跑掉
        self.transcribe(np.zeros(16000, dtype=np.float32), 16000)

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        self.prepare()
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        self._recognizer.decode_stream(stream)
        return stream.result.text
