# LLM 填充节点 / LLM Fill Nodes — 纯业务逻辑，被 graph 调用

from pathlib import Path

import yaml
from langchain_core.messages import HumanMessage, SystemMessage

from src.llm.client import get_model, llm_generate

# 骨架 / Skeleton
from src.llm.utils import write_yaml_entities
from src.domain.params import GenerateParams
from src.services.generator.world_pack import generate_skeleton
from src.services.validator import validate_template as validate_world_pack


# Meta / Meta description
def node_skeleton(output_dir: Path, params: GenerateParams) -> Path:
    """生成骨架，返回 output 目录 / Generate skeleton, return output dir."""
    out = Path(params.output_dir) / params.pack_id
    out.mkdir(parents=True, exist_ok=True)
    generate_skeleton(out, params)
    return out


async def node_generate_meta(output_dir: Path, _theme: str) -> None:
    """LLM 填充 meta 描述 / Fill meta description."""
    meta_path = output_dir / "meta.yaml"
    # Story setup / Story framework
    data = yaml.safe_load(meta_path.read_text("utf-8")) or {}
    prompt = (
        f"为「{data['name']}」世界的 meta.yaml 填写 description 字段。\n"
        f"主题：{_theme}\n要求：一句话概括，有吸引力。只输出纯文本。"
    )
    model = get_model()
    sys_msg = "你是世界创作助手，输出纯文本。"
    result = await model._agenerate([SystemMessage(content=sys_msg), HumanMessage(content=prompt)])
    desc = str(result.generations[0].message.content).strip().strip('"').strip("'")
    data["description"] = desc
    meta_path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


async def node_generate_story_setup(output_dir: Path, world_name: str, _theme: str) -> None:
    """LLM 填充故事框架 / Fill story setup."""
    from src.llm.client import get_model

    sp_path = output_dir / "story_setup.yaml"
    data = yaml.safe_load(sp_path.read_text("utf-8")) or {}
    prompt = (
        f"为「{world_name}」世界编写初始剧情框架。\n主题：{_theme}\n"
        f'请输出严格的 YAML 格式：\narcs:\n  - type: main\n    title: "主线标题"\n'
        f"    stage: hook\n    main_cast: []\n    branching_points: []"
    )
    model = get_model()
    sys_msg = "你是世界创作助手，输出严格的 YAML 格式。"
    result = await model._agenerate([SystemMessage(content=sys_msg), HumanMessage(content=prompt)])
    content = str(result.generations[0].message.content).strip()
    # 通用 LLM 填充 / Generic fill
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3].strip()
    new_data = yaml.safe_load(content)
    if new_data:
        data.update(new_data)
    sp_path.write_text(yaml.dump(data, allow_unicode=True), encoding="utf-8")


async def node_llm_fill(
    output_dir: Path, template_name: str, subdir: str | None, file_prefix: str, count: int, world_name: str, _theme: str
) -> None:
    """通用 LLM 填充 / Generic LLM fill."""
    if count == 0:
        return
    # 校验
    target_dir = output_dir / subdir if subdir else output_dir
    result = await llm_generate(template_name, world_name=world_name, count=count, theme=_theme)
    n = write_yaml_entities(target_dir, result, file_prefix)
    for i in range(count + 1, 20):
        extra = target_dir / f"{file_prefix}_{i}.yaml"
        if extra.exists():
            extra.unlink()
    print(f"  [LLM] {file_prefix}: wrote {min(n, count)}/{count} files ({len(result)} chars)")


def node_validate(output_dir: Path):
    """校验 / Validate."""
    return validate_world_pack(output_dir)


# ── 主入口（替代 LangGraph） / Main entry (replaces LangGraph) ──


async def generate_world_pack(params: GenerateParams) -> Path:
    """生成世界包 / Generate world pack.

    纯函数编排，替代 LangGraph：
    skeleton → meta → story → lore → pcs → npcs → items → scenes → objects → validate(→retry)
    """
    out = node_skeleton(Path(params.output_dir), params)
    theme = params.theme or params.world_name

    # 顺序执行 LLM 填充 / Sequential LLM fill
    await node_generate_meta(out, theme)
    await node_generate_story_setup(out, params.world_name, theme)

    # 带重试的填充 / Fill with retry
    for attempt in range(params.max_retries):
        await node_llm_fill(out, "lore", "lore", "lore", params.num_lore, params.world_name, theme)
        await node_llm_fill(out, "pc", "player_characters", "player_character", params.num_pcs, params.world_name, theme)
        await node_llm_fill(out, "npc", "actors", "actor", params.num_actors, params.world_name, theme)
        await node_llm_fill(out, "item", "items", "item", params.num_items, params.world_name, theme)
        await node_llm_fill(out, "scene", "scenes", "scene", params.num_scenes, params.world_name, theme)
        await node_llm_fill(out, "scene_object", "scene_objects", "scene_object", params.num_scene_objects, params.world_name, theme)

        result = node_validate(out)
        if result.is_valid:
            break

    # 素材生成 / Assets generation
    if params.assets_method != "skip":
        await _generate_assets(out, params)

    return out


async def _generate_assets(output_dir: Path, params: GenerateParams):
    """生成素材 / Generate assets."""
    import yaml as _yaml

    def _read_yaml_dir(d: Path) -> list[dict]:
        if not d.exists():
            return []
        return [_yaml.safe_load(f.read_text("utf-8")) for f in d.glob("*.yaml") if _yaml.safe_load(f.read_text("utf-8"))]

    chars = _read_yaml_dir(output_dir / "player_characters") + _read_yaml_dir(output_dir / "actors")

    if params.assets_method == "ai":
        from src.services.generator.assets_ai import generate_ai_sprites

        sprites_dir = output_dir / "sprites"
        sprites_dir.mkdir(exist_ok=True)
        generate_ai_sprites(chars, sprites_dir, params)
    else:
        from src.services.generator.assets_recolor import generate_character_sprites

        if chars:
            generate_character_sprites(chars, output_dir, method="recolor")
        scenes = _read_yaml_dir(output_dir / "scenes")
        if scenes:
            from src.services.generator.assets_tiles import generate_tileset
            from src.services.generator.assets_layout import generate_layout

            generate_tileset(scenes, output_dir)
            generate_layout(scenes, output_dir)
