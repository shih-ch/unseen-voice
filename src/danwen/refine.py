"""整理模式：把辨識結果交給 LLM，依「小紙條」（提示詞）整理成可以直接送出的文字。

provider：
- ollama：本機 Ollama 原生 API，可控制模型留在記憶體多久（keep_alive），完全離線
- openai：任何 OpenAI 相容服務（Groq、OpenAI、OpenRouter…），需要連網與 API Key

LLM 的輸出不一定可靠，所以有防呆：輸出空白、比原文長太多（多半是在「回答」內容）、
或語言跟原文不同（多半是被內容裡的指令帶走）時，丟出 RefineError，由呼叫端改貼原文。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import paths
from .config import ConfigError, RefineConfig

log = logging.getLogger(__name__)

BUNDLED_PROMPTS_DIR = paths.DATA_DIR / "prompts"
USER_PROMPTS_DIR = paths.CONFIG_DIR / "prompts"
MODE_FILE = paths.STATE_DIR / "mode"

_CJK_RE = re.compile(r"[㐀-䶿一-鿿]")


class RefineError(RuntimeError):
    pass


@dataclass
class Prompt:
    name: str
    description: str
    system: str
    examples: list[tuple[str, str]]
    check_language: bool = True

    def messages(self, text: str) -> list[dict[str, str]]:
        # 逐字稿包在標籤裡，讓模型分清楚「要整理的文字」和「給它的指示」
        msgs = [{"role": "system", "content": self.system}]
        for given, wanted in self.examples:
            msgs.append({"role": "user", "content": f"<逐字稿>{given}</逐字稿>"})
            msgs.append({"role": "assistant", "content": wanted})
        msgs.append({"role": "user", "content": f"<逐字稿>{text}</逐字稿>"})
        return msgs


def available_prompts() -> dict[str, Path]:
    """模式名稱 → 檔案；使用者目錄的同名檔案優先於內建的。"""
    found: dict[str, Path] = {}
    for directory in (BUNDLED_PROMPTS_DIR, USER_PROMPTS_DIR):
        if directory.is_dir():
            for f in sorted(directory.glob("*.yaml")):
                found[f.stem] = f
    return found


def load_prompt(name: str) -> Prompt:
    prompts = available_prompts()
    if name not in prompts:
        raise ConfigError(f"找不到小紙條「{name}」（可用：{'、'.join(prompts) or '無'}）")
    path = prompts[name]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        examples = [(str(e["input"]), str(e["output"])) for e in data.get("examples") or []]
        return Prompt(
            name=name,
            description=str(data.get("description", "")),
            system=str(data["system"]).strip(),
            examples=examples,
            check_language=bool(data.get("check_language", True)),
        )
    except (yaml.YAMLError, KeyError, TypeError) as e:
        raise ConfigError(f"小紙條 {path} 格式錯誤：{e}") from e


def current_mode(default: str) -> str:
    try:
        name = MODE_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default
    return name or default


def set_mode(name: str) -> None:
    load_prompt(name)  # 先確認存在且格式正確
    MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODE_FILE.write_text(name + "\n", encoding="utf-8")


def check_output(source: str, output: str, check_language: bool) -> None:
    if not output:
        raise RefineError("LLM 沒有輸出")
    if len(output) > 2 * len(source) + 40:
        raise RefineError("輸出比原文長太多，可能是在回答內容而不是整理")
    if check_language:
        src_cjk = len(_CJK_RE.findall(source))
        if src_cjk >= max(4, len(source) * 0.3) and len(_CJK_RE.findall(output)) < src_cjk * 0.5:
            raise RefineError("輸出的語言跟原文不同")


class Refiner:
    def __init__(self, cfg: RefineConfig):
        if cfg.provider not in ("ollama", "openai"):
            raise ConfigError(f"refine.provider 只能是 ollama 或 openai，不是 {cfg.provider}")
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")

    def _post(self, url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            raise RefineError(f"LLM 服務回應錯誤 {e.code}：{detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RefineError(f"連不到 LLM 服務 {self.base_url}：{e}") from e

    def preload(self) -> None:
        """在背景先把模型載入記憶體（只對 ollama 有效），讓使用者說話的同時完成載入。"""
        if self.cfg.provider != "ollama":
            return

        def run() -> None:
            try:
                self._post(
                    f"{self.base_url}/api/generate",
                    {"model": self.cfg.model, "keep_alive": self.cfg.keep_alive},
                )
            except RefineError as e:
                log.debug("預先載入模型失敗：%s", e)

        threading.Thread(target=run, name="refine-preload", daemon=True).start()

    def refine(self, text: str, mode: str | None = None) -> str:
        prompt = load_prompt(mode or current_mode(self.cfg.mode))
        messages = prompt.messages(text)
        if self.cfg.provider == "ollama":
            result = self._post(
                f"{self.base_url}/api/chat",
                {
                    "model": self.cfg.model,
                    "messages": messages,
                    "stream": False,
                    "keep_alive": self.cfg.keep_alive,
                    "options": {"temperature": 0},
                },
            )
            output = result.get("message", {}).get("content", "")
        else:
            result = self._post(
                f"{self.base_url}/chat/completions",
                {"model": self.cfg.model, "messages": messages, "temperature": 0},
                {"Authorization": f"Bearer {self._api_key()}"},
            )
            try:
                output = result["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as e:
                raise RefineError(f"LLM 回應格式不對：{str(result)[:200]}") from e
        # 去掉行尾空白（模型常為了 Markdown 換行在行尾加兩個空白）
        output = "\n".join(line.rstrip() for line in output.strip().splitlines())
        check_output(text, output, prompt.check_language)
        return output

    def _api_key(self) -> str:
        if not self.cfg.api_key_file:
            raise RefineError("provider 為 openai 時需要設定 refine.api_key_file")
        try:
            return Path(self.cfg.api_key_file).expanduser().read_text(encoding="utf-8").strip()
        except OSError as e:
            raise RefineError(f"讀不到 API Key 檔案：{e}") from e
