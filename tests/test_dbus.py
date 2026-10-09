"""D-Bus 介面：在 session bus 上用測試專用的名稱啟動服務，以 dbus-fast 當客戶端呼叫（就像 GNOME extension 會做的）。"""

import asyncio
import json
import os

import pytest
from dbus_fast import BusType
from dbus_fast.aio import MessageBus
from dbus_fast.errors import DBusError

from danwen.config import ConfigError
from danwen.dbus_service import INTERFACE, OBJECT_PATH, AlreadyRunning, DBusService

pytestmark = pytest.mark.skipif(not os.environ.get("DBUS_SESSION_BUS_ADDRESS"), reason="沒有 session bus")


class StubDaemon:
    cfg = type("Cfg", (), {"hotkey": type("Hotkey", (), {"key": "KEY_RIGHTCTRL"})})

    def __init__(self):
        self.state, self.kind, self.mode = "idle", "", "日常"
        self.calls: list[str] = []

    def current_mode(self):
        return self.mode

    def cloud_enabled(self):
        return False

    def cloud_label(self):
        return "Groq"

    def list_modes(self):
        return [("日常", "預設"), ("英文", "翻譯")]

    def set_mode(self, name):
        if name not in ("日常", "英文"):
            raise ConfigError(f"找不到小紙條「{name}」")
        self.mode = name

    def history_json(self, limit):
        return json.dumps([{"id": 2, "text": "第二句"}, {"id": 1, "text": "第一句"}][: limit or None], ensure_ascii=False)

    def copy_history(self, entry_id):
        if entry_id == 99:
            raise KeyError("找不到第 99 筆")
        return "第二句"

    def redo(self, entry_id, mode):
        return f"{mode}:{entry_id}"

    def start_long(self):
        self.calls.append("start_long")

    def stop_recording(self):
        self.calls.append("stop")

    def cancel_recording(self):
        self.calls.append("cancel")


@pytest.fixture
def service():
    name = f"io.github.danwen.Test{os.getpid()}"
    daemon = StubDaemon()
    svc = DBusService(daemon, bus_name=name)
    svc.start()
    yield name, daemon, svc
    svc.stop()


async def connect(name):
    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    obj = bus.get_proxy_object(name, OBJECT_PATH, await bus.introspect(name, OBJECT_PATH))
    return bus, obj.get_interface(INTERFACE), obj.get_interface("org.freedesktop.DBus.Properties")


def test_properties_and_methods(service):
    name, daemon, _ = service

    async def scenario():
        bus, iface, _ = await connect(name)
        assert await iface.get_state() == "idle"
        assert await iface.get_mode() == "日常"
        assert await iface.get_hotkey() == "KEY_RIGHTCTRL"
        assert [list(m) for m in await iface.call_list_modes()] == [["日常", "預設"], ["英文", "翻譯"]]
        await iface.call_set_mode("英文")
        assert await iface.get_mode() == "英文"
        assert json.loads(await iface.call_get_history(1)) == [{"id": 2, "text": "第二句"}]
        assert await iface.call_copy_history(0) == "第二句"
        assert await iface.call_redo(2, "Email") == "Email:2"
        await iface.call_start_long()
        await iface.call_stop()
        await iface.call_cancel()
        bus.disconnect()

    asyncio.run(scenario())
    assert daemon.calls == ["start_long", "stop", "cancel"]


def test_errors_carry_readable_message(service):
    name, _, _ = service

    async def scenario():
        bus, iface, _ = await connect(name)
        with pytest.raises(DBusError, match="找不到第 99 筆"):
            await iface.call_copy_history(99)
        with pytest.raises(DBusError, match="找不到小紙條"):
            await iface.call_set_mode("不存在")
        bus.disconnect()

    asyncio.run(scenario())


def test_state_and_history_signals(service):
    name, daemon, svc = service

    async def scenario():
        bus, iface, props = await connect(name)
        changes, history = [], asyncio.Event()
        props.on_properties_changed(lambda _iface, changed, _inv: changes.append({k: v.value for k, v in changed.items()}))
        iface.on_history_changed(lambda: history.set())
        await asyncio.sleep(0.1)
        daemon.state, daemon.kind = "recording", "refine"
        svc.notify_state()
        svc.notify_history()
        await asyncio.wait_for(history.wait(), 2)
        for _ in range(20):
            if changes:
                break
            await asyncio.sleep(0.05)
        bus.disconnect()
        return changes

    assert asyncio.run(scenario())[0] == {"State": "recording", "Kind": "refine"}


def test_second_instance_is_refused(service):
    name, _, _ = service
    with pytest.raises(AlreadyRunning):
        DBusService(StubDaemon(), bus_name=name).start()


def test_overlay_presence_follows_name_owner():
    name = f"io.github.danwen.Test{os.getpid()}b"
    overlay = f"io.github.danwen.TestOverlay{os.getpid()}"
    svc = DBusService(StubDaemon(), bus_name=name, overlay_name=overlay)
    svc.start()
    try:
        assert svc.overlay_present() is False

        async def own_and_check():
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            await bus.request_name(overlay)
            present = await asyncio.get_running_loop().run_in_executor(None, svc.overlay_present)
            bus.disconnect()
            return present

        assert asyncio.run(own_and_check()) is True
    finally:
        svc.stop()
