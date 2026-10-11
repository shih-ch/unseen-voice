"""字典建議：從歷史紀錄找出可能聽錯的詞，列給使用者確認後才加進替換字典（不會自動加）。

三個來源：
- 重講：90 秒內把同一句再講一次（相似度 ≥ 0.6），兩次不一樣的地方。不知道哪次才對，由使用者選方向
- 整理前後：整理模式把詞改掉（例如同音字）。翻譯、口誤修正（改成口述裡其他地方說過的詞）不算
- LLM（選用，--llm）：把最近的聽寫文字交給整理用的 LLM，找出同音字、被聽成中文的英文術語等。
  會把文字送到 LLM（方案 B 是雲端 Groq），所以只在使用者明確要求時才做

使用者「不要再提」的組合記在 ~/.local/state/danwen/dict-ignored.json。
"""

from __future__ import annotations

import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from . import paths
from .history import Entry

log = logging.getLogger(__name__)

IGNORED_FILE = paths.STATE_DIR / "dict-ignored.json"
REDICTATION_GAP_S = 90
MIN_SIMILARITY = 0.6
LLM_ENTRIES = 20

_TOKEN = re.compile(r"[A-Za-z0-9]+|[㐀-䶿一-鿿]|\S")
_CJK = re.compile(r"[㐀-䶿一-鿿]")
_WORDY = re.compile(r"[A-Za-z㐀-䶿一-鿿]")
_SELF_CORRECTION = re.compile("不對|應該是|我是說|更正")
_NUMERALS = re.compile(r"[0-9零〇一二兩三四五六七八九十百千萬\s]")


@dataclass
class Suggestion:
    wrong: str
    right: str
    source: str  # 重講／整理／LLM
    entries: list[int] = field(default_factory=list)
    reason: str = ""

    @property
    def two_way(self) -> bool:
        """重講時不知道哪次才對：讓使用者選方向。"""
        return self.source == "重講"

    def key(self) -> tuple[str, str]:
        return tuple(sorted((self.wrong, self.right))) if self.two_way else (self.wrong, self.right)


def _units(text: str) -> int:
    """長度：中文一字算一個、英文一個單字算一個。"""
    return len(_CJK.findall(text)) + len(re.findall(r"[A-Za-z0-9]+", text))


def _join(tokens: list[str]) -> str:
    return "".join(tokens) if all(_CJK.match(t) for t in tokens) else " ".join(tokens)


def replaced_words(a: str, b: str) -> list[tuple[str, str]]:
    """兩段文字裡被替換的詞（中文以字、英文以單字比對）；只換一個中文字時連同相鄰的字，湊成詞。"""
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    pairs = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ta, tb, autojunk=False).get_opcodes():
        if op != "replace":
            continue
        if i2 - i1 == 1 and j2 - j1 == 1 and _CJK.match(ta[i1]):
            if i2 < len(ta) and j2 < len(tb) and ta[i2] == tb[j2] and _CJK.match(ta[i2]):
                i2, j2 = i2 + 1, j2 + 1
            elif i1 > 0 and j1 > 0 and ta[i1 - 1] == tb[j1 - 1] and _CJK.match(ta[i1 - 1]):
                i1, j1 = i1 - 1, j1 - 1
        wrong, right = _join(ta[i1:i2]), _join(tb[j1:j2])
        if not _WORDY.search(wrong + right) or wrong.lower() == right.lower():
            continue  # 只差標點、數字或大小寫
        if _NUMERALS.sub("", wrong) == _NUMERALS.sub("", right):
            continue  # 只是數字寫法不同（三點 → 3 點）
        if _units(wrong) > 6 or _units(right) > 8:
            continue  # 太長多半是換了說法，不是聽錯
        pairs.append((wrong, right))
    return pairs


def _strip(text: str) -> str:
    return text.strip().strip("。？！，、")


def from_redictation(entries: list[Entry]) -> list[Suggestion]:
    """entries：舊的在前。"""
    found = []
    for prev, cur in zip(entries, entries[1:]):
        try:
            gap = (datetime.fromisoformat(cur.time) - datetime.fromisoformat(prev.time)).total_seconds()
        except ValueError:
            continue
        a, b = _strip(prev.raw), _strip(cur.raw)
        if gap > REDICTATION_GAP_S or min(len(a), len(b)) < 3:
            continue
        if difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() < MIN_SIMILARITY:
            continue
        found += [Suggestion(w, r, "重講", [prev.id, cur.id]) for w, r in replaced_words(a, b)]
    return found


