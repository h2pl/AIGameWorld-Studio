# AIGameWorld Studio

> 世界创作工坊——LLM 驱动的游戏世界生成器，YAML 定义世界，AIGameWorld 引擎运行。
> World Creation Workshop — LLM-powered game world generator.

## 快速开始 / Quick Start

```bash
# 1. 克隆 + 安装依赖
git clone git@github.com:h2pl/AIGameWorld-Studio.git
cd AIGameWorld-Studio
uv sync

# 2. 配置 API Key（复制 .env.example → .env，填入 DEEPSEEK_API_KEY）
cp .env.example .env

# 3. 生成世界
uv run aw-studio generate -i                    # 交互式输入（推荐）
uv run aw-studio generate --name 魔兽世界 \
  --pack-id warcraft_world --theme 魔兽世界主题 \
  --pc 2 --actor 2 --scene 1 --item 3 --lore 1  # 命令行全参数

# 4. 校验
uv run aw-studio validate world-packs/custom/warcraft_world
```

## 操作指南 / Usage

### generate — 生成世界

```bash
# 交互式（逐步输入所有参数，推荐新手）
aw-studio generate -i

# 命令行一行搞定
aw-studio generate \
  --name 魔兽世界 \            # 显示名（中文 OK）
  --pack-id warcraft_world \   # 目录名 + YAML id（ASCII，不填自动生成）
  --theme 魔兽世界主题 \       # 世界主题
  --pc 3 \                     # 主角数量
  --actor 4 \                  # 配角数量
  --scene 3 \                  # 场景数量
  --item 6 \                   # 物品数量
  --lore 3 \                   # 世界观设定条数
  --scene-object 2 \           # 场景对象数量
  --max-retries 3 \            # LLM 重试次数
  -o world-packs/custom        # 输出目录

# 仅生成骨架（不调 LLM，测试用）
aw-studio generate --name test --skeleton-only
```

### validate — 校验世界

```bash
aw-studio validate world-packs/custom/warcraft_world
# 输出: 13 passed, 0 failed → 校验通过
```

### 生成流程

```
skeleton(模板生成结构) → [LLM 填充文案] → validate(校验)
     └── 必过校验                └── DeepSeek/GLM/Zen Proxy
```

LLM 失败时骨架仍保留，可手动编辑或用 `--skeleton-only` 跳过 LLM。

## LLM Provider 切换

通过 `config.yaml` + `.env` 管理，三种 provider 可选：

| Provider | 切换方式 | 需要 Key |
|----------|---------|---------|
| DeepSeek（默认） | 无需配置 | `DEEPSEEK_API_KEY` |
| GLM（智谱） | `STUDIO_LLM_PROVIDER=glm` | `GLM_API_KEY` |
| Zen Proxy（本地） | `STUDIO_LLM_PROVIDER=zen-proxy` | 无需 key |

```bash
# .env
DEEPSEEK_API_KEY=sk-your-key-here
# GLM_API_KEY=your-glm-key-here

# 切换 provider
$env:STUDIO_LLM_PROVIDER="glm"
```

## 目录结构 / Project Structure

```
src/
├── cli.py                  # CLI 入口（argparse）
├── generator/
│   ├── params.py           # GenerateParams 数据类
│   ├── skeleton.py         # 模板 → 合法 YAML 骨架
│   └── graph.py            # LangGraph 生成管线
├── llm/
│   ├── client.py           # ChatOpenAI + RequestsChatModel
│   ├── utils.py            # YAML 解析写入
│   └── prompts/            # Jinja2 LLM prompt 模板
└── validator/
    └── validate.py         # YAML 校验器

templates/                  # 8 个 YAML 字段模板
world-packs/                # 生成的世界输出
```

## 测试 / Tests

```bash
uv run pytest tests/         # 57 tests（单元+集成）
uv run ruff check .          # Lint
uv run pyright src/ tests/   # Type check
```

## 对接 AIGameWorld

生成的 world-pack 通过 AIGameWorld backend 导入：

```bash
# AIGameWorld 侧
aw import world-packs/custom/warcraft_world  # YAML → SQLite + ChromaDB
```

## 技术栈

Python 3.14 · LangGraph · ChatOpenAI · Jinja2 · ruamel.yaml · Pydantic · pytest · ruff · pyright
