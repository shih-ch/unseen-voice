"""Benchmark：對 wav 檔分別跑各個 backend，輸出耗時與文字，方便人工比較。"""

from __future__ import annotations

import time
from pathlib import Path

from .asr import ASRUnavailable, create_backend
from .audio import read_wav
from .cloud import CloudError
from .config import Config, ConfigError
from .postprocess import PostProcessor


def collect_wavs(inputs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in inputs:
        files.extend(sorted(p.glob("*.wav")) if p.is_dir() else [p])
    return files


def run(cfg: Config, inputs: list[Path], backends: list[str]) -> int:
    files = collect_wavs(inputs)
    if not files:
        print("找不到 wav 檔。請把錄音放進 samples/（16 kHz 單聲道 PCM 最理想）。")
        return 1
    clips = [(f, *read_wav(f)) for f in files]
    replacements = cfg.postprocess.replacements
    post = PostProcessor(cfg.postprocess.opencc, Path(replacements).expanduser() if replacements else None)
    name_width = max(len(f.name) for f in files)

    for name in backends:
        backend = create_backend(name, cfg)
        print(f"\n=== {backend.name} ===")
        t = time.monotonic()
        try:
            backend.prepare()
        except (ASRUnavailable, ConfigError) as e:
            print(f"略過：{e}")
            continue
        print(f"載入＋暖機：{time.monotonic() - t:.2f} 秒")
        total_audio = total_asr = 0.0
        try:
            for f, audio, sr in clips:
                duration = audio.size / sr
                t0 = time.monotonic()
                try:
                    raw = backend.transcribe(audio, sr)
                except CloudError as e:
                    print(f"{f.name}：{e}")
                    break
                t1 = time.monotonic()
                text = post(raw)
                t2 = time.monotonic()
                total_audio += duration
                total_asr += t1 - t0
                print(
                    f"{f.name:<{name_width}}  音訊 {duration:6.2f}s  ASR {t1 - t0:6.3f}s  "
                    f"RTF {(t1 - t0) / duration:5.3f}  後處理 {(t2 - t1) * 1000:5.1f}ms  {text}"
                )
        finally:
            backend.close()
        if total_audio:
            print(f"合計：音訊 {total_audio:.1f}s，ASR {total_asr:.2f}s，平均 RTF {total_asr / total_audio:.3f}")
    return 0
