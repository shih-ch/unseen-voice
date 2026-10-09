"""模型下載：首次使用時從 Hugging Face 下載到 ~/.cache/danwen/models，之後完全離線。"""

from __future__ import annotations

import logging
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    name: str
    repo: str
    files: tuple[str, ...]

    @property
    def dir(self) -> Path:
        return paths.MODELS_DIR / self.name

    def missing(self) -> list[str]:
        return [f for f in self.files if not (self.dir / f).exists()]


SENSEVOICE = ModelSpec(
    "sensevoice-small-2024-07-17",
    "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
    ("model.int8.onnx", "tokens.txt"),
)
# 長錄音切段用的語音偵測（VAD）
SILERO_VAD = ModelSpec("silero-vad", "csukuangfj/vad", ("silero_vad.onnx",))
# danwen-whisper.service 以固定路徑載入這個檔案
WHISPER_TURBO = ModelSpec(
    "whisper-large-v3-turbo",
    "ggerganov/whisper.cpp",
    ("ggml-large-v3-turbo-q5_0.bin",),
)


def ensure(spec: ModelSpec) -> Path:
    for name in spec.missing():
        endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
        _download(f"{endpoint}/{spec.repo}/resolve/main/{name}", spec.dir / name)
    return spec.dir


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    log.info("下載模型：%s", url)
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done, last_report = 0, time.monotonic()
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if total and time.monotonic() - last_report > 5:
                log.info("  %s：%d%%（%d / %d MB）", dest.name, done * 100 // total, done >> 20, total >> 20)
                last_report = time.monotonic()
    if total and done != total:
        tmp.unlink(missing_ok=True)
        raise OSError(f"{dest.name} 下載不完整（{done}/{total} bytes），請重試")
    tmp.replace(dest)
    log.info("  完成：%s", dest)
