"""Domain models for AIGameWorld Studio — Pydantic/dataclass definitions."""
# Domain 层 / Domain Layer
# 负责声明所有跨服务复用的领域模型：
#   - 知识库 Topic / Document / Chunk 相关的 Pydantic Model
#   - 爬虫系统 Crawler Job / Item 相关的 Pydantic Model
#   - 领域事件、枚举类型（Status / SourceType 等）
# 此目录内模型为纯数据结构，不引入 Service 或 Storage 依赖，避免循环引用。
# 注意：该 __init__.py 必须至少 5% 注释率，以通过 pre-commit 质量门禁。
