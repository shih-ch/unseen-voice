"""Backend A：sherpa-onnx + SenseVoice-Small int8，CPU 執行，語言固定 zh。

SenseVoice 適合短句；實測整段送進超過約 20 秒的錄音會漏字甚至亂碼，
所以較長的錄音先用 Silero VAD 在停頓處切段（每段最多約 15 秒），逐段辨識再接起來。
"""

from __future__ import annotations

import numpy as np

from .. import models
from ..audio import resample
from ..config import SenseVoiceConfig
from .base import ASRBackend

SR = 16000
# 超過這個長度才切段；短句維持整段辨識，最快
SEGMENT_ABOVE_S = 20.0
MAX_CHUNK_S = 15.0
_GAP = np.zeros(int(SR * 0.2), dtype=np.float32)


def _join(parts: list[str]) -> str:
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 前後都是英數字時補一個空白，避免英文單字黏在一起
        if out and out[-1].isascii() and out[-1].isalnum() and part[0].isascii() and part[0].isalnum():
            out += " "
        out += part
    return out


class SenseVoiceBackend(ASRBackend):
    name = "sensevoice"

    def __init__(self, cfg: SenseVoiceConfig):
        self.cfg = cfg
        self._recognizer = None
        self._vad = None
        self._vad_window = 512

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
        vad_cfg = sherpa_onnx.VadModelConfig()
        vad_cfg.silero_vad.model = str(models.ensure(models.SILERO_VAD) / "silero_vad.onnx")
        vad_cfg.silero_vad.min_silence_duration = 0.3
        vad_cfg.silero_vad.min_speech_duration = 0.25
        vad_cfg.silero_vad.max_speech_duration = MAX_CHUNK_S  # 一直沒停頓時強制切開
        vad_cfg.sample_rate = SR
        self._vad = sherpa_onnx.VoiceActivityDetector(vad_cfg, buffer_size_in_seconds=60)
        self._vad_window = vad_cfg.silero_vad.window_size
        # 暖機：第一次推論較慢，先在啟動時跑掉
        self._decode(np.zeros(SR, dtype=np.float32))

    def _decode(self, audio: np.ndarray) -> str:
        stream = self._recognizer.create_stream()
        stream.accept_waveform(SR, audio)
        self._recognizer.decode_stream(stream)
        return stream.result.text

    def _chunks(self, audio: np.ndarray) -> list[np.ndarray]:
        """在停頓處切段，再把相鄰的短段合併成不超過 MAX_CHUNK_S 的片段（保留一點上下文）。"""
        self._vad.reset()
        speech: list[np.ndarray] = []
        for i in range(0, audio.size, self._vad_window):
            self._vad.accept_waveform(audio[i : i + self._vad_window])
            while not self._vad.empty():
                speech.append(np.asarray(self._vad.front.samples, dtype=np.float32))
                self._vad.pop()
        self._vad.flush()
        while not self._vad.empty():
            speech.append(np.asarray(self._vad.front.samples, dtype=np.float32))
            self._vad.pop()
        chunks: list[np.ndarray] = []
        current: list[np.ndarray] = []
        length = 0
        for seg in speech:
            if current and length + seg.size > MAX_CHUNK_S * SR:
                chunks.append(np.concatenate(current))
                current, length = [], 0
            if current:
                current.append(_GAP)
                length += _GAP.size
            current.append(seg)
            length += seg.size
        if current:
            chunks.append(np.concatenate(current))
        return chunks

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        self.prepare()
        audio = resample(audio, sample_rate, SR)
        if audio.size <= SEGMENT_ABOVE_S * SR:
            return self._decode(audio)
        return _join([self._decode(chunk) for chunk in self._chunks(audio)])
