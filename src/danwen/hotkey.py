"""熱鍵：按住超過門檻才開始錄音；按住期間按了其他鍵就視為組合鍵並取消。
先短按一下、很快再按住，則是整理模式（START_REFINE）。

HoldDetector 是純邏輯、不碰裝置，方便測試；KeyboardListener 負責讀 /dev/input。
監聽只讀取事件、不獨占裝置（不 grab），所有按鍵照常送到 GNOME 與 fcitx5。
"""

from __future__ import annotations

import enum
import glob
import logging
import os
import selectors
import threading
import time
from collections.abc import Callable, Collection

import evdev
from evdev import ecodes

log = logging.getLogger(__name__)

KEY_UP, KEY_DOWN, KEY_REPEAT = 0, 1, 2
# 0x100 以上是滑鼠、搖桿等按鈕，不算「其他鍵」
_FIRST_BUTTON = 0x100


class Action(enum.Enum):
    START = "start"
    START_REFINE = "start_refine"
    STOP = "stop"
    CANCEL = "cancel"


class _State(enum.Enum):
    IDLE = "idle"
    ARMED = "armed"  # 熱鍵已按下，還沒到門檻
    RECORDING = "recording"
    COMBO = "combo"  # 判定為組合鍵，等熱鍵放開再回到 IDLE


class HoldDetector:
    def __init__(self, key: int, hold_s: float, max_s: float, double_tap_s: float = 0.0):
        self.key = key
        self.hold_s = hold_s
        self.max_s = max_s
        self.double_tap_s = double_tap_s
        self._state = _State.IDLE
        self._since = 0.0
        self._held: set[int] = set()
        self._last_tap: float | None = None  # 上一次短按熱鍵放開的時間
        self._refine = False

    @property
    def recording(self) -> bool:
        return self._state is _State.RECORDING

    def deadline(self) -> float | None:
        """下一次需要呼叫 on_tick 的時間點。"""
        if self._state is _State.ARMED:
            return self._since + self.hold_s
        if self._state is _State.RECORDING:
            return self._since + self.max_s
        return None

    def on_key(self, code: int, value: int, now: float) -> Action | None:
        if value == KEY_REPEAT:
            return None
        if code == self.key:
            if value == KEY_DOWN:
                if self._state is _State.IDLE:
                    self._refine = (
                        self.double_tap_s > 0
                        and self._last_tap is not None
                        and now - self._last_tap <= self.double_tap_s
                    )
                    self._last_tap = None
                    # 先按住其他鍵再按熱鍵（例如 Shift+右Ctrl）也是組合鍵
                    self._state = _State.COMBO if self._held else _State.ARMED
                    self._since = now
                return None
            previous = self._state
            self._state = _State.IDLE
            if previous is _State.ARMED:
                self._last_tap = now  # 短按一下；接著很快再按住就是整理模式
            return Action.STOP if previous is _State.RECORDING else None
        if code >= _FIRST_BUTTON:
            return None
        if value == KEY_DOWN:
            self._held.add(code)
            self._last_tap = None
            if self._state is _State.ARMED:
                self._state = _State.COMBO
            elif self._state is _State.RECORDING:
                self._state = _State.COMBO
                return Action.CANCEL
        else:
            self._held.discard(code)
        return None

    def on_tick(self, now: float) -> Action | None:
        if self._state is _State.ARMED and now - self._since >= self.hold_s:
            self._state = _State.RECORDING
            self._since = now
            return Action.START_REFINE if self._refine else Action.START
        if self._state is _State.RECORDING and now - self._since >= self.max_s:
            self._state = _State.COMBO
            return Action.STOP
        return None

    def reset(self) -> Action | None:
        """鍵盤被拔除等狀況：清掉狀態；正在錄音就取消。"""
        was_recording = self._state is _State.RECORDING
        self._state = _State.IDLE
        self._held.clear()
        self._last_tap = None
        return Action.CANCEL if was_recording else None


def key_code(name: str) -> int:
    code = ecodes.ecodes.get(name)
    if not isinstance(code, int):
        raise ValueError(f"未知的按鍵名稱：{name}（例如 KEY_RIGHTCTRL）")
    return code


class NoKeyboardError(RuntimeError):
    pass


class KeyboardListener:
    RESCAN_S = 2.0

    def __init__(
        self,
        detector: HoldDetector,
        on_action: Callable[[Action], None],
        ignore_names: Collection[str] = (),
    ):
        self._detector = detector
        self._on_action = on_action
        self._ignore = set(ignore_names)
        self._sel = selectors.DefaultSelector()
        self._devices: dict[str, evdev.InputDevice] = {}
        self._skipped: set[tuple[str, int]] = set()
        self._denied: set[str] = set()

    def _wanted(self, dev: evdev.InputDevice) -> bool:
        if dev.name in self._ignore:
            return False
        keys = dev.capabilities().get(ecodes.EV_KEY, [])
        return self._detector.key in keys or ecodes.KEY_A in keys

    def _scan(self) -> None:
        present = set(glob.glob("/dev/input/event*"))
        self._skipped = {s for s in self._skipped if s[0] in present}
        for path in sorted(present - self._devices.keys()):
            try:
                ident = (path, os.stat(path).st_ino)
            except OSError:
                continue
            if ident in self._skipped:
                continue
            try:
                dev = evdev.InputDevice(path)
            except PermissionError:
                self._denied.add(path)
                continue
            except OSError:
                continue
            if not self._wanted(dev):
                dev.close()
                self._skipped.add(ident)
                continue
            self._devices[path] = dev
            self._sel.register(dev.fd, selectors.EVENT_READ, dev)
            log.info("監聽鍵盤：%s（%s）", dev.name, path)

    def _drop(self, dev: evdev.InputDevice) -> None:
        log.info("鍵盤已移除：%s", dev.path)
        self._devices.pop(dev.path, None)
        try:
            self._sel.unregister(dev.fd)
            dev.close()
        except (KeyError, ValueError, OSError):
            pass

    def _emit(self, action: Action | None) -> None:
        if action is not None:
            self._on_action(action)

    def run(self, stop: threading.Event) -> None:
        self._scan()
        if not self._devices:
            if self._denied:
                raise NoKeyboardError(
                    "沒有權限讀取 /dev/input。請確認已加入 input 群組，並登出再登入。"
                )
            raise NoKeyboardError("找不到鍵盤裝置。")
        next_scan = time.monotonic() + self.RESCAN_S
        try:
            while not stop.is_set():
                now = time.monotonic()
                timeout = min(next_scan - now, 0.5)
                deadline = self._detector.deadline()
                if deadline is not None:
                    timeout = min(timeout, deadline - now)
                for key, _ in self._sel.select(max(timeout, 0)):
                    dev = key.data
                    try:
                        for ev in dev.read():
                            if ev.type == ecodes.EV_KEY:
                                self._emit(self._detector.on_key(ev.code, ev.value, time.monotonic()))
                    except BlockingIOError:
                        pass
                    except OSError:
                        self._drop(dev)
                        self._emit(self._detector.reset())
                self._emit(self._detector.on_tick(time.monotonic()))
                if time.monotonic() >= next_scan:
                    self._scan()
                    next_scan = time.monotonic() + self.RESCAN_S
        finally:
            for dev in list(self._devices.values()):
                dev.close()
            self._sel.close()
