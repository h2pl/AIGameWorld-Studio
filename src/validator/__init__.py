# 校验层 / Validator Layer
# YAML 结构校验 + 交叉引用检查（名字约定，无 Pydantic 依赖）

from src.validator.validate import ValidationResult, format_report, validate_template

__all__ = ["validate_template", "ValidationResult", "format_report"]
