"""回饋：開始／結束提示音、錯誤通知。都不阻塞主流程。"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np

from . import paths
from .audio import to_wav_bytes

log = logging.getLogger(__name__)

APP_NAME = "但聞人語"
_PLAYERS = ("pw-play", "paplay", "aplay")


def _tone(freqs: tuple[float, ...], note_s: float = 0.06, sr: int = 48000) -> bytes:
    t = np.arange(int(sr * note_s)) / sr
    fade = np.minimum(1, np.minimum(t, t[::-1]) / 0.008)
    notes = [0.25 * np.sin(2 * np.pi * f * t) * fade for f in freqs]
    return to_wav_bytes(np.concatenate(notes).astype(np.float32), sr)


class Feedback:
    def __init__(self, sounds: bool = True, notify_errors: bool = True):
        self.notify_errors = notify_errors
        self._player = next((p for p in _PLAYERS if shutil.which(p)), None) if sounds else None
        self._files: dict[str, Path] = {}
        if self._player:
            paths.SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
            # 開始：上揚兩音；結束：下降兩音
            for name, freqs in (("start", (660.0, 990.0)), ("stop", (990.0, 660.0))):
                f = paths.SOUNDS_DIR / f"{name}.wav"
                if not f.exists():
                    f.write_bytes(_tone(freqs))
                self._files[name] = f

    def _play(self, name: str) -> None:
        if self._player:
            subprocess.Popen(
                [self._player, str(self._files[name])],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    def start(self) -> None:
        self._play("start")

    def stop(self) -> None:
        self._play("stop")

    def error(self, message: str) -> None:
        log.error(message)
        if self.notify_errors and shutil.which("notify-send"):
            subprocess.Popen(
                ["notify-send", f"--app-name={APP_NAME}", "--urgency=critical", APP_NAME, message],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
