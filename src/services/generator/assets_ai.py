# AI 素材管线 / AI Asset Pipeline — 角色精灵 AI 生成
# Replicate SDXL + OpenAI DALL-E

import json
import os
import urllib.request
from pathlib import Path

from src.domain.params import GenerateParams


def generate_ai_sprites(
    characters: list[dict],
    sprites_dir: Path,
    params: GenerateParams | None = None,
) -> dict[str, Path]:
    """使用 AI API 生成精灵图 / Generate sprites using AI image API."""
    replicate_token = os.getenv("REPLICATE_API_TOKEN")
    openai_key = os.getenv("OPENAI_API_KEY")

    if not replicate_token and not openai_key:
        raise RuntimeError("AI 生成需要 REPLICATE_API_TOKEN 或 OPENAI_API_KEY 环境变量")

    result: dict[str, Path] = {}
    world_name = params.world_name if params else "fantasy"

    for char in characters:
        char_id = char.get("id", f"unknown_{len(result)}")
        race = char.get("race", "human")
        role = char.get("role", "villager")

        prompt = (
            f"32x32 pixel art character sprite, {race} {role}, "
            f"side view, top-down RPG style, {world_name} theme, "
            f"solid color background, simple clean lines"
        )
        path = sprites_dir / f"{char_id}.png"

        if replicate_token:
            _replicate(prompt, path)
        else:
            _openai(prompt, path)
        result[char_id] = path

    return result


# ═══════════════════════════════════════════════════════════════
# Replicate
# ═══════════════════════════════════════════════════════════════

_REPLICATE_URL = "https://api.replicate.com/v1/predictions"
_REPLICATE_MODEL = "fofr/sdxl-pixel-art"


def _replicate(prompt: str, output_path: Path, poll_interval: int = 2, timeout: int = 120) -> None:
    """Replicate SDXL pixel art 生成 / Replicate SDXL pixel art generation."""
    token = os.getenv("REPLICATE_API_TOKEN")

    # 1. 创建预测 / Create prediction
    body = json.dumps(
        {"version": _REPLICATE_MODEL, "input": {"prompt": prompt, "width": 32, "height": 32, "num_outputs": 1}}
    ).encode()
    req = urllib.request.Request(
        _REPLICATE_URL, data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    # 2. 轮询直到完成 / Poll until complete
    poll_url = data.get("urls", {}).get("get")
    if not poll_url:
        raise RuntimeError("Replicate 返回异常: 缺少 poll URL")

    import time

    for _ in range(timeout // poll_interval):
        time.sleep(poll_interval)
        req = urllib.request.Request(poll_url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req) as resp:
            status = json.loads(resp.read())
        if status.get("status") == "succeeded":
            image_url = status.get("output", [None])[0]
            if image_url:
                urllib.request.urlretrieve(image_url, str(output_path))
                return
        elif status.get("status") in ("failed", "canceled"):
            raise RuntimeError(f"Replicate 生成失败: {status.get('error', 'unknown')}")

    raise TimeoutError(f"Replicate 生成超时 ({timeout}s)")


# ═══════════════════════════════════════════════════════════════
# OpenAI DALL-E
# ═══════════════════════════════════════════════════════════════


def _openai(prompt: str, output_path: Path) -> None:
    """OpenAI DALL-E 生成 / OpenAI DALL-E generation."""
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.images.generate(model="dall-e-3", prompt=prompt, size="256x256", n=1)
    url = response.data[0].url if response.data else None
    if url:
        urllib.request.urlretrieve(url, str(output_path))
