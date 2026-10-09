from evdev import ecodes

from danwen.hotkey import KEY_DOWN, KEY_REPEAT, KEY_UP, Action, HoldDetector, key_code

HOT = ecodes.KEY_RIGHTCTRL
SPACE = ecodes.KEY_SPACE


def make() -> HoldDetector:
    return HoldDetector(HOT, hold_s=0.3, max_s=10.0)


def test_short_tap_does_nothing():
    d = make()
    assert d.on_key(HOT, KEY_DOWN, 0.0) is None
    assert d.on_tick(0.2) is None
    assert d.on_key(HOT, KEY_UP, 0.25) is None
    assert d.on_tick(1.0) is None


def test_hold_starts_then_release_stops():
    d = make()
    d.on_key(HOT, KEY_DOWN, 0.0)
    assert d.deadline() == 0.3
    assert d.on_tick(0.31) is Action.START
    assert d.recording
    assert d.on_key(HOT, KEY_REPEAT, 0.5) is None
    assert d.on_key(HOT, KEY_UP, 2.0) is Action.STOP
    assert not d.recording


def test_combo_before_threshold_never_starts():
    # 例如右 Ctrl+Space（fcitx5 切換輸入法）
    d = make()
    d.on_key(HOT, KEY_DOWN, 0.0)
    assert d.on_key(SPACE, KEY_DOWN, 0.1) is None
    assert d.on_tick(0.5) is None
    d.on_key(SPACE, KEY_UP, 0.6)
    assert d.on_key(HOT, KEY_UP, 0.7) is None


def test_combo_while_recording_cancels():
    d = make()
    d.on_key(HOT, KEY_DOWN, 0.0)
    assert d.on_tick(0.3) is Action.START
    assert d.on_key(SPACE, KEY_DOWN, 1.0) is Action.CANCEL
    d.on_key(SPACE, KEY_UP, 1.1)
    assert d.on_key(HOT, KEY_UP, 1.2) is None
    # 回到初始狀態後可以再用
    d.on_key(HOT, KEY_DOWN, 2.0)
    assert d.on_tick(2.31) is Action.START


def test_hotkey_pressed_while_other_key_held_is_combo():
    # 例如 Shift+右Ctrl
    d = make()
    d.on_key(ecodes.KEY_LEFTSHIFT, KEY_DOWN, 0.0)
    d.on_key(HOT, KEY_DOWN, 0.1)
    assert d.on_tick(1.0) is None
    d.on_key(HOT, KEY_UP, 1.1)
    d.on_key(ecodes.KEY_LEFTSHIFT, KEY_UP, 1.2)
    d.on_key(HOT, KEY_DOWN, 2.0)
    assert d.on_tick(2.31) is Action.START


def test_mouse_buttons_are_not_other_keys():
    d = make()
    d.on_key(HOT, KEY_DOWN, 0.0)
    d.on_tick(0.3)
    assert d.on_key(ecodes.BTN_LEFT, KEY_DOWN, 0.5) is None
    assert d.recording


def test_max_duration_stops_and_waits_for_release():
    d = HoldDetector(HOT, hold_s=0.3, max_s=5.0)
    d.on_key(HOT, KEY_DOWN, 0.0)
    d.on_tick(0.3)
    assert d.on_tick(5.31) is Action.STOP
    assert d.on_key(HOT, KEY_UP, 6.0) is None
    d.on_key(HOT, KEY_DOWN, 7.0)
    assert d.on_tick(7.31) is Action.START


def test_reset_cancels_recording_and_clears_held_keys():
    d = make()
    d.on_key(SPACE, KEY_DOWN, 0.0)  # 鍵盤被拔掉，永遠收不到放開
    assert d.reset() is None
    d.on_key(HOT, KEY_DOWN, 1.0)
    assert d.on_tick(1.31) is Action.START
    assert d.reset() is Action.CANCEL
    assert not d.recording


def test_key_code():
    assert key_code("KEY_RIGHTCTRL") == HOT


def make_dt() -> HoldDetector:
    return HoldDetector(HOT, hold_s=0.3, max_s=10.0, double_tap_s=0.4)


def test_tap_then_hold_is_refine():
    d = make_dt()
    d.on_key(HOT, KEY_DOWN, 0.0)
    d.on_key(HOT, KEY_UP, 0.1)  # 短按一下
    d.on_key(HOT, KEY_DOWN, 0.3)  # 0.2 秒後再按住
    assert d.on_tick(0.61) is Action.START_REFINE
    assert d.on_key(HOT, KEY_UP, 2.0) is Action.STOP


def test_slow_second_press_is_normal_dictation():
    d = make_dt()
    d.on_key(HOT, KEY_DOWN, 0.0)
    d.on_key(HOT, KEY_UP, 0.1)
    d.on_key(HOT, KEY_DOWN, 0.6)  # 超過 0.4 秒才再按
    assert d.on_tick(0.91) is Action.START


def test_refine_only_applies_once():
    d = make_dt()
    d.on_key(HOT, KEY_DOWN, 0.0)
    d.on_key(HOT, KEY_UP, 0.1)
    d.on_key(HOT, KEY_DOWN, 0.2)
    assert d.on_tick(0.51) is Action.START_REFINE
    d.on_key(HOT, KEY_UP, 1.0)
    d.on_key(HOT, KEY_DOWN, 1.2)  # 錄音結束後再按住：不是短按後的連按
    assert d.on_tick(1.51) is Action.START


