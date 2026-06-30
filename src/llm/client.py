"""Studio LLM 客户端 / Studio LLM Client.

最小化封装，复用 langchain-openai 技术栈，与 AIGameWorld 后端对齐。
支持 OpenAI 兼容的任何 provider（DeepSeek / 通义千问 / GLM）。

配置优先级: 参数 > 环境变量 > 默认值
  STUDIO_LLM_MODEL     — 模型名 (default: deepseek-chat)
  STUDIO_LLM_BASE_URL  — API 地址 (default: https://api.deepseek.com/v1)
  DEEPSEEK_API_KEY     — API 密钥
"""

import asyncio
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr


class StudioLLM:
    """最小 LLM 客户端，接入 OpenAI 兼容 API."""

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

    async def generate(self, system: str, user: str, retries: int = 3) -> str:
        """调用 LLM 生成文本，含自动重试."""
        for attempt in range(retries):
            try:
                # 构造消息并调用 / Build messages and call
                result = await self._model._agenerate([SystemMessage(content=system), HumanMessage(content=user)])
                content = str(result.generations[0].message.content)
                # 清理 markdown code block 包裹 / Strip markdown code blocks
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3].strip()
                return content
            except Exception:
                if attempt == retries - 1:
                    raise
                # 重试前等待 / Wait before retry
                await asyncio.sleep(1)
        return ""  # unreachable
