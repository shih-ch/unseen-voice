"""藍牙麥克風：用假的 pactl 模擬 PipeWire 的裝置與模式，絕不碰真正的音訊系統。"""

import json

import pytest

from danwen.btmic import BluetoothMic, NoMicrophone, headset_profile

SPEAKER = "50_4F_3B_8A_04_C2"
HEADPHONES = "AC_80_0A_7F_DD_AF"


def card(address, active="a2dp-sink"):
    return {
        "name": f"bluez_card.{address}",
        "active_profile": active,
        "properties": {"device.description": address},
        "profiles": {
            "off": {"sources": 0, "priority": 0, "available": True},
            "a2dp-sink": {"sources": 0, "priority": 18, "available": True},
            "headset-head-unit": {"sources": 1, "priority": 1, "available": True},
            "headset-head-unit-cvsd": {"sources": 1, "priority": 2, "available": True},
            "headset-head-unit-msbc": {"sources": 1, "priority": 3, "available": True},
        },
    }


class FakePactl:
    """模擬 pactl：切到有麥克風的模式後，該裝置的麥克風在下一次查詢時出現。"""

    def __init__(self, cards, default_sink=f"bluez_output.{SPEAKER}.1", mic_appears=True):
        self.cards = cards
        self.default_sink = default_sink
        self.mic_appears = mic_appears
        self.calls = []
        self.recording = []  # 其他程式正在錄音的來源名稱

    def _mic_sources(self):
        return [f"bluez_input.{c['name'].split('.')[1]}.0" for c in self.cards
                if self.mic_appears and c["profiles"][c["active_profile"]]["sources"] > 0]

    def __call__(self, *args):
        self.calls.append(args)
        if args == ("get-default-source",):
            mics = self._mic_sources()
            return (mics[0] if mics else f"{self.default_sink}.monitor") + "\n"
        if args == ("get-default-sink",):
            return self.default_sink + "\n"
        if args == ("-f", "json", "list", "sources"):
            names = [f"{self.default_sink}.monitor", *self._mic_sources()]
            return json.dumps([{"index": i, "name": n} for i, n in enumerate(names)])
        if args == ("-f", "json", "list", "source-outputs"):
            names = [f"{self.default_sink}.monitor", *self._mic_sources()]
            return json.dumps([{"source": names.index(n), "properties": {"application.name": "Chrome"}}
                               for n in self.recording if n in names])
        if args == ("-f", "json", "list", "cards"):
            return json.dumps(self.cards)
        if args[0] == "set-card-profile":
            next(c for c in self.cards if c["name"] == args[1])["active_profile"] = args[2]
            return ""
        raise AssertionError(f"沒模擬到的 pactl 指令：{args}")

    def switches(self):
        return [a[1:] for a in self.calls if a[0] == "set-card-profile"]


def test_headset_profile_prefers_wideband():
    assert headset_profile(card(SPEAKER)) == "headset-head-unit-msbc"
    assert headset_profile({"profiles": {"a2dp-sink": {"sources": 0, "priority": 18}}}) is None


def test_switches_to_headset_and_back():
    pactl = FakePactl([card(HEADPHONES), card(SPEAKER)])
    mic = BluetoothMic(restore_after_s=0, pactl=pactl, sleep=lambda s: None)
    notified = []
    assert mic.acquire(on_switching=lambda: notified.append(True)) == f"bluez_input.{SPEAKER}.0"
    assert notified == [True]  # 要等切換時先提示
    # 優先用目前播放聲音的裝置（預設輸出），不是清單裡的第一個
    assert pactl.switches() == [(f"bluez_card.{SPEAKER}", "headset-head-unit-msbc")]
    mic.release()  # restore_after_s=0：立刻切回
    assert pactl.switches()[-1] == (f"bluez_card.{SPEAKER}", "a2dp-sink")


def test_reuses_headset_mode_until_restored():
    pactl = FakePactl([card(SPEAKER)])
    mic = BluetoothMic(restore_after_s=60, pactl=pactl, sleep=lambda s: None)
    mic.acquire()
    mic.release()  # 60 秒後才切回
    assert mic.acquire() == f"bluez_input.{SPEAKER}.0"  # 連續聽寫：不必再切
    assert len(pactl.switches()) == 1
    mic.close()  # danwen 結束：立刻切回
    assert pactl.switches()[-1] == (f"bluez_card.{SPEAKER}", "a2dp-sink")


