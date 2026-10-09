"""設定：內建預設值，再以 ~/.config/danwen/config.yaml 覆蓋。

設定檔只需寫要改的項目；拼錯的項目名稱會直接報錯，避免設定默默沒生效。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import paths


class ConfigError(ValueError):
    pass


@dataclass
class HotkeyConfig:
    key: str = "KEY_RIGHTCTRL"
    hold_ms: int = 300


@dataclass
class AudioConfig:
    device: str | int | None = None
    sample_rate: int = 16000
    min_duration_s: float = 0.4
    max_duration_s: float = 120.0
    silence_dbfs: float = -50.0


@dataclass
class SenseVoiceConfig:
    num_threads: int = 4
    use_itn: bool = True


@dataclass
class WhisperServerConfig:
    url: str = "http://127.0.0.1:8178"
    language: str = "zh"
    prompt: str = "以下是繁體中文的句子。"
    timeout_s: float = 30.0
    autostart: bool = True


@dataclass
class ASRConfig:
    backend: str = "sensevoice"
    sensevoice: SenseVoiceConfig = field(default_factory=SenseVoiceConfig)
    whisper_server: WhisperServerConfig = field(default_factory=WhisperServerConfig)


@dataclass
class PostprocessConfig:
    opencc: str = "s2twp"
    replacements: str | None = None


@dataclass
class OutputConfig:
    restore_clipboard: bool = True
    restore_delay_ms: int = 500


@dataclass
class FeedbackConfig:
    sounds: bool = True
    notify_errors: bool = True


@dataclass
class LogConfig:
    log_text: bool = False


@dataclass
class Config:
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    postprocess: PostprocessConfig = field(default_factory=PostprocessConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    log: LogConfig = field(default_factory=LogConfig)


def _apply(obj: object, data: object, where: str) -> None:
    if not isinstance(data, dict):
        raise ConfigError(f"{where or '設定檔'} 應該是「名稱: 值」的對應表")
    fields = {f.name for f in dataclasses.fields(obj)}
    for key, value in data.items():
        if key not in fields:
            raise ConfigError(f"未知的設定項目：{where}{key}")
        current = getattr(obj, key)
        if dataclasses.is_dataclass(current):
            _apply(current, value or {}, f"{where}{key}.")
        else:
            setattr(obj, key, value)


def load(path: Path | None = None) -> Config:
    cfg = Config()
    path = path or paths.CONFIG_FILE
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"{path} 格式錯誤：{e}") from e
        _apply(cfg, data, "")
    return cfg
