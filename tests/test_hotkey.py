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
