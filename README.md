# AIGameWorld Studio

> 世界创作工坊——LLM 驱动的游戏世界生成器，YAML 定义世界，AIGameWorld 引擎运行。

## 快速开始

```bash
git clone git@github.com:h2pl/AIGameWorld-Studio.git
cd AIGameWorld-Studio
uv sync
cp .env.example .env          # 填入 DEEPSEEK_API_KEY
uv run aw-studio generate -i  # 交互式生成
uv run aw-studio serve        # 浏览器查看 YAML pack → http://127.0.0.1:8888
```

## CLI 命令

### generate — 生成世界
```bash
aw-studio generate -i                              # 交互式
aw-studio generate --name 魔兽世界 --pack-id warcraft_world \
  --pc 2 --actor 2 --scene 1 --item 3 --lore 1     # 命令行全参
aw-studio generate --name test --skeleton-only      # 仅骨架，不调 LLM
aw-studio generate --name test --assets             # 生成素材（精灵/瓦片/布局）
```

### validate — 校验世界
```bash
aw-studio validate world-packs/custom/warcraft_world
```

### serve — YAML 浏览器
```bash
aw-studio serve                          # 默认 world-packs/custom/
aw-studio serve world-packs/custom --port 9999
```
打开浏览器浏览生成的 YAML pack，PC/NPC/Item/Scene/Object/Story 多 Tab 切换。

> DB 运行时数据查看器已集成到主项目：`http://localhost:8001/view`

## 生成管线

```
CLI → Generator(主图) → YAML + Assets → Validator
          ├── world_pack subgraph: skeleton → LLM fill → validate → retry
          └── assets subgraph: sprites → tileset → layout
```

- **world_pack**：模板骨架 → LLM 填充文案 → Validator 校验 → 写入 YAML
- **assets**：Recolor 换色(Pillow) + AI 生成(Replicate/DALL-E) + 瓦片集 + 场景布局

## LLM Provider

| Provider | 切换方式 | Key |
|----------|---------|-----|
| DeepSeek（默认） | 无需配置 | `DEEPSEEK_API_KEY` |
| GLM（智谱） | `STUDIO_LLM_PROVIDER=glm` | `GLM_API_KEY` |
| Zen Proxy（本地） | `STUDIO_LLM_PROVIDER=zen-proxy` | 无需 |

## 项目结构

```
src/
├── cli.py                  # CLI 入口
├── serve.py                # YAML 查看器
├── graph/                  # 编排层（主图 + subgraphs）
│   ├── graph.py            # 主图：world_pack → assets → END
│   └── subgraphs/
│       ├── world_pack.py   # skeleton → LLM fill → validate → retry
│       └── assets.py       # sprites → tileset → layout
├── pipeline/               # 业务逻辑
│   ├── world_pack/         # params + skeleton + nodes
│   ├── assets/             # character(Recolor) + tiles + layout
│   └── ai_assets/          # character(Replicate/DALL-E)
├── llm/                    # client.py + prompts/
└── validator/              # validate.py
templates/
├── serve/                  # 查看器 Jinja2 模板
└── *.yaml                  # 8 个 YAML 字段模板
world-packs/                # 生成的世界输出
```

## 对接主项目

```bash
# AIGameWorld 侧导入
aw import world-packs/custom/warcraft_world  # YAML → SQLite + ChromaDB
```

## 测试

```bash
uv run pytest tests/         # 78 tests
uv run ruff check .          # Lint
uv run pyright src/ tests/   # Type check
```

## 技术栈

Python 3.14 · LangGraph · ChatOpenAI · Jinja2 · ruamel.yaml · Pydantic · Pillow · pytest · ruff · pyright