def is_translation(mode: str) -> bool:
    """翻譯類的小紙條（postprocess: false）：整理前後本來就不同語言，不算聽錯。"""
    from . import refine
    from .config import ConfigError

    try:
        return not refine.load_prompt(mode).postprocess
    except ConfigError:
        return mode.startswith(refine.TRANSLATE)


def collect(entries: list[Entry], refiner=None) -> list[Suggestion]:
    """entries：舊的在前。refiner 不是 None 時也請 LLM 找（會送出文字）。"""
    found = from_redictation(entries) + from_refine(entries, is_translation)
    if refiner is not None:
        found += from_llm(entries, refiner)
    return found


def from_refine(entries: list[Entry], is_translation) -> list[Suggestion]:
    found = []
    for e in entries:
        if not e.mode or e.raw == e.text or is_translation(e.mode):
            continue
        for wrong, right in replaced_words(e.raw, e.text):
            # 口誤修正（「三點，不對，四點」→「四點」）：改成的詞本來就在口述裡，不是聽錯
            if right in e.raw or _SELF_CORRECTION.search(e.raw):
                continue
            found.append(Suggestion(wrong, right, "整理", [e.id]))
    return found


_LLM_SYSTEM = """你是語音辨識的校對員。使用者會給你幾段語音辨識的結果（台灣繁體中文，可能夾雜英文），
找出「聽錯」的詞：同音或近音的錯字、被聽成中文的英文術語或產品名稱（例如「無邦圖」應該是「Ubuntu」）。
規則：
1. wrong 必須逐字出現在辨識結果裡；right 是應該的寫法。
2. 只列有把握的；用詞、語氣、標點、口語的說法都不要改。沒有就輸出 []。
3. 最多 10 筆，只輸出 JSON 陣列，例如：[{"wrong": "無邦圖", "right": "Ubuntu", "reason": "作業系統名稱"}]"""


def from_llm(entries: list[Entry], refiner) -> list[Suggestion]:
    """請 LLM 看最近的聽寫文字；回覆格式不對或內容對不上時略過。"""
    recent = [e for e in entries if _strip(e.raw)][-LLM_ENTRIES:]
    if not recent:
        return []
    text = "\n".join(f"#{e.id}：{e.raw}" for e in recent)
    reply = refiner.chat([{"role": "system", "content": _LLM_SYSTEM}, {"role": "user", "content": text}])
    match = re.search(r"\[.*\]", reply, re.S)
    try:
        items = json.loads(match.group(0)) if match else []
    except json.JSONDecodeError:
        log.warning("LLM 的字典建議不是 JSON：%s", reply[:200])
        return []
    found = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        wrong, right = str(item.get("wrong", "")).strip(), str(item.get("right", "")).strip()
        if not wrong or not right or wrong == right or len(wrong) > 12 or len(right) > 30:
            continue
        ids = [e.id for e in recent if wrong in e.raw]
        if ids:  # LLM 編出來、辨識結果裡沒有的詞不採用
            found.append(Suggestion(wrong, right, "LLM", ids, str(item.get("reason", ""))))
    return found


def load_ignored() -> set[tuple[str, str]]:
    try:
        return {tuple(pair) for pair in json.loads(IGNORED_FILE.read_text(encoding="utf-8"))}
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return set()


def ignore(suggestion: Suggestion) -> None:
    ignored = load_ignored() | {suggestion.key()}
    IGNORED_FILE.parent.mkdir(parents=True, exist_ok=True)
    IGNORED_FILE.write_text(json.dumps(sorted(ignored), ensure_ascii=False), encoding="utf-8")


def merge(suggestions: list[Suggestion], dictionary: dict[str, str]) -> list[Suggestion]:
    """合併重複的、去掉已經在字典裡或使用者說不要再提的。"""
    ignored = load_ignored()
    merged: dict[tuple[str, str], Suggestion] = {}
    for s in suggestions:
        if s.key() in ignored or dictionary.get(s.wrong) == s.right:
            continue
        if s.two_way and (dictionary.get(s.right) == s.wrong):
            continue
        if s.key() in merged:
            merged[s.key()].entries = sorted(set(merged[s.key()].entries + s.entries))
        else:
            merged[s.key()] = s
    return list(merged.values())
