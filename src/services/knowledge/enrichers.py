"""可插拔的元数据增强器 / Pluggable metadata enrichers.

架构定位
--------
``KnowledgeReader`` 只负责通用元数据提取（file_path / sha256 / page_number 等），
不包含任何特定 IP / 主题的业务逻辑。

主题特定的元数据增强（如「魔兽世界编年史卷数识别」「原神地区分类」）通过
``MetadataEnricher`` 协议实现为独立插件，在构造 Reader 时按需注入。

扩展示例
--------
.. code-block:: python

    class GenshinRegionEnricher(MetadataEnricher):
        def enrich(self, file_path: Path, metadata: dict) -> dict:
            # 从文件名/内容识别蒙德/璃月/稻妻/须弥/枫丹等地区
            ...
            return metadata

    reader = KnowledgeReader(enrichers=[GenshinRegionEnricher()])
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class MetadataEnricher(Protocol):
    """元数据增强器协议：根据文件路径增强 Document 元数据."""

    def enrich(self, file_path: Path, metadata: dict) -> dict:
        """根据文件路径增强元数据，返回更新后的 metadata.

        Parameters
        ----------
        file_path : Path
            当前文件的绝对路径。
        metadata : dict
            已有的通用元数据（file_path / sha256 / page_number 等），
            增强器可以读取这些字段并追加自己的业务字段。

        Returns
        -------
        dict
            更新后的 metadata（通常是原地修改后返回）。
        """
        ...


# ---------------------------------------------------------------------------
# 内置增强器
# ---------------------------------------------------------------------------


class ChronicleVolumeEnricher:
    """魔兽世界编年史卷数识别增强器.

    从文件名识别编年史卷数，支持中英文混合命名。

    例：
      01_魔兽世界编年史·第一卷（Chronicle Vol.1）.pdf → "Chronicle Vol.1 / 第一卷"
      魔兽世界编年史·第二卷.pdf                        → "第二卷"
      Chronicle_Vol_3.pdf                              → "Chronicle Vol.3"
    """

    # Vol + 数字（Vol.1 / Vol_2 / VOL-3）
    _RE_VOL_EN = re.compile(r"Vol[\.\s_\-]*(\d+)", flags=re.IGNORECASE)
    # 中文「第X卷」（第一卷 / 第2卷 / 第十二卷）
    _RE_VOL_CN = re.compile(r"第\s*([一二三四五六七八九十百千0-9]+)\s*卷")

    def enrich(self, file_path: Path, metadata: dict) -> dict:
        name = file_path.name
        if not name:
            return metadata

        vol_en = None
        vol_cn = None

        m = self._RE_VOL_EN.search(name)
        if m:
            vol_en = f"Chronicle Vol.{m.group(1)}"

        m2 = self._RE_VOL_CN.search(name)
        if m2:
            vol_cn = f"第{m2.group(1)}卷"

        if vol_en and vol_cn:
            metadata["chronicle_volume"] = f"{vol_en} / {vol_cn}"
        elif vol_en or vol_cn:
            metadata["chronicle_volume"] = vol_en or vol_cn

        return metadata


# ---------------------------------------------------------------------------
# 主题 → 增强器映射
# ---------------------------------------------------------------------------

# 已注册的主题增强器：topic_id → list[MetadataEnricher]
# 新增主题时在此注册，或通过 KnowledgeManager.register_enrichers() 动态注册
_TOPIC_ENRICHERS: dict[str, list[MetadataEnricher]] = {
    "world_of_warcraft": [ChronicleVolumeEnricher()],
}


def get_enrichers_for_topic(topic_id: str) -> list[MetadataEnricher]:
    """获取指定主题的元数据增强器列表.

    Parameters
    ----------
    topic_id : str
        主题 ID（如 world_of_warcraft / genshin / xianjian）。

    Returns
    -------
    list[MetadataEnricher]
        该主题注册的所有增强器实例，无匹配时返回空列表。
    """
    return list(_TOPIC_ENRICHERS.get(topic_id, []))