def test_other_key_between_taps_breaks_double_tap():
    d = make_dt()
    d.on_key(HOT, KEY_DOWN, 0.0)
    d.on_key(HOT, KEY_UP, 0.1)
    d.on_key(ecodes.KEY_A, KEY_DOWN, 0.15)
    d.on_key(ecodes.KEY_A, KEY_UP, 0.18)
    d.on_key(HOT, KEY_DOWN, 0.2)
    assert d.on_tick(0.51) is Action.START


def test_combo_tap_does_not_count_as_tap():
    d = make_dt()
    d.on_key(HOT, KEY_DOWN, 0.0)
    d.on_key(ecodes.KEY_C, KEY_DOWN, 0.05)  # 右 Ctrl+C
    d.on_key(ecodes.KEY_C, KEY_UP, 0.08)
    d.on_key(HOT, KEY_UP, 0.1)
    d.on_key(HOT, KEY_DOWN, 0.2)
    assert d.on_tick(0.51) is Action.START


def test_double_tap_disabled():
    d = make()  # double_tap_s=0
    d.on_key(HOT, KEY_DOWN, 0.0)
    d.on_key(HOT, KEY_UP, 0.1)
    d.on_key(HOT, KEY_DOWN, 0.2)
    assert d.on_tick(0.51) is Action.START


def make_long() -> HoldDetector:
    return HoldDetector(HOT, hold_s=0.3, max_s=10.0, double_tap_s=0.4, long_max_s=600.0)


def double_tap(d: HoldDetector, t: float = 0.0):
    d.on_key(HOT, KEY_DOWN, t)
    d.on_key(HOT, KEY_UP, t + 0.1)
    d.on_key(HOT, KEY_DOWN, t + 0.2)
    return d.on_key(HOT, KEY_UP, t + 0.25)


def test_double_tap_starts_long_and_tap_stops():
    d = make_long()
    assert double_tap(d) is Action.START_LONG
    assert d.recording
    assert d.on_tick(100.0) is None  # 不必按著，也不會因為放開而結束
    d.on_key(HOT, KEY_DOWN, 30.0)
    assert d.on_key(HOT, KEY_UP, 30.1) is Action.STOP
    assert not d.recording
    # 結束用的那一下不算短按，之後馬上按住是一般的快速模式
    d.on_key(HOT, KEY_DOWN, 30.3)
    assert d.on_tick(30.61) is Action.START


def test_long_ignores_other_keys_but_esc_cancels():
    d = make_long()
    double_tap(d)
    for key in (ecodes.KEY_A, ecodes.KEY_SPACE, ecodes.KEY_LEFTALT, ecodes.KEY_TAB):
        assert d.on_key(key, KEY_DOWN, 5.0) is None
        d.on_key(key, KEY_UP, 5.1)
    assert d.recording
    assert d.on_key(ecodes.KEY_ESC, KEY_DOWN, 6.0) is Action.CANCEL
    assert not d.recording


def test_hotkey_used_in_combo_during_long_does_not_stop():
    d = make_long()
    double_tap(d)
    d.on_key(HOT, KEY_DOWN, 5.0)
    d.on_key(ecodes.KEY_C, KEY_DOWN, 5.05)  # 右 Ctrl+C 複製東西
    d.on_key(ecodes.KEY_C, KEY_UP, 5.1)
    assert d.on_key(HOT, KEY_UP, 5.2) is None
    assert d.recording


def test_long_max_duration():
    d = make_long()
    double_tap(d)
    assert d.on_tick(600.3) is Action.STOP
    assert not d.recording


def test_tap_then_hold_is_still_refine_when_long_enabled():
    d = make_long()
    d.on_key(HOT, KEY_DOWN, 0.0)
    d.on_key(HOT, KEY_UP, 0.1)
    d.on_key(HOT, KEY_DOWN, 0.2)
    assert d.on_tick(0.51) is Action.START_REFINE


def test_long_disabled_double_tap_does_nothing():
    d = make_dt()  # long_max_s=0
    assert double_tap(d) is None
    assert not d.recording


def test_reset_cancels_long():
    d = make_long()
    double_tap(d)
    assert d.reset() is Action.CANCEL


def test_external_start_and_stop_long():
    d = make_long()
    assert d.start_long(0.0) is Action.START_LONG
    assert d.start_long(1.0) is None  # 已在錄音
    assert d.stop() is Action.STOP
    assert not d.recording
    assert d.stop() is None


def test_external_cancel_and_hold_mode():
    d = make_long()
    d.start_long(0.0)
    assert d.cancel() is Action.CANCEL
    d.on_key(HOT, KEY_DOWN, 1.0)
    d.on_tick(1.31)  # 按住錄音中
    assert d.stop() is Action.STOP
    assert d.on_key(HOT, KEY_UP, 2.0) is None  # 放開時不會再停一次
    assert not d.recording


def test_external_start_long_disabled():
    assert make_dt().start_long(0.0) is None
