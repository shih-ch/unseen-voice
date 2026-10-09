"""D-Bus 介面：讓 GNOME extension 讀取狀態、切換小紙條、操作歷史紀錄與長錄音。

匯流排名稱 io.github.danwen 同時只能由一個程式持有，因此也用來避免重複啟動
（兩個 danwen 同時執行會把每句話貼上兩次）。

D-Bus 在自己的執行緒跑 asyncio；其他執行緒要通知狀態改變時用 call_soon_threadsafe 轉過來。
注意：本檔不可加 `from __future__ import annotations`，dbus-fast 要在執行時讀型別註記（"s" 等）。
"""

import asyncio
import logging
import threading

from dbus_fast import BusType, Message, MessageType, NameFlag, PropertyAccess, RequestNameReply
from dbus_fast.aio import MessageBus
from dbus_fast.errors import DBusError
from dbus_fast.service import ServiceInterface, dbus_property, method, signal

log = logging.getLogger(__name__)

BUS_NAME = "io.github.danwen"
OBJECT_PATH = "/io/github/danwen"
INTERFACE = "io.github.danwen.Daemon1"
ERROR = "io.github.danwen.Error"
# GNOME extension 啟用時持有這個名稱，表示畫面上會顯示錄音提示（danwen 就不必再跳系統通知）
OVERLAY_BUS_NAME = "io.github.danwen.ShellOverlay"


class AlreadyRunning(RuntimeError):
    pass


class _Interface(ServiceInterface):
    def __init__(self, daemon, loop: asyncio.AbstractEventLoop):
        super().__init__(INTERFACE)
        self._daemon = daemon
        self._loop = loop

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except (KeyError, ValueError, RuntimeError, OSError) as e:
            raise DBusError(ERROR, str(e.args[0]) if e.args else str(e)) from e

    @dbus_property(access=PropertyAccess.READ)
    def State(self) -> "s":  # idle / recording / processing
        return self._daemon.state

    @dbus_property(access=PropertyAccess.READ)
    def Kind(self) -> "s":  # fast / refine / long（目前或最近一次錄音）
        return self._daemon.kind

    @dbus_property(access=PropertyAccess.READ)
    def Mode(self) -> "s":
        return self._daemon.current_mode()

    @dbus_property(access=PropertyAccess.READ)
    def Hotkey(self) -> "s":  # evdev 按鍵名稱，例如 KEY_RIGHTCTRL
        return self._daemon.cfg.hotkey.key

    # 方案：哪些部分走雲端（走雲端的錄音或文字會送出電腦）
    @dbus_property(access=PropertyAccess.READ)
    def Plan(self) -> "s":  # local / hybrid / groq / cloudflare / custom
        return self._daemon.plan_properties()["Plan"]

    @dbus_property(access=PropertyAccess.READ)
    def PlanTitle(self) -> "s":  # 例如「B 本機辨識＋Groq 整理」
        return self._daemon.plan_properties()["PlanTitle"]

    @dbus_property(access=PropertyAccess.READ)
    def Cloud(self) -> "b":  # 方案有沒有用到雲端
        return self._daemon.plan_properties()["Cloud"]

    @dbus_property(access=PropertyAccess.READ)
    def CloudAsr(self) -> "b":  # 語音辨識走雲端（錄音會送出）
        return self._daemon.plan_properties()["CloudAsr"]

    @dbus_property(access=PropertyAccess.READ)
    def CloudRefine(self) -> "b":  # 整理模式走雲端（要整理的文字會送出）
        return self._daemon.plan_properties()["CloudRefine"]

    @dbus_property(access=PropertyAccess.READ)
    def CloudProvider(self) -> "s":  # 服務商名稱，例如 Groq；全部本機時為空字串
        return self._daemon.plan_properties()["CloudProvider"]

    @method()
    def ListPlans(self) -> "a(ss)":
        return [[name, title] for name, title in self._call(self._daemon.list_plans)]

    @method()
    def SetPlan(self, name: "s"):
        self._call(self._daemon.set_plan, name)
        self.emit_properties_changed(self._daemon.plan_properties())

    @method()
    def ListModes(self) -> "a(ss)":
        return [[name, description] for name, description in self._call(self._daemon.list_modes)]

    @method()
    def SetMode(self, name: "s"):
        self._call(self._daemon.set_mode, name)
        self.emit_properties_changed({"Mode": name})

    @method()
    def GetHistory(self, limit: "i") -> "s":
        return self._call(self._daemon.history_json, limit)

    @method()
    def CopyHistory(self, entry_id: "i") -> "s":
        return self._call(self._daemon.copy_history, entry_id)

    @method()
    async def Redo(self, entry_id: "i", mode: "s") -> "s":
        # LLM 整理要幾秒，丟到其他執行緒，不卡住 D-Bus
        return await self._loop.run_in_executor(None, self._call, self._daemon.redo, entry_id, mode)

    @method()
    def StartLong(self):
        self._call(self._daemon.start_long)

    @method()
    def Stop(self):
        self._call(self._daemon.stop_recording)

    @method()
    def Cancel(self):
        self._call(self._daemon.cancel_recording)

    @signal()
    def HistoryChanged(self):
        pass


