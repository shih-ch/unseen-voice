"""字典建議：重講、整理前後、LLM 三種來源，以及合併、不要再提、命令列。"""

import pytest

from danwen import cli, paths, suggest
from danwen.history import Entry, History
from danwen.suggest import Suggestion, from_llm, from_redictation, from_refine, merge, replaced_words


def entry(n, raw, text=None, mode=None, time="2026-10-11T10:00:00"):
    return Entry(n, time, 1.0, "sensevoice", raw, text or raw, mode)


def test_replaced_words():
    assert replaced_words("你上次有提過怎麼修改籤島城市", "你上次有提過要怎麼修改前島城市") == [("籤島", "前島")]
    assert replaced_words("這個 intlation 失敗", "這個 installation 失敗") == [("intlation", "installation")]
    assert replaced_words("今天三點開會。", "今天3點開會！") == []  # 只差數字寫法、標點
    assert replaced_words("PR", "pr") == []


def test_redictation_needs_close_in_time_and_similar():
    near = [entry(1, "月風魔傳。", time="2026-10-11T10:00:00"), entry(2, "嶽風魔傳。", time="2026-10-11T10:00:40")]
    s = from_redictation(near)
    assert [(x.wrong, x.right, x.entries) for x in s] == [("月風", "嶽風", [1, 2])] and s[0].two_way
    far = [near[0], entry(2, "嶽風魔傳。", time="2026-10-11T10:05:00")]
    assert from_redictation(far) == []  # 隔太久，不是重講
    rephrased = [entry(1, "如何讓藍牙喇叭不要斷線", time="2026-10-11T10:00:00"),
                 entry(2, "藍牙喇叭為什麼一直斷", time="2026-10-11T10:00:10")]
    assert from_redictation(rephrased) == []  # 換了說法


def test_refine_differences_skip_self_correction_and_translation():
    is_translation = lambda mode: mode.startswith("翻譯")  # noqa: E731
    fixed = entry(1, "請寄給黃寶西。", "請寄給黃保翕。", mode="日常")
    corrected = entry(2, "三點開會，不對，應該是四點。", "四點開會。", mode="日常")
    translated = entry(3, "月風魔傳。", "月風魔伝。", mode="翻譯（日文）")
    found = from_refine([fixed, corrected, translated], is_translation)
    # 只比對有差異的字，不知道人名從哪裡開始；「寶西→保翕」當字典規則一樣有效
    assert [(s.wrong, s.right, s.source) for s in found] == [("寶西", "保翕", "整理")]


class FakeRefiner:
    def __init__(self, reply):
        self.reply = reply
        self.messages = None

    def chat(self, messages):
        self.messages = messages
        return self.reply


def test_llm_suggestions_are_checked_against_the_text():
    entries = [entry(1, "如何在無邦圖上面安裝套件？"), entry(2, "今天天氣很好。")]
    reply = """```json
[{"wrong": "無邦圖", "right": "Ubuntu", "reason": "作業系統"},
 {"wrong": "根本沒出現", "right": "編的", "reason": "LLM 亂編"},
 {"wrong": "天氣", "right": "天氣", "reason": "沒改"}]
```"""
    refiner = FakeRefiner(reply)
    found = from_llm(entries, refiner)
    assert [(s.wrong, s.right, s.entries, s.reason) for s in found] == [("無邦圖", "Ubuntu", [1], "作業系統")]
    assert "#1：如何在無邦圖" in refiner.messages[-1]["content"]
    assert from_llm(entries, FakeRefiner("我覺得沒有錯字")) == []  # 不是 JSON


def test_merge_skips_dictionary_and_ignored():
    a = Suggestion("月風", "嶽風", "重講", [1, 2])
    b = Suggestion("嶽風", "月風", "重講", [5, 6])  # 同一組、方向相反
    c = Suggestion("無邦圖", "Ubuntu", "LLM", [3])
    d = Suggestion("酷伯內提斯", "Kubernetes", "LLM", [4])
    merged = merge([a, b, c, d], {"酷伯內提斯": "Kubernetes"})  # d 已經在字典裡
    assert [(s.wrong, s.entries) for s in merged] == [("月風", [1, 2, 5, 6]), ("無邦圖", [3])]
    suggest.ignore(merged[0])  # 不要再提
    assert [s.wrong for s in merge([a, b, c], {})] == ["無邦圖"]


def test_cli_suggest_lists_commands_without_terminal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    h = History(size=10)
    h.add(duration_s=1.0, backend="sensevoice", raw="你上次有提過怎麼修改籤島城市", text="你上次有提過怎麼修改籤島城市")
    h.add(duration_s=1.0, backend="sensevoice", raw="你上次有提過要怎麼修改前島城市", text="你上次有提過要怎麼修改前島城市")
    assert cli.main(["dict", "suggest"]) == 0
    out = capsys.readouterr().out
    assert "「籤島」和「前島」" in out and "danwen dict add 籤島 前島" in out and "danwen dict add 前島 籤島" in out


def test_cli_suggest_llm_needs_confirmation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.setattr("danwen.cloud.choose_refiner", lambda cfg: (FakeRefiner("[]"), "雲端 Groq"))
    assert cli.main(["dict", "suggest", "--llm"]) == 2  # 沒有終端機可以詢問，也沒加 --yes：不送出
    assert "會把最近" in capsys.readouterr().out
    assert cli.main(["dict", "suggest", "--llm", "--yes"]) == 0


@pytest.fixture(autouse=True)
def no_real_history(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "HISTORY_DIR", tmp_path / "history")
