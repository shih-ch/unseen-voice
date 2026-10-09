"""常駐流程：熱鍵 → 錄音 → ASR → 後處理 →（整理模式：LLM 整理）→ 貼上，並記錄各階段耗時。

狀態（idle／recording／processing）與歷史紀錄、小紙條、長錄音的操作經 D-Bus 對外提供，給 GNOME extension 使用。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import queue
import signal
import subprocess
import threading
import time
from pathlib import Path

import numpy as np

from . import cloud
from .asr import ASRUnavailable, create_backend
from .asr.cloud import CloudASRBackend
from .audio import Recorder, dbfs
from .config import Config, ConfigError
from .feedback import Feedback
from .history import History, redo_entry
from .hotkey import Action, HoldDetector, KeyboardListener, display_name, key_code
from .output import VKBD_NAME, Clipboard, Paster
from .postprocess import PostProcessor
from .refine import RefineError, Refiner, available_prompts, current_mode, load_prompt, set_mode

log = logging.getLogger(__name__)


class Daemon:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.feedback = Feedback(cfg.feedback.sounds, cfg.feedback.notify_errors)
        self.recorder = Recorder(cfg.audio.sample_rate, cfg.audio.device)
        self.backend = create_backend(cfg.asr.backend, cfg)
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
        # 雲端（預設關閉）：本機的 backend 與 Refiner 一直保留，雲端失敗時改用本機
        self.cloud_asr = CloudASRBackend(cfg.cloud, terms=self.post.replacements.terms)
        self.history = (
            History(size=cfg.history.size, keep_audio=cfg.history.keep_audio) if cfg.history.size > 0 else None
        )
        self.paster: Paster | None = None
        self.dbus = None
        self.state = "idle"  # idle / recording / processing
        self.kind = ""  # fast / refine / long
        self._state_lock = threading.Lock()
        self._listener: KeyboardListener | None = None
        self._refining = False
        self._jobs: queue.Queue[tuple[np.ndarray, float, bool, str] | None] = queue.Queue()
        self._stop = threading.Event()

    def _start_dbus(self) -> None:
        from .dbus_service import AlreadyRunning, DBusService

        service = DBusService(self)
        try:
            service.start()
        except AlreadyRunning:
            raise
        except Exception as e:
            log.warning("D-Bus 介面無法啟用（GNOME extension 會連不上），聽寫不受影響：%s", e)
            return
        self.dbus = service

    def _watch_settings(self) -> None:
        last = None
        while not self._stop.wait(1.0):
            current = {"Mode": self.current_mode(), "Cloud": self.cloud_enabled()}
            if last is not None and current != last:
                self.dbus.notify_properties({k: v for k, v in current.items() if last.get(k) != v})
            last = current

    def _set_state(self, state: str, kind: str | None = None) -> None:
        with self._state_lock:
            self.state = state
            if kind is not None:
                self.kind = kind
        if self.dbus:
            self.dbus.notify_state()

    def _finish_processing(self) -> None:
        with self._state_lock:
            # 處理期間又開始新的錄音，或還有排隊的工作時，維持原狀態
            if self.state != "processing" or not self._jobs.empty():
                return
            self.state = "idle"
        if self.dbus:
            self.dbus.notify_state()

    def run(self) -> None:
        self._start_dbus()  # 先確認沒有另一個 danwen 在執行
        t = time.monotonic()
        self.backend.prepare()
        log.info("ASR backend %s 就緒（%.1f 秒）", self.backend.name, time.monotonic() - t)
        self.paster = Paster(self.cfg.output.restore_clipboard, self.cfg.output.restore_delay_ms)
        worker = threading.Thread(target=self._worker, name="dictation", daemon=True)
        worker.start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: self._stop.set())
        listener = KeyboardListener(self.detector, self._on_action, ignore_names={VKBD_NAME})
        self._listener = listener
        if self.dbus:
            threading.Thread(target=self._watch_settings, name="watch", daemon=True).start()
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
            if self.dbus:
                self.dbus.stop()

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
                self._set_state("idle")
                return
            self._set_state("recording", "long" if long else "refine" if self._refining else "fast")
            self.feedback.start(refine=self._refining, long=long)
            # 有 GNOME extension 時畫面上已有錄音提示，不再另外跳通知
            if long and not (self.dbus and self.dbus.overlay_present()):
                self.feedback.info(f"長錄音中：再按一下 {display_name(self.cfg.hotkey.key)} 結束，Esc 取消")
            if self._refining and not self._cloud_refine():
                self.refiner.preload()  # 說話的同時把模型載入記憶體
        elif action is Action.STOP:
            audio = self.recorder.stop()
            self.feedback.stop()
            self._set_state("processing")
            self._jobs.put((audio, now, self._refining, self.kind))
        elif action is Action.CANCEL:
            self.recorder.stop()
            self._set_state("idle")
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
            finally:
                self._finish_processing()

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

    # ---- 雲端 ----

    def cloud_enabled(self) -> bool:
        return cloud.is_enabled(self.cfg.cloud)

    def cloud_label(self) -> str:
        return cloud.label(self.cfg.cloud)

    def set_cloud(self, on: bool) -> None:
        if on:
            cloud.check_ready(self.cfg.cloud)  # 免得開了才發現不能用
        cloud.set_enabled(on)
        log.info("雲端：%s（%s）", "開" if on else "關", self.cloud_label())

    def _cloud_refine(self) -> bool:
        return self.cloud_enabled() and self.cfg.cloud.use_for_refine

    def _transcribe(self, audio: np.ndarray, sr: int) -> tuple[str, str]:
        """雲端開啟時先用雲端辨識，失敗就改用本機（本機模型一直保持載入）。"""
        if self.cloud_enabled() and self.cfg.cloud.use_for_asr:
            try:
                return self.cloud_asr.transcribe(audio, sr), self.cloud_asr.name
            except (cloud.CloudError, ConfigError) as e:
                self.feedback.notice(f"雲端辨識失敗，改用本機（{e}）")
        return self.backend.transcribe(audio, sr), self.backend.name

    def _process(self, audio: np.ndarray, released: float, refine: bool, kind: str = "fast") -> None:
        sr = self.cfg.audio.sample_rate
        duration = audio.size / sr
        # 長錄音不必按著，連按想停止時很容易又開始一段很短的；這種當作誤觸
        minimum = self.cfg.long_recording.min_duration_s if kind == "long" else self.cfg.audio.min_duration_s
        if duration < minimum:
            log.info("錄音 %.2f 秒，短於 %.1f 秒，視為誤觸丟棄", duration, minimum)
            return
        level = dbfs(audio)
        if level < self.cfg.audio.silence_dbfs:
            log.info("錄音音量 %.1f dBFS，視為沒講話，略過", level)
            return
        t0 = time.monotonic()
        raw, asr_name = self._transcribe(audio, sr)
        t1 = time.monotonic()
        text = plain = self.post(raw)
        t2 = time.monotonic()
        mode = ""
        refined_with: str | None = None
        if refine and text:
            mode = current_mode(self.cfg.refine.mode)
            try:
                refiner = cloud.make_refiner(self.cfg) if self._cloud_refine() else self.refiner
                # LLM 可能輸出簡體字或「臺」，整理後再過一次轉換與替換字典
                terms = self.post.replacements.terms()
                text = self.post(refiner.refine(text, mode, terms, self._context()))
                refined_with = mode
            except (RefineError, ConfigError) as e:
                self.feedback.notice(f"整理失敗，已貼上原文（{e}）")
        t3 = time.monotonic()
        if text:
            self.paster.paste(text)
        t4 = time.monotonic()
        log.info(
            "聽寫完成 錄音=%.2fs ASR=%.3fs 後處理=%.3fs 整理=%.3fs 貼上=%.3fs 放開到貼上=%.3fs 字數=%d backend=%s%s",
            duration, t1 - t0, t2 - t1, t3 - t2, t4 - t3, t4 - released, len(text), asr_name,
            f" 小紙條={mode}{'（雲端）' if self._cloud_refine() else ''}" if mode else "",
        )
        if self.cfg.log.log_text:
            log.info("文字：%s", text)
        if self.history and text:
            try:
                self.history.add(
                    duration_s=duration, backend=asr_name, raw=plain, text=text,
                    mode=refined_with, audio=audio, sample_rate=sr,
                )
            except OSError:
                log.exception("寫入歷史紀錄失敗")
            else:
                if self.dbus:
                    self.dbus.notify_history()

    # ---- 給 D-Bus（GNOME extension）用的操作 ----

    def current_mode(self) -> str:
        return current_mode(self.cfg.refine.mode)

    def list_modes(self) -> list[tuple[str, str]]:
        modes = []
        for name in available_prompts():
            try:
                description = load_prompt(name).description
            except ConfigError:
                description = "（格式錯誤）"
            modes.append((name, description))
        return modes

    def set_mode(self, name: str) -> None:
        set_mode(name)

    def _require_history(self) -> History:
        if self.history is None:
            raise RuntimeError("歷史紀錄未啟用（history.size 為 0）")
        return self.history

    def history_json(self, limit: int) -> str:
        if self.history is None:
            return "[]"
        entries = self.history.entries()
        if limit > 0:
            entries = entries[:limit]
        return json.dumps([dataclasses.asdict(e) for e in entries], ensure_ascii=False)

    def copy_history(self, entry_id: int) -> str:
        entry = self._require_history().get(entry_id or None)
        Clipboard.set_text(entry.text)
        return entry.text

    def redo(self, entry_id: int, mode: str) -> str:
        if self.refiner is None:
            raise RuntimeError("整理模式未啟用（hotkey.double_tap_ms 為 0）")
        terms = self.post.replacements.terms()
        result, _ = redo_entry(
            self._require_history(), entry_id, mode or self.current_mode(),
            lambda raw, m: self.post(self.refiner.refine(raw, m, terms)),
        )
        Clipboard.set_text(result)
        if self.dbus:
            self.dbus.notify_history()
        return result

    def _post(self, command) -> None:
        if self._listener is None:
            raise RuntimeError("danwen 尚未就緒")
        self._listener.post(command)

    def start_long(self) -> None:
        self._post(lambda detector, now: detector.start_long(now))

    def stop_recording(self) -> None:
        self._post(lambda detector, now: detector.stop())

    def cancel_recording(self) -> None:
        self._post(lambda detector, now: detector.cancel())
