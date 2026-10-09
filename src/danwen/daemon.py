"""常駐流程：熱鍵 → 錄音 → ASR → 後處理 →（整理模式：LLM 整理）→ 貼上，並記錄各階段耗時。"""

from __future__ import annotations

import logging
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from .asr import ASRUnavailable, create_backend
from .audio import Recorder, dbfs
from .config import Config, ConfigError
from .feedback import Feedback
from .history import History
from .hotkey import Action, HoldDetector, KeyboardListener, key_code
from .output import VKBD_NAME, Clipboard, Paster
from .postprocess import PostProcessor
from .refine import RefineError, Refiner, current_mode

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
            key_code(cfg.hotkey.key),
            cfg.hotkey.hold_ms / 1000,
            cfg.audio.max_duration_s,
            cfg.hotkey.double_tap_ms / 1000,
            cfg.long_recording.max_duration_s if cfg.long_recording.enabled else 0.0,
        )
        self.refiner = Refiner(cfg.refine) if cfg.hotkey.double_tap_ms > 0 else None
        self.history = (
            History(size=cfg.history.size, keep_audio=cfg.history.keep_audio) if cfg.history.size > 0 else None
        )
        self.paster: Paster | None = None
        self._refining = False
        self._jobs: queue.Queue[tuple[np.ndarray, float, bool] | None] = queue.Queue()
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
        log.info(
            "就緒：按住 %s 說話%s%s", self.cfg.hotkey.key,
            "；先短按一下再按住＝整理模式" if self.refiner else "",
            "；連按兩下＝長錄音" if self.detector.long_max_s > 0 and self.detector.double_tap_s > 0 else "",
        )
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
        if action in (Action.START, Action.START_REFINE, Action.START_LONG):
            long = action is Action.START_LONG
            wants_refine = action is Action.START_REFINE or (long and self.cfg.long_recording.refine)
            self._refining = wants_refine and self.refiner is not None
            try:
                self.recorder.start()
            except Exception as e:
                self.feedback.error(f"無法開啟麥克風：{e}")
                return
            self.feedback.start(refine=self._refining, long=long)
            if long:
                self.feedback.info("長錄音中：再按一下熱鍵結束，Esc 取消")
            if self._refining:
                self.refiner.preload()  # 說話的同時把模型載入記憶體
        elif action is Action.STOP:
            audio = self.recorder.stop()
            self.feedback.stop()
            self._jobs.put((audio, now, self._refining))
        elif action is Action.CANCEL:
            self.recorder.stop()
            log.info("錄音已取消（組合鍵或 Esc）")

    def _worker(self) -> None:
        while (job := self._jobs.get()) is not None:
            try:
                self._process(*job)
            except ASRUnavailable as e:
                self.feedback.error(str(e))
            except Exception as e:
                log.exception("聽寫失敗")
                self.feedback.error(f"聽寫失敗：{e}")

    def _context(self) -> dict[str, str] | None:
        """在貼上之前讀取剪貼簿／選取的文字當參考資料（設定開啟才讀）。只記錄字數，不記錄內容。"""
        cfg = self.cfg.refine
        if not (cfg.context_clipboard or cfg.context_selection) or not self.refiner.context_allowed():
            return None
        sources = {}
        if cfg.context_clipboard:
            sources["clipboard"] = Clipboard.get_text
        if cfg.context_selection:
            sources["selection"] = Clipboard.get_selection
        context: dict[str, str] = {}
        for name, read in sources.items():
            try:
                value = (read() or "").strip()
            except (OSError, subprocess.SubprocessError):
                continue
            if value:
                context[name] = value[: cfg.context_max_chars]
        if context:
            log.info("上下文：%s", "、".join(f"{k} {len(v)} 字" for k, v in context.items()))
        return context or None

    def _process(self, audio: np.ndarray, released: float, refine: bool) -> None:
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
        text = plain = self.post(raw)
        t2 = time.monotonic()
        mode = ""
        refined_with: str | None = None
        if refine and text:
            mode = current_mode(self.cfg.refine.mode)
            try:
                # LLM 可能輸出簡體字或「臺」，整理後再過一次轉換與替換字典
                terms = self.post.replacements.terms()
                text = self.post(self.refiner.refine(text, mode, terms, self._context()))
                refined_with = mode
            except (RefineError, ConfigError) as e:
                self.feedback.notice(f"整理失敗，已貼上原文（{e}）")
        t3 = time.monotonic()
        if text:
            self.paster.paste(text)
        t4 = time.monotonic()
        log.info(
            "聽寫完成 錄音=%.2fs ASR=%.3fs 後處理=%.3fs 整理=%.3fs 貼上=%.3fs 放開到貼上=%.3fs 字數=%d backend=%s%s",
            duration, t1 - t0, t2 - t1, t3 - t2, t4 - t3, t4 - released, len(text), self.backend.name,
            f" 小紙條={mode}" if mode else "",
        )
        if self.cfg.log.log_text:
            log.info("文字：%s", text)
        if self.history and text:
            try:
                self.history.add(
                    duration_s=duration, backend=self.backend.name, raw=plain, text=text,
                    mode=refined_with, audio=audio, sample_rate=sr,
                )
            except OSError:
                log.exception("寫入歷史紀錄失敗")
