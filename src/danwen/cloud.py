"""雲端：語音辨識與整理模式可改用雲端服務（Groq、OpenAI、Cloudflare 或任何 OpenAI 相容服務）。

用「方案」決定哪些部分走雲端：
  A local       全部本機
  B hybrid      本機辨識＋Groq 整理（預設；只有整理模式的文字會送出，沒有金鑰時整理改用本機）
  C groq        全部用 Groq
  D cloudflare  全部用 Cloudflare
  E custom      依設定檔 cloud 區段的 provider、use_for_asr、use_for_refine
執行中可用 `danwen cloud <方案>` 或 GNOME extension 選單切換，狀態存在 ~/.local/state/danwen/cloud
（沒有這個檔案時依設定檔的 cloud.plan）。
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


def make_refiner(cfg: Config, cloud_cfg: CloudConfig | None = None) -> Refiner:
    """整理模式改用雲端 LLM（OpenAI 相容介面），其餘設定（上下文等）沿用 refine。"""
    from .refine import Refiner

    endpoint = resolve(cloud_cfg or cfg.cloud)
    refine_cfg = dataclasses.replace(
        cfg.refine, provider="openai", base_url=endpoint.llm_base_url,
        model=endpoint.llm_model, timeout_s=cfg.cloud.timeout_s,
    )
    return Refiner(refine_cfg, api_key=lambda: api_key(endpoint.provider), extra=endpoint.llm_extra)


def choose_refiner(cfg: Config) -> tuple[Refiner, str]:
    """依目前方案選擇整理用的 LLM，回傳（Refiner, 說明）。
    方案要雲端但不能用時：refine.local_fallback 為 true 改用本機，否則丟出 CloudError（不喚醒 Ollama）。"""
    from .refine import Refiner

    active = resolve_plan(cfg.cloud)
    if active.refine:
        try:
            check_ready(active.cloud)
            return make_refiner(cfg, active.cloud), f"雲端 {label(active.cloud)}"
        except (CloudError, ConfigError) as e:
            if not cfg.refine.local_fallback:
                raise CloudError(f"方案 {active.title}：{e}") from e
    return Refiner(cfg.refine), "本機"


def label(cfg: CloudConfig) -> str:
    preset = PRESETS.get(cfg.provider)
    return preset.label if preset else cfg.provider


# ---- 方案 ----


@dataclass(frozen=True)
class Plan:
    name: str
    letter: str
    label: str
    provider: str | None  # None＝依設定檔的 cloud.provider（custom）
    asr: bool | None  # None＝依設定檔的 cloud.use_for_asr
    refine: bool | None  # None＝依設定檔的 cloud.use_for_refine


PLANS = {
    "local": Plan("local", "A", "全部本機", None, False, False),
    "hybrid": Plan("hybrid", "B", "本機辨識＋Groq 整理", "groq", False, True),
    "groq": Plan("groq", "C", "全部用 Groq", "groq", True, True),
    "cloudflare": Plan("cloudflare", "D", "全部用 Cloudflare", "cloudflare", True, True),
    "custom": Plan("custom", "E", "自訂（依設定檔）", None, None, None),
}
DEFAULT_PLAN = "hybrid"


@dataclass(frozen=True)
class ActivePlan:
    plan: Plan
    cloud: CloudConfig  # 套用方案後的雲端設定（服務商、網址、模型）
    asr: bool  # 語音辨識走雲端
    refine: bool  # 整理模式走雲端

    @property
    def title(self) -> str:
        return f"{self.plan.letter} {self.plan.label}"

    @property
    def uses_cloud(self) -> bool:
        return self.asr or self.refine


def plan_name(name: str) -> str:
    """接受方案名稱或代號（A～E，不分大小寫）。"""
    for plan in PLANS.values():
        if name.lower() in (plan.name, plan.letter.lower()):
            return plan.name
    raise ConfigError(f"沒有「{name}」這個方案（可用：{'、'.join(f'{p.letter} {p.name}' for p in PLANS.values())}）")


def current_plan_name(cfg: CloudConfig) -> str:
    try:
        value = STATE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return cfg.plan
    if value == "off":  # 舊版存的是 on／off
        return "local"
    if value == "on":
        return cfg.plan if cfg.plan != "local" else DEFAULT_PLAN
    return value if value in PLANS else cfg.plan


def resolve_plan(cfg: CloudConfig, name: str | None = None) -> ActivePlan:
    plan = PLANS[plan_name(name or current_plan_name(cfg))]
    cloud_cfg = cfg
    if plan.provider is not None and plan.provider != cfg.provider:
        # 換了服務商：設定檔裡給原服務商的網址與模型不適用，改用新服務商的預設
        cloud_cfg = dataclasses.replace(cfg, provider=plan.provider, base_url=None, asr_model=None, llm_model=None)
    return ActivePlan(
        plan=plan,
        cloud=cloud_cfg,
        asr=cfg.use_for_asr if plan.asr is None else plan.asr,
        refine=cfg.use_for_refine if plan.refine is None else plan.refine,
    )


def check_plan(active: ActivePlan) -> None:
    """切換前檢查：方案用到雲端時，設定要完整、鑰匙圈要有金鑰（不連線）。"""
    if active.uses_cloud:
        check_ready(active.cloud)


def set_plan(name: str) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(plan_name(name) + "\n", encoding="utf-8")
