"""常駐流程：熱鍵 → 錄音 → ASR → 後處理 → 貼上，並記錄各階段耗時。"""

from __future__ import annotations

import logging
import queue
import signal
import threading
import time
from pathlib import Path

import numpy as np

from .asr import ASRUnavailable, create_backend
from .audio import Recorder, dbfs
from .config import Config
from .feedback import Feedback
from .hotkey import Action, HoldDetector, KeyboardListener, key_code
from .output import VKBD_NAME, Paster
from .postprocess import PostProcessor

log = logging.getLogger(__name__)


class Daemon:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.feedback = Feedback(cfg.feedback.sounds, cfg.feedback.notify_errors)
        self.recorder = Recorder(cfg.audio.sample_rate, cfg.audio.device)
        self.backend = create_backend(cfg.asr.backend, cfg.asr)
        replacements = cfg.postprocess.replacements
        self.post = PostProcessor(
            cfg.postprocess.opencc, Path(replacements).expanduser() if replacements else None
        )
        self.detector = HoldDetector(
            key_code(cfg.hotkey.key), cfg.hotkey.hold_ms / 1000, cfg.audio.max_duration_s
        )
        self.paster: Paster | None = None
        self._jobs: queue.Queue[tuple[np.ndarray, float] | None] = queue.Queue()
        self._stop = threading.Event()

    def run(self) -> None:
        t = time.monotonic()
        self.backend.prepare()
        log.info("ASR backend %s 就緒（%.1f 秒）", self.backend.name, time.monotonic() - t)
        self.paster = Paster(self.cfg.output.restore_clipboard, self.cfg.output.restore_delay_ms)
        worker = threading.Thread(target=self._worker, name="dictation", daemon=True)
        worker.start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: self._stop.set())
        listener = KeyboardListener(self.detector, self._on_action, ignore_names={VKBD_NAME})
        log.info("就緒：按住 %s 說話", self.cfg.hotkey.key)
        try:
            listener.run(self._stop)
        finally:
            self.recorder.stop()
            self._jobs.put(None)
            worker.join(timeout=5)
            self.paster.close()
            self.backend.close()

    def _on_action(self, action: Action) -> None:
        now = time.monotonic()
        if action is Action.START:
            try:
                self.recorder.start()
            except Exception as e:
                self.feedback.error(f"無法開啟麥克風：{e}")
                return
            self.feedback.start()
        elif action is Action.STOP:
            audio = self.recorder.stop()
            self.feedback.stop()
            self._jobs.put((audio, now))
        elif action is Action.CANCEL:
            self.recorder.stop()
            log.info("按住期間按了其他鍵，取消錄音")

    def _worker(self) -> None:
        while (job := self._jobs.get()) is not None:
            try:
                self._process(*job)
            except ASRUnavailable as e:
                self.feedback.error(str(e))
            except Exception as e:
                log.exception("聽寫失敗")
                self.feedback.error(f"聽寫失敗：{e}")

    def _process(self, audio: np.ndarray, released: float) -> None:
        sr = self.cfg.audio.sample_rate
        duration = audio.size / sr
        if duration < self.cfg.audio.min_duration_s:
            log.info("錄音 %.2f 秒，短於 %.1f 秒，丟棄", duration, self.cfg.audio.min_duration_s)
            return
        level = dbfs(audio)
        if level < self.cfg.audio.silence_dbfs:
            log.info("錄音音量 %.1f dBFS，視為沒講話，略過", level)
            return
        t0 = time.monotonic()
        raw = self.backend.transcribe(audio, sr)
        t1 = time.monotonic()
        text = self.post(raw)
        t2 = time.monotonic()
        if text:
            self.paster.paste(text)
        t3 = time.monotonic()
        log.info(
            "聽寫完成 錄音=%.2fs ASR=%.3fs 後處理=%.3fs 貼上=%.3fs 放開到貼上=%.3fs 字數=%d backend=%s",
            duration, t1 - t0, t2 - t1, t3 - t2, t3 - released, len(text), self.backend.name,
        )
        if self.cfg.log.log_text:
            log.info("文字：%s", text)
