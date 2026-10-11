"""藍牙耳機／喇叭的麥克風：沒有其他麥克風時，錄音期間切到免持模式，錄完再切回高音質播放。

藍牙音訊裝置一次只能用一種模式：A2DP（高音質播放，沒有麥克風）或免持（HSP/HFP，有麥克風但音質較差）。
系統（WirePlumber）只替通話類程式自動切換，所以由 danwen 自己切：
- 開始錄音前看系統預設輸入：是真的麥克風就照常錄，不做任何切換
- 預設輸入只是某個喇叭的播放聲音（.monitor，表示沒有麥克風）時，找一個有免持模式的藍牙裝置
  （優先用目前正在播放聲音的那一個）切過去，等它的麥克風出現才開始錄（約 1～2 秒）
- 錄完幾秒後切回原本的模式；期間又開始錄音就直接用，不必再等。使用者自己換過模式就不動
- 只對 danwen 自己的錄音指定麥克風（PULSE_SOURCE），不改系統的預設輸入
- 有其他程式還在用藍牙麥克風時不切回（例如系統替 Chrome 視訊切到免持）
都透過 pactl（PipeWire 的 PulseAudio 相容指令）。

有安裝 bt-audio-profile 的協調服務（D-Bus：io.github.shih_ch.BtAudio）時改由它切換：
danwen、其他程式與手動切換都經過同一個地方，不會互相打架。沒有時用這裡內建的做法。
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


# bt-audio-profile 的協調服務（選用）
SERVICE_BUS_NAME = "io.github.shih_ch.BtAudio"
SERVICE_OBJECT_PATH = "/io/github/shih_ch/BtAudio"
SERVICE_INTERFACE = "io.github.shih_ch.BtAudio1"
RETRY_S = 5.0  # 其他程式還在用藍牙麥克風時，隔多久再試著切回


class NoMicrophone(RuntimeError):
    pass


class ServiceUnavailable(RuntimeError):
    """沒有安裝協調服務，或它沒有回應：改用內建的做法。"""


def _pactl(*args: str) -> str:
    result = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        raise OSError(f"pactl {' '.join(args)} 失敗：{result.stderr.strip()}")
    return result.stdout


def _address(name: str) -> str:
    """bluez_card.50_4F_3B_8A_04_C2、bluez_output.50_4F_3B_8A_04_C2.1 → 50_4F_3B_8A_04_C2。"""
    return name.split(".")[1] if name.startswith("bluez_") and "." in name else ""


def headset_profile(card: dict) -> str | None:
    """有麥克風（sources ≥ 1）的可用模式中優先權最高的（通常是 mSBC，16 kHz 寬頻）。"""
    choices = [
        (info.get("priority", 0), name)
        for name, info in (card.get("profiles") or {}).items()
        if info.get("sources", 0) >= 1 and info.get("available", True)
    ]
    return max(choices)[1] if choices else None


class BluetoothMic:
    def __init__(
        self,
        restore_after_s: float = 5.0,
        timeout_s: float = 4.0,
        pactl: Callable[..., str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        remote: Callable[[], object] | None = None,
    ):
        """remote：取得協調服務用戶端的函式（danwen 的 D-Bus 服務，有 bt_acquire／bt_release）；None＝不使用。"""
        self.restore_after_s = restore_after_s
        self.timeout_s = timeout_s
        self._pactl_fn = pactl
        self._sleep = sleep
        self._lock = threading.RLock()
        self._switched: tuple[str, str, str] | None = None  # （裝置, 原本的模式, 切過去的免持模式）
        self._timer: threading.Timer | None = None
        self._remote = remote
        self._remote_held = None  # 目前向協調服務借用中

    def _pactl(self, *args: str) -> str:
        # 每次才查模組的 _pactl：測試會把它換成假的，確保絕不動到真正的音訊裝置
        return (self._pactl_fn or _pactl)(*args)

    # ---- 查詢 ----

    def _sources(self) -> list[str]:
        return [s["name"] for s in json.loads(self._pactl("-f", "json", "list", "sources"))]

    def _cards(self) -> list[dict]:
        return [c for c in json.loads(self._pactl("-f", "json", "list", "cards")) if c["name"].startswith("bluez_card.")]

    def _bt_source(self, address: str) -> str | None:
        return next((s for s in self._sources() if s.startswith(f"bluez_input.{address}")), None)

    def needs_switch(self) -> bool:
        """系統預設輸入不是麥克風（只是播放聲音的監聽）時為 True。查不到時當作不需要。"""
        try:
            default = self._pactl("get-default-source").strip()
        except OSError as e:
            log.debug("讀不到預設輸入：%s", e)
            return False
        return not default or default.endswith(".monitor")

    # ---- 錄音前後 ----

    def acquire(self, on_switching: Callable[[], None] | None = None) -> str | None:
        """回傳要錄音的來源名稱；None＝系統預設就是麥克風。沒有麥克風可用時丟出 NoMicrophone。
        需要切換藍牙模式（要等 1～2 秒）時，先呼叫 on_switching 讓畫面提示使用者稍等。"""
        with self._lock:
            source = self._acquire_remote(on_switching)
            if source is not False:
                return source
            self._cancel_timer()
            try:
                if self._switched is not None:  # 剛錄完還沒切回：直接用
                    source = self._bt_source(_address(self._switched[0]))
                    if source:
                        return source
                    self._switched = None
                if not self.needs_switch():
                    return None
                # 已經在免持模式、只是預設輸入不是它：直接用，不必切換
                existing = next((s for s in self._sources() if s.startswith("bluez_input.")), None)
                if existing:
                    return existing
                card, profile = self._pick_card()
                if card is None:
                    raise NoMicrophone(
                        "找不到麥克風：系統預設輸入只是喇叭的播放聲音，也沒有可以切到免持模式的藍牙耳機或喇叭。"
                        "請接上麥克風，或在設定檔的 audio.device 指定"
                    )
                if on_switching is not None:
                    on_switching()
                t = time.monotonic()
                self._pactl("set-card-profile", card["name"], profile)
                self._switched = (card["name"], card["active_profile"], profile)
                address = _address(card["name"])
                while time.monotonic() - t < self.timeout_s:
                    source = self._bt_source(address)
                    if source:
                        log.info("藍牙 %s 切到免持模式 %s（%.1f 秒），錄音用 %s",
                                 card.get("properties", {}).get("device.description", card["name"]),
                                 profile, time.monotonic() - t, source)
                        return source
                    self._sleep(0.1)
                self._restore_now()
                raise NoMicrophone("藍牙裝置切到免持模式後，等不到它的麥克風，已切回原本的模式")
            except OSError as e:
                log.warning("無法切換藍牙麥克風，改用系統預設輸入：%s", e)
                return None

    def _acquire_remote(self, on_switching) -> str | None | bool:
        """向協調服務借用；回傳麥克風名稱（None＝用系統預設），沒有協調服務時回傳 False。"""
        remote = self._remote() if self._remote else None
        if remote is None or not hasattr(remote, "bt_acquire"):
            return False
        try:
            if on_switching is not None and self.needs_switch():
                on_switching()
            source = remote.bt_acquire("danwen")
        except ServiceUnavailable as e:
            log.debug("沒有 bt-audio-profile 協調服務，用內建的切換：%s", e)
            return False
        self._remote_held = remote
        return source or None

    def _others_using(self, address: str) -> bool:
        """有其他程式在用這個藍牙麥克風錄音（不含 GNOME 設定的音量指示條）。"""
        indexes = {s["index"] for s in json.loads(self._pactl("-f", "json", "list", "sources"))
                   if s["name"].startswith(f"bluez_input.{address}")}
        for so in json.loads(self._pactl("-f", "json", "list", "source-outputs")):
            props = so.get("properties", {})
            if so.get("source") in indexes and props.get("resample.peaks") != "true" \
                    and props.get("stream.monitor") != "true":
                return True
        return False

    def _pick_card(self) -> tuple[dict | None, str | None]:
        """優先用目前播放聲音的藍牙裝置（預設輸出），其次是任何有免持模式的藍牙裝置。"""
        try:
            sink_address = _address(self._pactl("get-default-sink").strip())
        except OSError:
            sink_address = ""
        cards = sorted(self._cards(), key=lambda c: _address(c["name"]) != sink_address)
        for card in cards:
            profile = headset_profile(card)
            if profile:
                return card, profile
        return None, None

    def release(self, restore_after_s: float | None = None) -> None:
        """錄完：幾秒後切回原本的模式（期間又開始錄音就取消）。"""
        with self._lock:
            if self._remote_held is not None:
                remote, self._remote_held = self._remote_held, None
                try:
                    remote.bt_release(self.restore_after_s if restore_after_s is None else restore_after_s)
                except ServiceUnavailable as e:
                    log.warning("無法向協調服務歸還麥克風（它斷線時會自動歸還）：%s", e)
                return
            if self._switched is None:
                return
            self._cancel_timer()
            if self.restore_after_s <= 0:
                self._restore_now()
                return
            self._timer = threading.Timer(self.restore_after_s, self._restore_later)
            self._timer.daemon = True
            self._timer.start()

    def close(self) -> None:
        """danwen 結束時：立刻切回原本的模式。"""
        with self._lock:
            if self._remote_held is not None:
                self.release(0)
            self._cancel_timer()
            self._restore_now(force=True)

    def _restore_later(self) -> None:
        with self._lock:
            self._timer = None
            self._restore_now()

    def _restore_now(self, force: bool = False) -> None:
        if self._switched is None:
            return
        name, previous, headset = self._switched
        try:
            card = next((c for c in self._cards() if c["name"] == name), None)
            # 裝置已斷線，或使用者在這段時間自己換了模式：不動
            if card is None or card.get("active_profile") != headset:
                self._switched = None
                return
            # 其他程式還在用藍牙麥克風（例如 Chrome 視訊）：晚點再看；danwen 結束時就不管了
            if self._others_using(_address(name)):
                if not force:
                    self._timer = threading.Timer(RETRY_S, self._restore_later)
                    self._timer.daemon = True
                    self._timer.start()
                else:
                    self._switched = None
                return
            self._switched = None
            self._pactl("set-card-profile", name, previous)
            log.info("藍牙 %s 切回 %s", name, previous)
        except OSError as e:
            log.warning("無法把藍牙裝置切回原本的模式：%s", e)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
