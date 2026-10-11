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
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from . import paths
from .config import CloudConfig, Config, ConfigError

if TYPE_CHECKING:
    from .refine import Refiner

STATE_FILE = paths.STATE_DIR / "cloud"
MODELS_FILE = paths.STATE_DIR / "models.json"  # 每家服務商選用的模型（設定畫面或 danwen models set）


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
    "groq": Preset("Groq", "https://api.groq.com/openai/v1", "whisper-large-v3-turbo", "qwen/qwen3.8-27b", "openai"),
    "openai": Preset("OpenAI", "https://api.openai.com/v1", "gpt-transcribe", "gpt-4o-mini", "openai"),
    "cloudflare": Preset("Cloudflare", "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai",
                         # 整理模型實測（2026-10-11）：llama-3.3-70b 6/6、平均 1 秒；qwen3-30b 6 次有 2 次被防呆擋下
                         "@cf/openai/whisper-large-v3-turbo", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "cloudflare",
                         llm_suffix="/v1"),
    "custom": Preset("自訂", "", "", "", "openai"),
}


@dataclass(frozen=True)
class ModelChoice:
    id: str
    label: str
    note: str = ""  # 實測結果，顯示在選單
    extra: dict = field(default_factory=dict)  # 呼叫時附加的參數


# 選單裡的模型：只列實測可用的（2026-10-11，日常 6 句 × 2 輪，Qwen 與 gpt-oss-120b 另測 Email／社群／翻譯 6 句）
MODELS = {
    "groq": {
        "llm": [
            # 思考型模型：關掉思考，不然偶爾會想太久（實測一次 30 秒逾時）
            ModelChoice("qwen/qwen3.8-27b", "Qwen3.8 27B", "最快：實測 18/18、每句約 0.3 秒",
                        {"reasoning_effort": "none"}),
            ModelChoice("openai/gpt-oss-120b", "gpt-oss-120b", "實測 18/18、每句約 0.7 秒", {"reasoning_effort": "low"}),
            ModelChoice("openai/gpt-oss-20b", "gpt-oss-20b", "實測 10/12，口誤修正加條列偶爾被防呆擋下",
                        {"reasoning_effort": "low"}),
        ],
        # whisper-large-v3 實測比 turbo 慢、還漏字，不列
        "asr": [ModelChoice("whisper-large-v3-turbo", "Whisper large-v3 turbo", "10 秒錄音約 0.5 秒")],
    },
    "cloudflare": {
        # qwen3-30b 6 次有 2 次被擋、gpt-oss-20b 常沒有輸出、mistral-small 會把「好」翻成 Hello，都不列
        "llm": [ModelChoice("@cf/meta/llama-3.3-70b-instruct-fp8-fast", "Llama 3.3 70B", "實測 12/12、每句約 0.9 秒")],
        "asr": [ModelChoice("@cf/openai/whisper-large-v3-turbo", "Whisper large-v3 turbo", "10 秒錄音約 1.8 秒")],
    },
}


def model_choice(provider: str, kind: str, model: str) -> ModelChoice | None:
    return next((m for m in MODELS.get(provider, {}).get(kind, []) if m.id == model), None)


def model_extra(provider: str, model: str) -> dict:
    """模型專屬的參數；不在清單裡的 gpt-oss 也設推理強度低。"""
    choice = model_choice(provider, "llm", model)
    if choice is not None:
        return dict(choice.extra)
    return {"reasoning_effort": "low"} if provider == "groq" and model.startswith("openai/gpt-oss") else {}


def chosen_models() -> dict:
    try:
        data = json.loads(MODELS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def chosen_model(provider: str, kind: str) -> str | None:
    value = chosen_models().get(provider, {})
    return value.get(kind) if isinstance(value, dict) else None


def set_model(provider: str, kind: str, model: str) -> None:
    """選用某家服務商的模型（只能選選單裡的）。"""
    if model_choice(provider, kind, model) is None:
        options = "、".join(m.id for m in MODELS.get(provider, {}).get(kind, [])) or "無"
        raise ConfigError(f"{provider} 的{'整理' if kind == 'llm' else '辨識'}模型只能選：{options}")
    data = chosen_models()
    data.setdefault(provider, {})[kind] = model
    MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODELS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


TEST_SENTENCE = "嗯，那個我們明天下午三點開會，不對，應該是四點，然後要討論兩件事，第一是預算，第二是人力。"


def ollama_models(base_url: str) -> list[str]:
    """本機 Ollama 已安裝的模型；Ollama 沒在執行時回傳空清單。"""
    import urllib.request

    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=2) as r:
            return sorted(m["name"] for m in json.load(r).get("models", []))
    except (OSError, ValueError, KeyError):
        return []


def model_menu(cfg: Config) -> dict:
    """設定畫面與 danwen models 用的選單：{服務商: {label, kinds: {llm|asr: {choices, current}}}}。"""
    menu = {}
    for provider, kinds in MODELS.items():
        preset = PRESETS[provider]
        menu[provider] = {"label": preset.label, "kinds": {
            kind: {
                "choices": [dataclasses.asdict(m) for m in choices],
                "current": chosen_model(provider, kind) or (preset.llm_model if kind == "llm" else preset.asr_model),
            } for kind, choices in kinds.items()
        }}
    installed = ollama_models(cfg.refine.base_url) if cfg.refine.provider == "ollama" else []
    if cfg.refine.model not in installed:
        installed.insert(0, cfg.refine.model)
    menu["ollama"] = {"label": "本機 Ollama", "kinds": {"llm": {
        "choices": [{"id": m, "label": m, "note": "預設" if m == "qwen3:4b-instruct-2507-q4_K_M" else "", "extra": {}}
                    for m in installed],
        "current": cfg.refine.model,
    }}}
    return menu


def test_model(cfg: Config, provider: str, post=None) -> tuple[float, str]:
    """用一句固定的測試句整理一次，回傳（秒數, 結果）。provider：groq、cloudflare 或 ollama。"""
    import time

    from .refine import Refiner

    if provider == "ollama":
        refiner = Refiner(cfg.refine)
    else:
        active = resolve_plan(cfg.cloud, provider)
        check_ready(active.cloud)
        refiner = make_refiner(cfg, active.cloud)
    start = time.monotonic()
    output = refiner.refine(TEST_SENTENCE, "日常", post=post)
    return time.monotonic() - start, output


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
    # 設定檔明確指定的優先（自訂方案用），其次是選單選的，最後是預設
    asr_model = cfg.asr_model or chosen_model(cfg.provider, "asr") or preset.asr_model
    llm_model = cfg.llm_model or chosen_model(cfg.provider, "llm") or preset.llm_model
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
        llm_extra={**preset.llm_extra, **model_extra(cfg.provider, llm_model)},
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
