"""Remove interpret_tilemap, generate_actors, generate_scene_objects from all backend files."""
import re

ROOT = "E:/Projects/AIGameWorld/backend"

# ── 1. scene_service.py ──
path = f"{ROOT}/src/services/scene_service.py"
with open(path, encoding="utf-8") as f:
    c = f.read()

# Remove the 3 functions at structural boundaries
# interpret_tilemap: from @trace_node("scene.interpret_tilemap") to @trace_node("scene.generate_actors")
si = c.index('@trace_node("scene.interpret_tilemap")')
ei = c.index('@trace_node("scene.generate_actors")')
c = c[:si] + c[ei:]

# generate_actors: from @trace_node("scene.generate_actors") to @trace_node("scene.build_actors")
si = c.index('@trace_node("scene.generate_actors")')
ei = c.index('@trace_node("scene.build_actors")')
c = c[:si] + c[ei:]

# generate_scene_objects: from @trace_node("scene.generate_scene_objects") to @trace_node("scene.build_scene_objects")
si = c.index('@trace_node("scene.generate_scene_objects")')
ei = c.index('@trace_node("scene.build_scene_objects")')
c = c[:si] + c[ei:]

# remove helper functions (_build_spawn_ctx, _build_scene_object_ctx)
si = c.index("def _build_spawn_ctx")
c = c[:si]

# Fix build_scene_state calls
c = c.replace("result.update(await interpret_tilemap({**state, **result}, config))\n", "")
c = c.replace("result.update(await generate_actors({**state, **result}, config))\n", "")
c = c.replace("result.update(await generate_scene_objects({**state, **result}, config))\n", "")
c = c.replace("7 个场景方法", "4 个场景方法")

# Clean imports & helpers
for bad in [
    "import json\n",
    "from pathlib import Path\n",
    "from jinja2 import Environment, FileSystemLoader\n",
    "from langchain_core.messages import HumanMessage, SystemMessage\n",
    "from langchain_core.runnables.config import RunnableConfig\n",
    "from ..schemas.llm_output import (\n    ActorGenerationSchema,\n    SceneObjectGenerationSchema,\n    TilemapInterpretationSchema,\n)\n",
    "from ..utils.helpers import build_occupied_set, find_vacant_adjacent, get_llm, get_repo\n",
]:
    c = c.replace(bad, "")

c = c.replace(
    "from ..utils.helpers import build_occupied_set, find_vacant_adjacent, get_repo\n\n",
    "from ..utils.helpers import build_occupied_set, find_vacant_adjacent, get_repo\n",
)
c = c.replace("_PROMPTS_ROOT = Path(__file__).parent.parent / \"prompts\"\n_PROMPTS = Environment(loader=FileSystemLoader(str(_PROMPTS_ROOT)))\n\n\n", "")
c = re.sub(r"\n{4,}", "\n\n\n", c)

with open(path, "w", encoding="utf-8") as f:
    f.write(c)

# ── 2. scene_subgraph.py ──
path = f"{ROOT}/src/graph/subgraphs/scene_subgraph.py"
with open(path, encoding="utf-8") as f:
    c = f.read()

c = c.replace(
    '    graph.add_node("scene_service.interpret_tilemap", scene_service.interpret_tilemap)\n',
    '',
)
c = c.replace(
    '    graph.add_node("scene_service.generate_actors", scene_service.generate_actors)\n',
    '',
)
c = c.replace(
    '    graph.add_node("scene_service.generate_scene_objects", scene_service.generate_scene_objects)\n',
    '',
)
c = c.replace(
    'graph.add_edge("scene_service.build_scene_info", "scene_service.interpret_tilemap")\n'
    '    graph.add_edge("scene_service.interpret_tilemap", "scene_service.generate_actors")\n'
    '    graph.add_edge("scene_service.generate_actors", "scene_service.build_actors")\n'
    '    graph.add_edge("scene_service.build_actors", "scene_service.build_pcs")\n'
    '    graph.add_edge("scene_service.build_pcs", "scene_service.generate_scene_objects")\n'
    '    graph.add_edge("scene_service.generate_scene_objects", "scene_service.build_scene_objects")\n',
    '    graph.add_edge("scene_service.build_scene_info", "scene_service.build_actors")\n'
    '    graph.add_edge("scene_service.build_actors", "scene_service.build_pcs")\n'
    '    graph.add_edge("scene_service.build_pcs", "scene_service.build_scene_objects")\n',
)
c = c.replace("7 个节点", "4 个节点")
c = c.replace("Seven", "Four")
c = c.replace("7 个 service", "4 个 service")

with open(path, "w", encoding="utf-8") as f:
    f.write(c)

# ── 3. config.py ──
path = f"{ROOT}/src/config.py"
with open(path, encoding="utf-8") as f:
    c = f.read()
c = c.replace("    interpret_tilemap: LLMModelConfig  # tilemap 语义解读 / Tilemap semantic interpretation\n", "")
c = c.replace("    spawn_actors: LLMModelConfig  # 动态生成 Actor / Dynamic actor spawning\n", "")
c = c.replace("    spawn_objects: LLMModelConfig  # 动态生成场景物体 / Dynamic scene object spawning\n", "")
with open(path, "w", encoding="utf-8") as f:
    f.write(c)

# ── 4. llm_client.py ──
path = f"{ROOT}/src/llm/llm_client.py"
with open(path, encoding="utf-8") as f:
    c = f.read()
c = c.replace('            ("interpret_tilemap", llm_config.interpret_tilemap),\n', "")
c = c.replace('            ("spawn_actors", llm_config.spawn_actors),\n', "")
c = c.replace('            ("spawn_objects", llm_config.spawn_objects),\n', "")
with open(path, "w", encoding="utf-8") as f:
    f.write(c)

# ── 5. mock_data.py ──
path = f"{ROOT}/src/llm/mock_data.py"
with open(path, encoding="utf-8") as f:
    c = f.read()
# Remove interpret_tilemap block
si = c.index('    "interpret_tilemap": [')
ei = c.index("],", si) + 2
c = c[:si] + c[ei:]
# Remove spawn_actors block
si = c.index('    "spawn_actors": [')
ei = c.index("],", si) + 2
c = c[:si] + c[ei:]
# Remove spawn_objects block
si = c.index('    "spawn_objects": [')
ei = c.index("],", si) + 2
c = c[:si] + c[ei:]
with open(path, "w", encoding="utf-8") as f:
    f.write(c)

print("done")
