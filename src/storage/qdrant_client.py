"""QdrantClient — 纯 Qdrant HTTP 裸操作，零业务知识.

对标主项目 backend/src/storage/chroma_client.py 的分层契约：
- 只负责与 Qdrant 的底层 HTTP 通信（points search / count / delete）。
- 不含任何业务概念：不构造 filter、不解析 payload、不兜底文本。
- 业务层（service / repository）调用本客户端后自行处理 filter 与结果映射。

Studio 默认用 Qdrant Docker（HTTP 6333），裸 urllib 直调，避免引入
重客户端依赖；与主项目用 qdrant_client 库的方式解耦，仅覆盖知识库
检索所需的三个端点。
"""

from __future__ import annotations

import json
import logging
import urllib.request as _u

_log = logging.getLogger(__name__)


class QdrantClient:
    """Qdrant 连接 + 裸 points 操作（零业务）."""

    def __init__(self, qdrant_url: str = "http://127.0.0.1:6333"):
        # 去掉尾部斜杠，避免拼接出双斜杠
        self._base = qdrant_url.rstrip("/")

    # ── points search ──────────────────────────────────────────────
    def search_points(
        self,
        collection: str,
        vector: list[float],
        *,
        limit: int = 20,
        with_payload: bool = True,
        with_vectors: bool = False,
        query_filter: dict | None = None,
    ) -> list[dict]:
        """向量检索，返回 Qdrant 原始 result 列表（每个 hit 含 id / score / payload）.

        业务层负责把 payload 映射成 chunk 文本与元数据。
        """
        body = {
            "vector": vector,
            "limit": limit,
            "with_payload": with_payload,
            "with_vectors": with_vectors,
        }
        if query_filter:
            body["filter"] = query_filter
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = f"{self._base}/collections/{collection}/points/search"
        req = _u.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            # 发起 HTTP POST 并解析返回结果
            with _u.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw).get("result") or []
        except Exception as e:  # noqa: BLE001
            _log.warning("[qdrant] search failed collection=%s: %s", collection, e)
            return []

    # ── points count ──────────────────────────────────────────────
    def count_points(self, collection: str) -> int | None:
        """查 collection 的 points_count；失败返回 None.

        用于 stats / status 展示向量库条目数。
        """
        try:
            raw = _u.urlopen(f"{self._base}/collections/{collection}", timeout=8).read()
            return json.loads(raw)["result"].get("points_count")
        except Exception as e:  # noqa: BLE001
            _log.warning("[qdrant] count failed collection=%s: %s", collection, e)
            return None

    # ── points delete ─────────────────────────────────────────────
    def delete_points(self, collection: str, point_ids: list[str]) -> bool:
        """批量删 points；空列表直接返回 True.

        用于文档软删时同步清理向量库中的 chunk points。
        """
        if not point_ids:
            return True
        try:
            url = f"{self._base}/collections/{collection}/points/delete"
            body = json.dumps({"ids": point_ids}, ensure_ascii=False).encode("utf-8")
            req = _u.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _u.urlopen(req, timeout=15) as resp:
                resp.read()
            return True
        except Exception:  # noqa: BLE001
            _log.exception("[qdrant] delete failed collection=%s count=%d", collection, len(point_ids))
            return False
