"""後處理：去除 SenseVoice 標籤 → OpenCC 轉繁體 → 使用者替換字典。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from . import paths

log = logging.getLogger(__name__)

# SenseVoice 原始輸出的語言／情緒／事件標籤，例如 <|zh|><|NEUTRAL|><|Speech|><|withitn|>
_TAG_RE = re.compile(r"<\|[^|>]*\|>")
# FunASR 的 rich_transcription_postprocess 會把情緒／事件標籤換成這些 emoji
_TAG_EMOJI_RE = re.compile("[😊😔😡😰🤢😮🎼👏😀😭🤧😷❓]")


def strip_tags(text: str) -> str:
    return _TAG_EMOJI_RE.sub("", _TAG_RE.sub("", text)).strip()


class Replacements:
    """替換字典；檔案修改後下次使用時自動重新載入。"""

    def __init__(self, path: Path):
        self.path = path
        self._mtime: float | None = None
        self._table: dict[str, str] = {}
        self._pattern: re.Pattern[str] | None = None

    def _reload_if_changed(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            mtime = None
        if mtime == self._mtime:
            return
        self._mtime = mtime
        table: dict[str, str] = {}
        if mtime is not None:
            try:
                data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
                if not isinstance(data, dict):
                    raise ValueError("應該是「辨識結果: 想要的文字」的對應表")
                table = {str(k): "" if v is None else str(v) for k, v in data.items() if str(k)}
            except (yaml.YAMLError, ValueError) as e:
                log.error("替換字典 %s 格式錯誤，暫不套用：%s", self.path, e)
                table = {}
        self._table = table
        # 長的詞優先；單一 regex 一次掃過，不會連鎖替換
        keys = sorted(table, key=len, reverse=True)
        self._pattern = re.compile("|".join(map(re.escape, keys))) if keys else None
        log.info("載入替換字典 %s（%d 條）", self.path, len(table))

    def apply(self, text: str) -> str:
        self._reload_if_changed()
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda m: self._table[m.group(0)], text)


def default_replacements_path() -> Path:
    if paths.REPLACEMENTS_FILE.exists():
        return paths.REPLACEMENTS_FILE
    return paths.DATA_DIR / "replacements.yaml"


class PostProcessor:
    def __init__(self, opencc_config: str = "s2twp", replacements_path: Path | None = None):
        self._converter = None
        if opencc_config:
            import opencc

            self._converter = opencc.OpenCC(opencc_config)
        self.replacements = Replacements(replacements_path or default_replacements_path())

    def __call__(self, text: str) -> str:
        text = strip_tags(text)
        if self._converter is not None:
            text = self._converter.convert(text)
        return self.replacements.apply(text).strip()