def test_real_microphone_means_no_switch():
    pactl = FakePactl([card(SPEAKER, active="headset-head-unit")])  # 預設輸入已經是麥克風
    mic = BluetoothMic(pactl=pactl)
    assert mic.acquire() is None
    assert pactl.switches() == []


def test_does_not_restore_if_user_changed_profile():
    pactl = FakePactl([card(SPEAKER)])
    mic = BluetoothMic(restore_after_s=0, pactl=pactl, sleep=lambda s: None)
    mic.acquire()
    pactl.cards[0]["active_profile"] = "a2dp-sink-sbc_xq"  # 使用者自己換了
    mic.release()
    assert pactl.switches() == [(f"bluez_card.{SPEAKER}", "headset-head-unit-msbc")]


def test_no_microphone_at_all():
    mic = BluetoothMic(pactl=FakePactl([], default_sink="alsa_output.hdmi"))
    with pytest.raises(NoMicrophone, match="找不到麥克風"):
        mic.acquire()


def test_mic_never_appears_switches_back():
    pactl = FakePactl([card(SPEAKER)], mic_appears=False)
    mic = BluetoothMic(timeout_s=0.05, pactl=pactl, sleep=lambda s: None)
    with pytest.raises(NoMicrophone, match="等不到"):
        mic.acquire()
    assert pactl.switches()[-1] == (f"bluez_card.{SPEAKER}", "a2dp-sink")


def test_pactl_failure_falls_back_to_default():
    def broken(*args):
        raise OSError("找不到 pactl")

    assert BluetoothMic(pactl=broken).acquire() is None  # 不影響聽寫，照常用系統預設


def test_does_not_restore_while_another_app_records():
    pactl = FakePactl([card(SPEAKER)])
    mic = BluetoothMic(restore_after_s=0, pactl=pactl, sleep=lambda s: None)
    mic.acquire()
    pactl.recording = [f"bluez_input.{SPEAKER}.0"]  # 例如 Chrome 視訊也在用
    mic.release()
    assert pactl.switches() == [(f"bluez_card.{SPEAKER}", "headset-head-unit-msbc")]  # 不切回
    pactl.recording = []
    mic._restore_later()  # 稍後重試時沒人在用了
    assert pactl.switches()[-1] == (f"bluez_card.{SPEAKER}", "a2dp-sink")
    mic.close()


class FakeService:
    """代替 bt-audio-profile 的協調服務。"""

    def __init__(self, result="bluez_input.X.0"):
        self.result = result
        self.calls = []

    def bt_acquire(self, who):
        self.calls.append(("acquire", who))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def bt_release(self, restore_after_s):
        self.calls.append(("release", restore_after_s))


def test_uses_coordinator_service_when_installed():
    from danwen.btmic import NoMicrophone

    pactl = FakePactl([card(SPEAKER)])
    service = FakeService()
    mic = BluetoothMic(restore_after_s=5, pactl=pactl, remote=lambda: service)
    notified = []
    assert mic.acquire(on_switching=lambda: notified.append(True)) == "bluez_input.X.0"
    assert notified == [True]  # 預設輸入不是麥克風：提示要稍等
    mic.release()
    assert service.calls == [("acquire", "danwen"), ("release", 5)]
    assert pactl.switches() == []  # 由協調服務切換，danwen 自己不切

    service.result = NoMicrophone("沒有已連線的藍牙音訊裝置")
    with pytest.raises(NoMicrophone):
        mic.acquire()


def test_falls_back_when_service_missing():
    from danwen.btmic import ServiceUnavailable

    pactl = FakePactl([card(SPEAKER)])
    mic = BluetoothMic(restore_after_s=0, pactl=pactl, sleep=lambda s: None,
                       remote=lambda: FakeService(ServiceUnavailable("ServiceUnknown")))
    assert mic.acquire() == f"bluez_input.{SPEAKER}.0"  # 沒裝協調服務：內建的切換
    mic.release()
    assert pactl.switches()[-1] == (f"bluez_card.{SPEAKER}", "a2dp-sink")
