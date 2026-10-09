import os

from danwen import paths
from danwen.postprocess import PostProcessor, Replacements, strip_tags


def test_strip_tags():
    assert strip_tags("<|zh|><|NEUTRAL|><|Speech|><|withitn|>開會。") == "開會。"
    assert strip_tags("😊大家好🎼") == "大家好"


def test_s2twp_and_mixed_english(tmp_path):
    empty = tmp_path / "r.yaml"
    empty.write_text("{}", encoding="utf-8")
    p = PostProcessor("s2twp", empty)
    assert p("把这个PR merge到main") == "把這個PR merge到main"
    assert p("这个软件的内存不够") == "這個軟體的記憶體不夠"


def test_bundled_replacements_prefer_tai():
    p = PostProcessor("s2twp", paths.DATA_DIR / "replacements.yaml")
    assert p("台湾的出租车") == "台灣的計程車"


def test_replacements_longest_first_and_no_chaining(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text("吉特: X\n吉特哈布: GitHub\nGitHub: 不該連鎖\n", encoding="utf-8")
    assert Replacements(f).apply("吉特哈布和吉特") == "GitHub和X"


def test_replacements_reload_on_change(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text("甲: 乙\n", encoding="utf-8")
    r = Replacements(f)
    assert r.apply("甲") == "乙"
    f.write_text("甲: 丙\n", encoding="utf-8")
    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + 1))
    assert r.apply("甲") == "丙"


def test_broken_replacements_file_is_ignored(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text("- 不是對應表\n", encoding="utf-8")
    assert Replacements(f).apply("原文") == "原文"
