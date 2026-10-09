"""熱鍵：三種用法共用一個鍵。

- 按住（超過 hold 時間）：快速模式，放開結束；按住期間按了其他鍵視為組合鍵並取消
- 先短按一下、很快再按住：整理模式（START_REFINE）
- 很快連按兩下：長錄音（START_LONG），不必按住；再按一下結束，Esc 取消，其他鍵不影響

HoldDetector 是純邏輯、不碰裝置，方便測試；KeyboardListener 負責讀 /dev/input。
監聽只讀取事件、不獨占裝置（不 grab），所有按鍵照常送到 GNOME 與 fcitx5。
"""

from __future__ import annotations

import enum
import glob
import logging
import os
import queue
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
    START_LONG = "start_long"
    STOP = "stop"
    CANCEL = "cancel"


class _State(enum.Enum):
    IDLE = "idle"
    ARMED = "armed"  # 熱鍵已按下，還沒到門檻
    RECORDING = "recording"  # 按住錄音中
    COMBO = "combo"  # 判定為組合鍵（或錄音已結束），等熱鍵放開再回到 IDLE
    LONG = "long"  # 長錄音中，熱鍵沒有按著
    LONG_PRESSED = "long_pressed"  # 長錄音中按下熱鍵；乾淨地放開就結束
    LONG_COMBO = "long_combo"  # 長錄音中把熱鍵當組合鍵用（例如右 Ctrl+C），不結束


_LONG_STATES = (_State.LONG, _State.LONG_PRESSED, _State.LONG_COMBO)


class HoldDetector:
    def __init__(
        self,
        key: int,
        hold_s: float,
        max_s: float,
        double_tap_s: float = 0.0,
        long_max_s: float = 0.0,
    ):
        self.key = key
        self.hold_s = hold_s
        self.max_s = max_s
        self.double_tap_s = double_tap_s
        self.long_max_s = long_max_s  # 0＝停用長錄音
        self._state = _State.IDLE
        self._since = 0.0
        self._held: set[int] = set()
        self._last_tap: float | None = None  # 上一次短按熱鍵放開的時間
        self._second_press = False  # 這次按下是否緊接在短按之後

    @property
    def recording(self) -> bool:
        return self._state is _State.RECORDING or self._state in _LONG_STATES

    def deadline(self) -> float | None:
        """下一次需要呼叫 on_tick 的時間點。"""
        if self._state is _State.ARMED:
            return self._since + self.hold_s
        if self._state is _State.RECORDING:
            return self._since + self.max_s
        if self._state in _LONG_STATES:
            return self._since + self.long_max_s
        return None

    def _end_long(self, action: Action) -> Action:
        # 熱鍵還按著就等它放開，避免放開時又被當成一次短按
        self._state = _State.COMBO if self.key in self._held else _State.IDLE
        return action

    def on_key(self, code: int, value: int, now: float) -> Action | None:
        if value == KEY_REPEAT:
            return None
        if code == self.key:
            return self._on_hotkey(value == KEY_DOWN, now)
        if code >= _FIRST_BUTTON:
            return None
        if value != KEY_DOWN:
            self._held.discard(code)
            return None
        self._held.add(code)
        self._last_tap = None
        if self._state in _LONG_STATES:
            if code == ecodes.KEY_ESC:
                return self._end_long(Action.CANCEL)
            if self._state is _State.LONG_PRESSED:
                self._state = _State.LONG_COMBO
            return None  # 長錄音時其他按鍵不影響（可以切換視窗、點選位置）
        if self._state is _State.ARMED:
            self._state = _State.COMBO
        elif self._state is _State.RECORDING:
            self._state = _State.COMBO
            return Action.CANCEL
        return None

    def _on_hotkey(self, down: bool, now: float) -> Action | None:
        if down:
            self._held.add(self.key)
            if self._state is _State.IDLE:
                self._second_press = (
                    self.double_tap_s > 0
                    and self._last_tap is not None
                    and now - self._last_tap <= self.double_tap_s
                )
                self._last_tap = None
                # 先按住其他鍵再按熱鍵（例如 Shift+右Ctrl）也是組合鍵
                self._state = _State.COMBO if self._held - {self.key} else _State.ARMED
                self._since = now
            elif self._state is _State.LONG:
                self._state = _State.LONG_PRESSED
            return None
        self._held.discard(self.key)
        state = self._state
        if state is _State.LONG_PRESSED:
            self._state = _State.IDLE
            return Action.STOP
        if state is _State.LONG_COMBO:
            self._state = _State.LONG
            return None
        if state is _State.LONG:
            return None
        self._state = _State.IDLE
        if state is _State.ARMED:
            if self._second_press and self.long_max_s > 0:
                # 連按兩下：開始長錄音
                self._state = _State.LONG
                self._since = now
                return Action.START_LONG
            self._last_tap = now  # 短按一下；接著很快再按就是整理模式或長錄音
        return Action.STOP if state is _State.RECORDING else None

    def on_tick(self, now: float) -> Action | None:
        if self._state is _State.ARMED and now - self._since >= self.hold_s:
            self._state = _State.RECORDING
            self._since = now
            return Action.START_REFINE if self._second_press else Action.START
        if self._state is _State.RECORDING and now - self._since >= self.max_s:
            self._state = _State.COMBO
            return Action.STOP
        if self._state in _LONG_STATES and now - self._since >= self.long_max_s:
            return self._end_long(Action.STOP)
        return None

    # ---- 外部控制（GNOME extension 的選單經 D-Bus 呼叫）----

    def start_long(self, now: float) -> Action | None:
        if self._state is not _State.IDLE or self.long_max_s <= 0:
            return None
        self._state = _State.LONG
        self._since = now
        self._last_tap = None
        return Action.START_LONG

    def stop(self) -> Action | None:
        if self._state in _LONG_STATES:
            return self._end_long(Action.STOP)
        if self._state is _State.RECORDING:
            self._state = _State.COMBO  # 熱鍵還按著，等它放開
            return Action.STOP
        return None

    def cancel(self) -> Action | None:
        if self._state in _LONG_STATES:
            return self._end_long(Action.CANCEL)
        if self._state is _State.RECORDING:
            self._state = _State.COMBO
            return Action.CANCEL
        return None

    def reset(self) -> Action | None:
        """鍵盤被拔除等狀況：清掉狀態；正在錄音就取消。"""
        was_recording = self.recording
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
        # 其他執行緒（D-Bus）送來的指令；寫入 _wake 讓 select 立刻醒來處理
        self._commands: queue.SimpleQueue[Callable[[HoldDetector, float], Action | None]] = queue.SimpleQueue()
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        self._sel.register(self._wake_r, selectors.EVENT_READ, None)
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

    def post(self, command: Callable[[HoldDetector, float], Action | None]) -> None:
        """從其他執行緒要求操作偵測器（例如開始長錄音），在監聽執行緒裡執行。"""
        self._commands.put(command)
        os.write(self._wake_w, b"x")

    def _run_commands(self) -> None:
        try:
            os.read(self._wake_r, 4096)
        except BlockingIOError:
            pass
        while not self._commands.empty():
            self._emit(self._commands.get()(self._detector, time.monotonic()))

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
                    if key.data is None:
                        self._run_commands()
                        continue
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
            os.close(self._wake_r)
            os.close(self._wake_w)
