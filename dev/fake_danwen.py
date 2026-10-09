"""開發用：假的 danwen 常駐程式，只提供 D-Bus 介面，方便測試 GNOME extension。

預設停在待命，只有從選單操作時才改變狀態（開始長錄音 → 結束後辨識 → 新增一筆紀錄）。
設定 DANWEN_FAKE_CYCLE=1 時會自動輪流切換（待命 → 錄音 → 辨識中），用來看圖示變色；
從選單開始長錄音時暫停自動切換，直到按「結束」或「取消」。
不錄音、不辨識、不碰剪貼簿。由 dev/nested-shell.sh 在隔離的 D-Bus 裡啟動。
"""

import json
import os
import threading
import time

from danwen import refine
from danwen.config import ConfigError
from danwen.dbus_service import DBusService


class FakeConfig:
    class hotkey:  # 模仿 Config.hotkey.key
        key = "KEY_RIGHTCTRL"


class FakeDaemon:
    cfg = FakeConfig

    def __init__(self):
        self.state, self.kind, self.mode = "idle", "", "日常"
        self.cloud = False
        self._refused_once = False
        self.manual = threading.Event()  # 由選單控制錄音時停止自動切換
        self.service: DBusService | None = None
        self.history = [
            {"id": 2, "mode": "日常", "text": "我們明天下午四點開會，要討論三件事：\n1. 預算\n2. 人力\n3. 時程"},
            {"id": 1, "mode": None, "text": "嗯，那個今天的會議改到下午兩點半，然後記得通知PM。"},
        ]

    def set_state(self, state, kind=None):
        self.state = state
        if kind is not None:
            self.kind = kind
        print(f"狀態：{state} {self.kind}", flush=True)
        self.service.notify_state()

    def add_history(self, text, mode=None):
        self.history.insert(0, {"id": self.history[0]["id"] + 1 if self.history else 1, "mode": mode, "text": text})
        self.service.notify_history()

    # ---- D-Bus 會呼叫的方法（與 danwen.daemon.Daemon 相同）----

    def current_mode(self):
        return self.mode

    def cloud_enabled(self):
        return self.cloud

    def cloud_label(self):
        return "Groq"

    def set_cloud(self, on):
        # DANWEN_FAKE_NO_KEY=1 時一律模擬「還沒設定金鑰」；=once 時只有第一次，用來看選單開關會不會跳回
        no_key = os.environ.get("DANWEN_FAKE_NO_KEY")
        if on and (no_key == "1" or (no_key == "once" and not self._refused_once)):
            self._refused_once = True
            raise RuntimeError("還沒設定 Groq 的 API Key，請執行：danwen key set groq")
        self.cloud = on
        print(f"雲端：{'開' if on else '關'}", flush=True)

    def list_modes(self):
        return [(name, refine.load_prompt(name).description) for name in refine.available_prompts()]

    def set_mode(self, name):
        if name not in refine.available_prompts():
            raise ConfigError(f"找不到小紙條「{name}」")
        self.mode = name
        print(f"切換小紙條：{name}", flush=True)

    def history_json(self, limit):
        return json.dumps(self.history[: limit or None], ensure_ascii=False)

    def copy_history(self, entry_id):
        entry = next((e for e in self.history if e["id"] == entry_id), self.history[0] if entry_id == 0 else None)
        if entry is None:
            raise KeyError(f"找不到第 {entry_id} 筆")
        print(f"（假裝）複製第 {entry['id']} 筆", flush=True)
        return entry["text"]

    def redo(self, entry_id, mode):
        time.sleep(2)
        text = f"（用「{mode or self.mode}」重新整理的結果）{self.history[0]['text']}"
        self.add_history(text, mode or self.mode)
        return text

    def start_long(self):
        if self.state == "idle":
            self.manual.set()
            self.set_state("recording", "long")

    def stop_recording(self):
        if self.state == "recording":
            self.set_state("processing")
            threading.Timer(1.5, self._finish, ("（長錄音的辨識結果）今天的內容先講到這裡。",)).start()

    def cancel_recording(self):
        if self.state == "recording":
            self.set_state("idle")
            self.manual.clear()

    def _finish(self, text):
        self.add_history(text)
        self.set_state("idle")
        self.manual.clear()


def main():
    daemon = FakeDaemon()
    daemon.service = DBusService(daemon)
    daemon.service.start()
    print("假的 danwen 已在 D-Bus 上就緒", flush=True)
    if os.environ.get("DANWEN_FAKE_CYCLE") != "1":
        threading.Event().wait()  # 不自動切換，只回應選單操作
    cycle = [
        ("idle", "", 4), ("recording", "fast", 4), ("processing", None, 2),
        ("idle", "", 4), ("recording", "refine", 5), ("processing", None, 3),
    ]
    n = 0
    while True:
        for state, kind, seconds in cycle:
            if daemon.manual.is_set():
                break
            daemon.set_state(state, kind)
            time.sleep(seconds)
        else:
            n += 1
            daemon.add_history(f"自動產生的第 {n} 句測試聽寫。", None)
            continue
        while daemon.manual.is_set():
            time.sleep(0.5)


if __name__ == "__main__":
    main()
