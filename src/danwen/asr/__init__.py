"""ASR backend：由設定檔的 asr.backend 切換。"""

from __future__ import annotations

from ..config import Config, ConfigError
from .base import ASRBackend, ASRUnavailable

BACKENDS = ("sensevoice", "whisper_server", "cloud")
# 規格文件裡的 A／B 也可以直接用；C＝雲端
ALIASES = {"a": "sensevoice", "b": "whisper_server", "whisper": "whisper_server", "c": "cloud"}


def resolve_name(name: str) -> str:
    name = ALIASES.get(name.lower(), name)
    if name not in BACKENDS:
        raise ConfigError(f"未知的 ASR backend：{name}（可用：{'、'.join(BACKENDS)}）")
    return name


def create_backend(name: str, cfg: Config) -> ASRBackend:
    name = resolve_name(name)
    # 延遲 import：不用的 backend 不必載入它的相依套件
    if name == "sensevoice":
        from .sensevoice import SenseVoiceBackend

        return SenseVoiceBackend(cfg.asr.sensevoice)
    if name == "cloud":
        from .cloud import CloudASRBackend

        return CloudASRBackend(cfg.cloud)
    from .whisper_server import WhisperServerBackend

    return WhisperServerBackend(cfg.asr.whisper_server)


__all__ = ["ASRBackend", "ASRUnavailable", "BACKENDS", "create_backend", "resolve_name"]
