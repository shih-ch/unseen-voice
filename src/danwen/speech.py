"""辨識前先確認錄音裡有沒有人在說話（Silero VAD）。

只看音量不夠：藍牙喇叭的免持麥克風、環境雜音常常高於「當作沒講話」的音量門檻，送去辨識後
SenseVoice 會吐出「我。」「。」，雲端 Whisper 甚至會編出歌詞字幕。沒偵測到說話就略過，不辨識、不上傳。
"""

from __future__ import annotations

import logging

import numpy as np

from . import models
from .audio import resample

log = logging.getLogger(__name__)

SR = 16000


class SpeechDetector:
    def __init__(self):
        self._vad = None
        self._window = 512

    def prepare(self) -> None:
        if self._vad is not None:
            return
        import sherpa_onnx

        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = str(models.ensure(models.SILERO_VAD) / "silero_vad.onnx")
        cfg.silero_vad.min_silence_duration = 0.3
        cfg.silero_vad.min_speech_duration = 0.1  # 單音節（「好」「對」）也要算
        cfg.sample_rate = SR
        self._vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)
        self._window = cfg.silero_vad.window_size

    def speech_seconds(self, audio: np.ndarray, sample_rate: int) -> float:
        """錄音裡偵測到說話的總秒數。"""
        self.prepare()
        audio = resample(audio, sample_rate, SR)
        self._vad.reset()
        total = 0
        for i in range(0, audio.size, self._window):
            self._vad.accept_waveform(audio[i : i + self._window])
            while not self._vad.empty():
                total += len(self._vad.front.samples)
                self._vad.pop()
        self._vad.flush()
        while not self._vad.empty():
            total += len(self._vad.front.samples)
            self._vad.pop()
        return total / SR