class DBusService:
    def __init__(self, daemon, bus_name: str = BUS_NAME, overlay_name: str = OVERLAY_BUS_NAME):
        self._daemon = daemon
        self._bus_name = bus_name
        self._overlay_name = overlay_name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bus: MessageBus | None = None
        self._iface: _Interface | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = 5.0) -> None:
        """連上 session bus 並取得名稱；已有另一個 danwen 在執行時丟出 AlreadyRunning。"""
        self._thread = threading.Thread(target=self._run, name="dbus", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("連接 D-Bus 逾時")
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._setup())
        except BaseException as e:  # 啟動失敗交給 start() 回報
            self._error = e
            if self._bus is not None:
                self._bus.disconnect()
            self._ready.set()
            return
        self._ready.set()
        loop.run_forever()

    async def _setup(self) -> None:
        self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self._iface = _Interface(self._daemon, self._loop)
        self._bus.export(OBJECT_PATH, self._iface)
        reply = await self._bus.request_name(self._bus_name, NameFlag.DO_NOT_QUEUE)
        if reply not in (RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER):
            raise AlreadyRunning("已經有另一個 danwen 在執行（同時執行會把每句話貼上兩次）")
        log.info("D-Bus 介面就緒：%s %s", self._bus_name, OBJECT_PATH)

    def _soon(self, fn, *args) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(fn, *args)

    def notify_state(self) -> None:
        if self._iface is not None:
            changed = {"State": self._daemon.state, "Kind": self._daemon.kind}
            self._soon(self._iface.emit_properties_changed, changed)

    def notify_properties(self, changed: dict) -> None:
        if self._iface is not None and changed:
            self._soon(self._iface.emit_properties_changed, changed)

    def notify_history(self) -> None:
        if self._iface is not None:
            self._soon(self._iface.HistoryChanged)

    def overlay_present(self, timeout: float = 0.5) -> bool:
        """GNOME extension 是否在執行（會在畫面上顯示錄音提示）。可從任何執行緒呼叫。"""
        if self._loop is None or self._bus is None or not self._loop.is_running():
            return False
        future = asyncio.run_coroutine_threadsafe(self._name_has_owner(self._overlay_name), self._loop)
        try:
            return future.result(timeout)
        except Exception:
            return False

    async def _name_has_owner(self, name: str) -> bool:
        reply = await self._bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="NameHasOwner",
                signature="s",
                body=[name],
            )
        )
        return reply.message_type == MessageType.METHOD_RETURN and bool(reply.body[0])

    def stop(self) -> None:
        if self._loop is None:
            return
        if self._bus is not None:
            self._loop.call_soon_threadsafe(self._bus.disconnect)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=2)
