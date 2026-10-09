"""輸出：備份剪貼簿 → 寫入結果 → 虛擬鍵盤送 Ctrl+V → 稍後還原剪貼簿。

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

    @classmethod
    def restore(cls, saved: ClipboardContent | None) -> None:
        if saved is None:
            cls._write(b"", _TEXT_TARGET)
        else:
            cls._write(saved.data, saved.target)


class VirtualKeyboard:
    """uinput 虛擬鍵盤，只會按 Ctrl+V。啟動時建立並常駐，讓 GNOME 先認得這個裝置。"""

    def __init__(self):
        from evdev import UInput, ecodes

        self._e = ecodes
        self._ui = UInput({ecodes.EV_KEY: [ecodes.KEY_LEFTCTRL, ecodes.KEY_V]}, name=VKBD_NAME)

    def _key(self, code: int, value: int) -> None:
        self._ui.write(self._e.EV_KEY, code, value)
        self._ui.syn()
        time.sleep(0.008)

    def ctrl_v(self) -> None:
        e = self._e
        self._key(e.KEY_LEFTCTRL, 1)
        self._key(e.KEY_V, 1)
        self._key(e.KEY_V, 0)
        self._key(e.KEY_LEFTCTRL, 0)

    def close(self) -> None:
        self._ui.close()


class Paster:
    def __init__(self, restore_clipboard: bool = True, restore_delay_ms: int = 500):
        self.restore_clipboard = restore_clipboard
        self.restore_delay_s = restore_delay_ms / 1000
        self._keyboard = VirtualKeyboard()

    def paste(self, text: str) -> None:
        saved = Clipboard.save() if self.restore_clipboard else None
        Clipboard.set_text(text)
        time.sleep(_SYNC_DELAY_S)
        self._keyboard.ctrl_v()
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
