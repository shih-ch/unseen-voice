"""雲端：語音辨識與整理模式的 LLM 改用雲端服務（Groq、OpenAI、Cloudflare 或任何 OpenAI 相容服務）。

預設關閉；開啟後錄音與文字會送到所選的服務。執行中可用 `danwen cloud on/off` 或 GNOME extension 選單切換，
狀態存在 ~/.local/state/danwen/cloud（沒有這個檔案時依設定檔的 cloud.enabled）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import paths
from .config import CloudConfig, Config, ConfigError

if TYPE_CHECKING:
    from .refine import Refiner

STATE_FILE = paths.STATE_DIR / "cloud"


class CloudError(RuntimeError):
    pass


@dataclass(frozen=True)
class Preset:
    label: str
    base_url: str
    asr_model: str
    llm_model: str
    asr_api: str  # openai：/audio/transcriptions（multipart）；cloudflare：/run/<模型>（JSON＋base64）
    llm_suffix: str = ""  # OpenAI 相容 chat 介面相對 base_url 的位置
    llm_extra: dict = field(default_factory=dict)


PRESETS = {
    # gpt-oss 是推理模型，推理強度設為 low，避免為了「想」多花時間與費用
    "groq": Preset("Groq", "https://api.groq.com/openai/v1", "whisper-large-v3-turbo", "openai/gpt-oss-20b",
                   "openai", llm_extra={"reasoning_effort": "low"}),
    "openai": Preset("OpenAI", "https://api.openai.com/v1", "gpt-transcribe", "gpt-4o-mini", "openai"),
    "cloudflare": Preset("Cloudflare", "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai",
                         "@cf/openai/whisper-large-v3-turbo", "@cf/qwen/qwen3-30b-a3b-fp8", "cloudflare",
                         llm_suffix="/v1"),
    "custom": Preset("自訂", "", "", "", "openai"),
}


@dataclass(frozen=True)
class Endpoint:
    provider: str
    label: str
    base_url: str
    asr_model: str
    llm_model: str
    asr_api: str
    llm_base_url: str
    llm_extra: dict


def resolve(cfg: CloudConfig) -> Endpoint:
    """依設定組出實際要呼叫的網址與模型；設定不完整時丟出 ConfigError。"""
    preset = PRESETS.get(cfg.provider)
    if preset is None:
        raise ConfigError(f"cloud.provider 只能是 {'、'.join(PRESETS)}，不是 {cfg.provider}")
    base = (cfg.base_url or preset.base_url).rstrip("/")
    if "{account_id}" in base:
        if not cfg.account_id:
            raise ConfigError("使用 Cloudflare 需要在設定檔填 cloud.account_id")
        base = base.format(account_id=cfg.account_id)
    if not base:
        raise ConfigError("provider 為 custom 時需要設定 cloud.base_url")
    asr_model = cfg.asr_model or preset.asr_model
    llm_model = cfg.llm_model or preset.llm_model
    if not asr_model or not llm_model:
        raise ConfigError("provider 為 custom 時需要設定 cloud.asr_model 與 cloud.llm_model")
    return Endpoint(
        provider=cfg.provider,
        label=preset.label,
        base_url=base,
        asr_model=asr_model,
        llm_model=llm_model,
        asr_api=preset.asr_api,
        llm_base_url=base + preset.llm_suffix,
        llm_extra=dict(preset.llm_extra),
    )


def api_key(provider: str) -> str:
    """從 GNOME 鑰匙圈讀取 API Key；沒有設定或鑰匙圈鎖住時丟出 CloudError。"""
    from .credentials import CredentialError, KeyringStore

    try:
        key = KeyringStore().get(provider)
    except CredentialError as e:
        raise CloudError(str(e)) from e
    if not key:
        name = PRESETS[provider].label if provider in PRESETS else provider
        raise CloudError(f"還沒設定 {name} 的 API Key，請執行：danwen key set {provider}")
    return key


def check_ready(cfg: CloudConfig) -> Endpoint:
    """開啟雲端前的檢查：設定完整、鑰匙圈裡有金鑰（不連線）。"""
    endpoint = resolve(cfg)
    api_key(endpoint.provider)
    return endpoint


def make_refiner(cfg: Config) -> Refiner:
    """整理模式改用雲端 LLM（OpenAI 相容介面），其餘設定（上下文等）沿用 refine。"""
    from .refine import Refiner

    endpoint = resolve(cfg.cloud)
    refine_cfg = dataclasses.replace(
        cfg.refine, provider="openai", base_url=endpoint.llm_base_url,
        model=endpoint.llm_model, timeout_s=cfg.cloud.timeout_s,
    )
    return Refiner(refine_cfg, api_key=lambda: api_key(endpoint.provider), extra=endpoint.llm_extra)


def label(cfg: CloudConfig) -> str:
    preset = PRESETS.get(cfg.provider)
    return preset.label if preset else cfg.provider


def is_enabled(cfg: CloudConfig) -> bool:
    try:
        return STATE_FILE.read_text(encoding="utf-8").strip() == "on"
    except FileNotFoundError:
        return cfg.enabled


def set_enabled(on: bool) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text("on\n" if on else "off\n", encoding="utf-8")
