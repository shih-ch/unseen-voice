"""輸出：備份剪貼簿 → 寫入結果 → 虛擬鍵盤送 Ctrl+V（終端機等依規則改用其他按鍵）→ 稍後還原剪貼簿。

剪貼簿走 XWayland（xclip）而不是 wl-clipboard：GNOME 沒有提供背景程式存取剪貼簿的
Wayland 協定，wl-copy／wl-paste 每次都要開一個小視窗搶鍵盤焦點，會讓目標輸入框
（和 fcitx5）收到失焦／取得焦點事件。X11 端設定剪貼簿不需要焦點，Mutter 會把它同步給
Wayland 程式。
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

VKBD_NAME = "danwen virtual keyboard"
_XCLIP = ["xclip", "-selection", "clipboard"]
_TEXT_TARGET = "UTF8_STRING"
_PREFERRED_TARGETS = (_TEXT_TARGET, "text/plain;charset=utf-8", "image/png")
_META_TARGETS = {"TARGETS", "TIMESTAMP", "MULTIPLE", "SAVE_TARGETS", "DELETE"}
# 寫入剪貼簿後稍等，讓 Mutter 把 X11 的剪貼簿同步到 Wayland 端再貼上
_SYNC_DELAY_S = 0.03


@dataclass
class ClipboardContent:
    target: str
    data: bytes


class Clipboard:
    @staticmethod
    def _read(target: str) -> bytes | None:
        r = subprocess.run([*_XCLIP, "-o", "-t", target], capture_output=True, timeout=2)
        return r.stdout if r.returncode == 0 else None

    @staticmethod
    def _write(data: bytes, target: str) -> None:
        # xclip 會 fork 到背景持續提供內容，所以 stdout/stderr 不能接 pipe，否則會卡住
        subprocess.run(
            [*_XCLIP, "-i", "-t", target], input=data, check=True, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    @classmethod
    def save(cls) -> ClipboardContent | None:
        listing = cls._read("TARGETS")
        if not listing:
            return None
        targets = [t for t in listing.decode(errors="replace").splitlines() if t not in _META_TARGETS]
        target = next((t for t in _PREFERRED_TARGETS if t in targets), targets[0] if targets else None)
        if target is None:
            return None
        data = cls._read(target)
        return ClipboardContent(target, data) if data is not None else None

    @classmethod
    def set_text(cls, text: str) -> None:
        cls._write(text.encode(), _TEXT_TARGET)

    @classmethod
    def get_text(cls) -> str | None:
        data = cls._read(_TEXT_TARGET)
        return data.decode(errors="replace") if data is not None else None

    @staticmethod
    def get_selection() -> str | None:
        """最近選取的文字（X11 PRIMARY，Mutter 會與 Wayland 程式同步）。"""
        r = subprocess.run(
            ["xclip", "-selection", "primary", "-o", "-t", _TEXT_TARGET], capture_output=True, timeout=2
        )
        return r.stdout.decode(errors="replace") if r.returncode == 0 else None

    @classmethod
    def restore(cls, saved: ClipboardContent | None) -> None:
        if saved is None:
            cls._write(b"", _TEXT_TARGET)
        else:
            cls._write(saved.data, saved.target)


# 貼上用的按鍵組合 → evdev 按鍵名稱（依序按下，反序放開，跟手按的順序一樣：
# fcitx5 的 Ctrl+Shift 切換輸入法只在中間沒按其他鍵時才觸發，所以 Ctrl+Shift+V 不會切換）
_KEY_NAMES = {"ctrl": "KEY_LEFTCTRL", "shift": "KEY_LEFTSHIFT", "v": "KEY_V", "insert": "KEY_INSERT"}


def key_names(keys: str) -> list[str]:
    """「ctrl+shift+v」→ ["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_V"]。"""
    try:
        return [_KEY_NAMES[k] for k in keys.lower().split("+")]
    except KeyError as e:
        raise ValueError(f"不支援的貼上按鍵：{keys}") from e


class VirtualKeyboard:
    """uinput 虛擬鍵盤，只會按貼上用的幾個鍵。啟動時建立並常駐，讓 GNOME 先認得這個裝置。"""

    def __init__(self):
        from evdev import UInput, ecodes

        self._e = ecodes
        codes = [getattr(ecodes, name) for name in _KEY_NAMES.values()]
        self._ui = UInput({ecodes.EV_KEY: codes}, name=VKBD_NAME)

    def _key(self, code: int, value: int) -> None:
        self._ui.write(self._e.EV_KEY, code, value)
        self._ui.syn()
        time.sleep(0.008)

    def press(self, keys: str) -> None:
        codes = [getattr(self._e, name) for name in key_names(keys)]
        for code in codes:
            self._key(code, 1)
        for code in reversed(codes):
            self._key(code, 0)

    def close(self) -> None:
        self._ui.close()


class Paster:
    def __init__(self, restore_clipboard: bool = True, restore_delay_ms: int = 500):
        self.restore_clipboard = restore_clipboard
        self.restore_delay_s = restore_delay_ms / 1000
        self._keyboard = VirtualKeyboard()

    def paste(self, text: str, keys: str = "ctrl+v") -> None:
        """keys：貼上用的按鍵（ctrl+v、ctrl+shift+v、shift+insert）；none＝只放進剪貼簿，不貼也不還原。"""
        if keys == "none":
            Clipboard.set_text(text)
            return
        saved = Clipboard.save() if self.restore_clipboard else None
        Clipboard.set_text(text)
        time.sleep(_SYNC_DELAY_S)
        self._keyboard.press(keys)
        if self.restore_clipboard:
            timer = threading.Timer(self.restore_delay_s, self._restore, (saved, text))
            timer.daemon = True
            timer.start()

    @staticmethod
    def _restore(saved: ClipboardContent | None, pasted: str) -> None:
        try:
            # 使用者在這段時間自己複製了別的東西，就不要蓋掉
            if Clipboard.get_text() != pasted:
                log.info("剪貼簿已被改變，略過還原")
                return
            Clipboard.restore(saved)
        except Exception:
            log.exception("還原剪貼簿失敗")

    def close(self) -> None:
        self._keyboard.close()
