"""Studio LLM 客户端 / Studio LLM Client.

与 AIGameWorld 后端技术栈对齐：langchain-openai + Jinja2 模板。
支持 OpenAI 兼容的任何 provider（DeepSeek / 通义千问 / GLM）。

配置优先级: 参数 > 环境变量 > 默认值
  STUDIO_LLM_MODEL     — 模型名 (default: deepseek-chat)
  STUDIO_LLM_BASE_URL  — API 地址 (default: https://api.deepseek.com/v1)
  DEEPSEEK_API_KEY     — API 密钥
"""

import asyncio
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

# Jinja2 环境 / Jinja2 environment
_TEMPLATE_DIR = Path(__file__).parent / "prompts"
_JINJA = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))

# 系统人格缓存 / System persona cache
_SYSTEM_PROMPT: str | None = None


def _get_system_prompt() -> str:
    """获取系统人格 prompt（懒加载 + 缓存）."""
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        _SYSTEM_PROMPT = _JINJA.get_template("_system.jinja").render()
    return _SYSTEM_PROMPT


class StudioLLM:
    """LLM 客户端，支持两种调用模式 / LLM client with two call modes:

    1. 模板模式: generate(template_name, **kwargs) — 渲染 Jinja2 模板 → LLM
    2. 原始模式: generate_raw(user_prompt) — 直接发 prompt → LLM（用于 LangGraph 管线）
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.8,
    ):
        # 构建 ChatOpenAI 实例 / Build ChatOpenAI instance
        key = api_key or os.getenv("DEEPSEEK_API_KEY", "sk-dummy")
        self._model = ChatOpenAI(
            model=model or os.getenv("STUDIO_LLM_MODEL", "deepseek-chat"),
            base_url=base_url or os.getenv("STUDIO_LLM_BASE_URL", "https://api.deepseek.com/v1"),
            api_key=SecretStr(key),
            temperature=temperature,
        )

    async def generate(self, template_name: str, **kwargs) -> str:
        """渲染 Jinja2 模板并调用 LLM / Render Jinja2 template → LLM.

        Args:
            template_name: 模板文件名 (不含 .jinja 后缀)，如 "lore", "pc", "scene"
            **kwargs: 模板变量，如 world_name, count, theme
        """
        # 加载并渲染 Jinja2 模板 / Load and render Jinja2 template
        template = _JINJA.get_template(f"{template_name}.jinja")
        user_prompt = template.render(**kwargs)
        # 调用 LLM 生成 / Invoke LLM
        return await self._call(user_prompt)

    async def generate_raw(self, user_prompt: str, system_prompt: str | None = None) -> str:
        """直接发送 prompt 调用 LLM / Send raw prompt directly to LLM.

        Args:
            user_prompt: 用户 prompt 文本
            system_prompt: 系统 prompt，默认使用内置世界创作助手人格
        """
        return await self._call(user_prompt, system_prompt=system_prompt)

    async def _call(self, user_prompt: str, retries: int = 3, system_prompt: str | None = None) -> str:
        """调用 LLM 生成文本，含自动重试 / Call LLM with auto retry."""
        # 系统 prompt / System prompt (传入 > 默认)
        sys_msg = system_prompt if system_prompt is not None else _get_system_prompt()
        for attempt in range(retries):
            try:
                # 构造消息并调用 / Build messages and invoke
                result = await self._model._agenerate(
                    [SystemMessage(content=sys_msg), HumanMessage(content=user_prompt)]
                )
                content = str(result.generations[0].message.content)
                # 清理 markdown code block 包裹 / Strip markdown code blocks
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3].strip()
                return content
            except Exception:
                # 最后一次重试则抛出 / Raise on final retry
                if attempt == retries - 1:
                    raise
                # 重试前等待 / Wait before retry
                await asyncio.sleep(1)
        return ""  # unreachable
