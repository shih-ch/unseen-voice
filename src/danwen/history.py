"""歷史紀錄：最近幾次聽寫的文字（選用：錄音），可換小紙條重新整理而不必重錄。

存在 ~/.local/state/danwen/history/history.json，目錄與檔案都只有自己能讀。
格式刻意保持簡單（一個 JSON 陣列，新的在後），之後的 GNOME extension 也會使用這份資料。
常駐程式與 danwen history 指令可能同時寫入，以檔案鎖避免互相覆蓋。
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from . import paths
from .audio import to_wav_bytes


@dataclass
class Entry:
    id: int
    time: str  # ISO 8601，本地時間
    duration_s: float
    backend: str
    raw: str  # 辨識＋後處理後、整理前的文字（重新整理時用這份）
    text: str  # 實際貼上（或重新整理後）的文字
    mode: str | None = None  # 用了哪張小紙條；None＝快速模式或整理失敗
    redo_of: int | None = None  # 由哪一筆重新整理而來
    audio: str | None = None  # 錄音檔名（keep_audio 開啟時）


class History:
    FILE_NAME = "history.json"

    def __init__(self, directory: Path | None = None, size: int = 20, keep_audio: bool = False):
        self.directory = directory or paths.HISTORY_DIR
        self.size = size
        self.keep_audio = keep_audio
        self._file = self.directory / self.FILE_NAME

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        with open(self.directory / ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self) -> list[Entry]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        fields = {f.name for f in dataclasses.fields(Entry)}
        return [Entry(**{k: v for k, v in item.items() if k in fields}) for item in data]

    def _save(self, entries: list[Entry]) -> None:
        tmp = self._file.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([dataclasses.asdict(e) for e in entries], f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._file)

    def entries(self) -> list[Entry]:
        """新的在前。"""
        with self._locked():
            return list(reversed(self._load()))

    def get(self, entry_id: int | None = None) -> Entry:
        """指定編號；不指定則為最新一筆。"""
        entries = self.entries()
        if not entries:
            raise KeyError("還沒有任何歷史紀錄")
        if entry_id is None:
            return entries[0]
        for e in entries:
            if e.id == entry_id:
                return e
        raise KeyError(f"找不到第 {entry_id} 筆（可能已超過保留筆數）")

    def audio_path(self, entry: Entry) -> Path | None:
        return self.directory / entry.audio if entry.audio else None

    def add(
        self,
        *,
        duration_s: float,
        backend: str,
        raw: str,
        text: str,
        mode: str | None = None,
        redo_of: int | None = None,
        audio: np.ndarray | None = None,
        sample_rate: int = 16000,
    ) -> Entry | None:
        if self.size <= 0:
            return None
        with self._locked():
            entries = self._load()
            entry = Entry(
                id=(entries[-1].id + 1) if entries else 1,
                time=datetime.now().astimezone().isoformat(timespec="seconds"),
                duration_s=round(duration_s, 2),
                backend=backend,
                raw=raw,
                text=text,
                mode=mode,
                redo_of=redo_of,
            )
            if self.keep_audio and audio is not None:
                entry.audio = f"{entry.id}.wav"
                wav = self.directory / entry.audio
                fd = os.open(wav, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(to_wav_bytes(audio, sample_rate))
            entries.append(entry)
            for old in entries[: -self.size]:
                if old.audio:
                    (self.directory / old.audio).unlink(missing_ok=True)
            self._save(entries[-self.size :])
            return entry

    def clear(self) -> int:
        with self._locked():
            entries = self._load()
            for e in entries:
                if e.audio:
                    (self.directory / e.audio).unlink(missing_ok=True)
            self._save([])
            return len(entries)
