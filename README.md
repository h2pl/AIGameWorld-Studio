# AIGameWorld Studio

> 世界创作工坊——既是世界生成器，也是设定集，也是知识库。YAML 定义世界，引擎读取运行。
> World Creation Workshop——generator, setting library, knowledge base.

## 快速开始 / Quick Start

```bash
git clone <repo> && cd AIGameWorld-Studio
uv sync
python -m src.cli generate --preset forgotten_realm   # 生成模板
python -m src.cli validate output/forgotten_realm      # 校验模板
python -m src.cli load output/forgotten_realm           # 加载到 AIGameWorld
```

## 文档 / Docs

项目知识库位于 `multi-agent-manager/knowledge/domains/dev/AIGameWorld-Studio/`：

| 文档 | 内容 |
|------|------|
| README.md | 项目入口 |
| docs/product/prd.md | 产品需求——三个核心角色 + 15 项功能 |
| docs/product/ROADMAP.md | 3 Phase 路线图 |
| docs/dev/architecture.md | 系统架构——5 层 + 模块导航 |
| implementation/task-plan.md | Phase 1 任务拆分（T1~T5） |

## 关键原则 / Key Principles

1. **Pack 是静态模板，运行时状态在 AIGameWorld backend**
2. **Def/Instance 分离**——YAML 是 Def，SQLite 是 Instance
3. **一套引擎 + 多 Pack**——DND/武侠/修仙共用 d20 引擎
4. **YAML Schema 强校验**——Pydantic v2，加载时拦截非法 Pack
