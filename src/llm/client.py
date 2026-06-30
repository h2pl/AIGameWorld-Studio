# LLM 客户端 / LLM Client — 对齐 AIGameWorld/backend/src/llm/llm_client.py
# ChatOpenAI + RequestsChatModel + .env + config.yaml

import asyncio
import logging
import os
import time
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

# 项目根 / Project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 自动加载 .env + config.yaml / Auto-load .env + config.yaml
load_dotenv(_PROJECT_ROOT / ".env")
_config_path = _PROJECT_ROOT / "config.yaml"
_CONFIG: dict = {}
if _config_path.exists():
    _CONFIG = yaml.safe_load(_config_path.read_text(encoding="utf-8")) or {}

logger = logging.getLogger(__name__)

# Jinja2 环境 / Jinja2 environment
_TEMPLATE_DIR = Path(__file__).parent / "prompts"
_JINJA = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))

# 系统人格缓存 / System persona cache
_SYSTEM_PROMPT: str | None = None


# ═══════════════════════════════════════════════════════════════
# RequestsChatModel — 对齐 AIGameWorld 同款
# ═══════════════════════════════════════════════════════════════


class RequestsChatModel(BaseChatModel):
    """用 requests 替代 httpx 的 ChatModel——解决 Zen Proxy 502 问题."""

    model: str = ""
    temperature: float = 0.7
    base_url: str = ""
    timeout: int = 30

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("Use async version")

    async def _agenerate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        _role_map = {"human": "user", "ai": "assistant"}
        payload = {
            "model": self.model,
            "messages": [{"role": _role_map.get(m.type, m.type), "content": m.content} for m in messages],
            "temperature": self.temperature,
        }
        url = f"{self.base_url}/chat/completions"
        t_start = time.monotonic()
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(None, lambda: requests.post(url, json=payload, timeout=self.timeout))
            resp.raise_for_status()
        except requests.Timeout as e:
            raise RuntimeError(f"HTTP 请求超时：{url} 在 {self.timeout}s 内无响应") from e
        except requests.ConnectionError as e:
            raise RuntimeError(f"连接失败：{url} — {e}") from e
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            body = e.response.text[:500] if e.response is not None else "(无响应体)"
            raise RuntimeError(f"HTTP {status}：{body}") from e
        data = resp.json()
        content: str = data["choices"][0]["message"]["content"]
        elapsed = time.monotonic() - t_start
        logger.debug("LLM 响应完成, model=%s elapsed=%.1fs content_len=%d", self.model, elapsed, len(content))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    @property
    def _llm_type(self) -> str:
        return "requests-chat"


# ═══════════════════════════════════════════════════════════════
# Model 构建 + 调用
# ═══════════════════════════════════════════════════════════════

_MODEL: BaseChatModel | None = None


def _get_system_prompt() -> str:
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = _JINJA.get_template("_system.jinja").render()
    return _SYSTEM_PROMPT


def _resolve_provider() -> dict:
    """从 config.yaml 解析当前 provider / Resolve provider from config.yaml."""
    providers = _CONFIG.get("llm_providers", {})
    if not providers:
        raise RuntimeError("config.yaml 缺少 llm_providers / config.yaml missing llm_providers")

    # 环境变量覆盖 > config.yaml > 默认 deepseek
    name = os.getenv("STUDIO_LLM_PROVIDER") or _CONFIG.get("llm_provider", "deepseek")
    if name not in providers:
        available = ", ".join(providers.keys())
        raise ValueError(f"Unknown provider '{name}'. Available: {available}")
    return providers[name]


def _llm_config() -> dict:
    """获取 LLM 调用参数 / Get LLM call parameters."""
    return _CONFIG.get("llm", {}).get("generate", {})


def get_model() -> BaseChatModel:
    """获取/创建 LLM 模型单例 / Get or create LLM model singleton."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    provider = _resolve_provider()
    llm_cfg = _llm_config()
    temperature = llm_cfg.get("temperature", 0.8)
    timeout = llm_cfg.get("timeout", 60)

    key_env = provider.get("api_key_env", "")
    api_key = os.getenv(key_env, "sk-dummy") if key_env else "no-key"

    if provider.get("client_backend") == "requests":
        _MODEL = RequestsChatModel(
            model=provider["model"],
            temperature=temperature,
            base_url=provider["base_url"].rstrip("/"),
            timeout=timeout,
        )
    else:
        _MODEL = ChatOpenAI(
            model=provider["model"],
            temperature=temperature,
            base_url=provider["base_url"],
            api_key=SecretStr(api_key),
            timeout=timeout,
            max_retries=1,
        )
    return _MODEL


async def llm_generate(template_name: str, **kwargs) -> str:
    """Jinja2 模板 → LLM 生成 / Render Jinja2 template → LLM call."""
    template = _JINJA.get_template(f"{template_name}.jinja")
    user_prompt = template.render(**kwargs)
    return await _llm_call(user_prompt)


async def _llm_call(user_prompt: str, retries: int = 3) -> str:
    """调用 LLM + 自动重试 / Call LLM with retry."""
    model = get_model()
    sys_msg = _get_system_prompt()
    for attempt in range(retries):
        try:
            result = await model._agenerate([SystemMessage(content=sys_msg), HumanMessage(content=user_prompt)])
            content = str(result.generations[0].message.content).strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3].strip()
            return content
        except Exception:
            if attempt == retries - 1:
                raise
            await asyncio.sleep(1)
    return ""
