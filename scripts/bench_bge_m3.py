"""BGE-M3 embedding 速度基准：量化单 batch / 单 chunk 耗时，定位并发瓶颈。"""

from __future__ import annotations

import os
import time

os.environ.setdefault("KB_VECTOR_STORE", "qdrant")

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT))

from llama_index.embeddings.huggingface import HuggingFaceEmbedding  # noqa: E402

from src.services.knowledge.pipeline import _resolve_bge_m3_model_name  # noqa: E402

_model_path = _resolve_bge_m3_model_name()
print(f"[bench] model = {_model_path}")

model = HuggingFaceEmbedding(model_name=_model_path, trust_remote_code=True, embed_batch_size=64, local_files_only=True)

# 构造一批中文 chunk（模拟编年史文本长度）
sample = "泰坦在创世之初降临艾泽拉斯，用秩序魔法塑造了大陆与海洋，并创造了守护巨龙来维护世界的平衡。" * 8
texts_64 = [sample] * 64

# warmup
t0 = time.time()
model._get_text_embeddings(texts_64[:4])
print(f"[bench] warmup 4 chunks: {time.time() - t0:.2f}s")

# 测单 batch=64
t0 = time.time()
embs = model._get_text_embeddings(texts_64)
print(f"[bench] 1 batch (64 chunks): {time.time() - t0:.2f}s  → {time.time() - t0:.3f}s/chunk")

# 测 382 chunks 预估（6 batches）
n = 382
batches = (n + 63) // 64
t0 = time.time()
for _ in range(batches):
    model._get_text_embeddings(texts_64[:64])
dt = time.time() - t0
print(f"[bench] {batches} batches × 64 = {n} chunks: {dt:.1f}s  (≈单本编年史 Vol.1 embedding 耗时)")
print(f"[bench] 若 4 workers 并行（torch threads=1），理论 ≈ {dt / 4:.1f}s（理想）~ {dt:.1f}s（争抢）")
