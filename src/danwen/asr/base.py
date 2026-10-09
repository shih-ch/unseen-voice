"""ASR backend 介面。"""

from __future__ import annotations

import abc

import numpy as np


class ASRUnavailable(RuntimeError):
    """backend 無法使用（未安裝、server 沒回應等）；訊息會直接顯示給使用者。"""


class ASRBackend(abc.ABC):
    name: str = ""

    def prepare(self) -> None:
        """下載模型、載入、暖機。可能很慢；常駐程式啟動時呼叫一次。"""

    @abc.abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """整段辨識。audio 為 float32 單聲道，範圍 -1～1。"""

    def close(self) -> None:
        pass
