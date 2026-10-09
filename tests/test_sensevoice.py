from danwen.asr.sensevoice import _join


def test_join_segments():
    assert _join(["今天開會。", "記得帶筆電。"]) == "今天開會。記得帶筆電。"
    assert _join(["把這個PR merge", "to main"]) == "把這個PR merge to main"  # 英文之間補空白
    assert _join(["", "  ", "只有這段。"]) == "只有這段。"
