"""Embedding 性能基准测试（快速版）/ Embedding performance benchmark (fast).

策略：只测 1 个 batch 的 embedding 耗时（不同 BLAS 线程数），
然后根据 batch 总数推算各方案总耗时。无需跑完全部 batches。

用法: uv run python -u scripts/bench_embedding.py
"""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from llama_index.core import Document, SimpleDirectoryReader
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

logging.basicConfig(level=logging.WARNING)

PDF_FILES = [
    Path(
        r"e:\Projects\AIGameWorld-Studio\knowledge-bases\world_of_warcraft\knowledge\documents\pdf\01_魔兽世界编年史·第一卷（Chronicle Vol.1）.pdf"
    ),
    Path(
        r"e:\Projects\AIGameWorld-Studio\knowledge-bases\world_of_warcraft\knowledge\documents\pdf\02_魔兽世界编年史·第二卷（Chronicle Vol.2）.pdf"
    ),
    Path(
        r"e:\Projects\AIGameWorld-Studio\knowledge-bases\world_of_warcraft\knowledge\documents\pdf\03_魔兽世界编年史·第三卷（Chronicle Vol.3）.pdf"
    ),
]

BATCH_SIZE = 64


def load_pdfs() -> list[Document]:
    docs: list[Document] = []
    for fp in PDF_FILES:
        if not fp.exists():
            continue
        reader = SimpleDirectoryReader(input_files=[str(fp)])
        sub_docs = reader.load_data()
        for d in sub_docs:
            d.excluded_llm_metadata_keys = []
            d.excluded_embed_metadata_keys = []
        docs.extend(sub_docs)
        print(f"  {fp.name[:30]}... → {len(sub_docs)} segments")
    return docs


def load_model() -> HuggingFaceEmbedding:
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    candidates = [
        hf_home / "hub" / "models--BAAI--bge-m3" / "snapshots",
        hf_home / "hub" / "models" / "BAAI--bge-m3" / "snapshots",
    ]
    model_path = "BAAI/bge-m3"
    for base in candidates:
        if base.exists():
            snapshots = [p for p in base.iterdir() if p.is_dir()]
            if snapshots:
                model_path = str(max(snapshots, key=lambda p: p.stat().st_mtime))
                break
    kwargs = {"trust_remote_code": True, "embed_batch_size": BATCH_SIZE}
    if model_path != "BAAI/bge-m3":
        kwargs["local_files_only"] = True
    print(f"  模型: {model_path}")
    return HuggingFaceEmbedding(model_name=model_path, **kwargs)


def time_one_batch(model: HuggingFaceEmbedding, texts: list[str], torch_threads: int) -> float:
    """测一个 batch 的 embedding 耗时（指定 BLAS 线程数）."""
    import torch

    original = torch.get_num_threads()
    torch.set_num_threads(torch_threads)
    # warmup（避免首次推理冷启动偏差）
    model._get_text_embeddings(texts[:4])
    # 正式测 1 次
    t0 = time.time()
    model._get_text_embeddings(texts)
    elapsed = time.time() - t0
    torch.set_num_threads(original)
    return elapsed


def main():
    print("=" * 70)
    print("  Embedding 性能基准测试（快速版）")
    print("=" * 70)

    cpu_count = os.cpu_count() or 4
    print(f"\nCPU 逻辑核数: {cpu_count}")

    print("\n[1/3] 加载 BGE-M3 模型...")
    model = load_model()

    print("\n[2/3] 加载 PDF + 切 chunks...")
    docs = load_pdfs()
    print(f"  总计: {len(docs)} segments")

    splitter = SentenceSplitter(chunk_size=800, chunk_overlap=120)
    splitter.include_metadata = True
    splitter.include_prev_next_rel = True
    try:
        splitter.excluded_embed_metadata_keys = []
        splitter.excluded_llm_metadata_keys = []
    except Exception:
        pass
    nodes = splitter.get_nodes_from_documents(docs)
    total_chunks = len(nodes)
    total_batches = math.ceil(total_chunks / BATCH_SIZE)
    print(f"  总计: {total_chunks} chunks → {total_batches} batches (batch_size={BATCH_SIZE})")

    # 取第一个 batch 的 texts 做基准测试
    batch_texts = [getattr(n, "text", "") or "" for n in nodes[:BATCH_SIZE]]
    print(f"\n[3/3] 基准测试（1 batch = {len(batch_texts)} texts，每项测 3 次取中位数）...")

    # 测不同 BLAS 线程数下的单 batch 耗时
    results: dict[int, float] = {}
    for threads in [1, 2, 4, 6, cpu_count]:
        t = time_one_batch(model, batch_texts, threads)
        results[threads] = t
        print(f"  {threads:2d} BLAS 线程: {t:.1f}s/batch")

    # 推算各方案总耗时
    t1 = results[1]
    t6 = results.get(6, results.get(cpu_count, t1))
    t_full = results.get(cpu_count, t6)

    print("\n" + "=" * 70)
    print(f"  推算总耗时（{total_batches} batches）")
    print("=" * 70)

    # 方案 A：串行 + 全 BLAS
    plan_a = total_batches * t_full
    print(f"\n  方案 A: 串行 + {cpu_count} BLAS 线程")
    print(f"    {total_batches} batches × {t_full:.1f}s = {plan_a:.0f}s ({plan_a / 60:.1f}min)")

    # 方案 B4：并行 4 workers + 1 BLAS（当前代码 max_workers=4）
    rounds_b4 = math.ceil(total_batches / 4)
    plan_b4 = rounds_b4 * t1
    print("\n  方案 B(4 workers): 并行 4 workers + 1 BLAS 线程（当前代码默认）")
    print(f"    ceil({total_batches}/4) = {rounds_b4} 轮 × {t1:.1f}s = {plan_b4:.0f}s ({plan_b4 / 60:.1f}min)")
    print(f"    vs 方案 A: {plan_b4 / plan_a:.2f}x {'✅ 更快' if plan_b4 < plan_a else '❌ 更慢'}")

    # 方案 B12：并行 cpu_count workers + 1 BLAS
    workers = min(cpu_count, total_batches)
    rounds_b12 = math.ceil(total_batches / workers)
    plan_b12 = rounds_b12 * t1
    print(f"\n  方案 B({workers} workers): 并行 {workers} workers + 1 BLAS 线程（建议优化）")
    print(
        f"    ceil({total_batches}/{workers}) = {rounds_b12} 轮 × {t1:.1f}s = {plan_b12:.0f}s ({plan_b12 / 60:.1f}min)"
    )
    print(f"    vs 方案 A: {plan_b12 / plan_a:.2f}x {'✅ 更快' if plan_b12 < plan_a else '❌ 更慢'}")

    # 方案 C：每文件独立串行（和方案 A 本质相同）
    print(f"\n  方案 C: 每文件独立串行 + {cpu_count} BLAS 线程")
    print(f"    总 batch 数不变，embedding 时间 ≈ 方案 A = {plan_a:.0f}s")
    print("    （每文件一个 job 不会更快，因为瓶颈是总 embedding 计算量）")

    # 结论
    print("\n" + "=" * 70)
    print("  结论")
    print("=" * 70)
    blas_ratio = t1 / t_full
    print(f"  BLAS {cpu_count}→1 线程降速比: {blas_ratio:.1f}x")
    print(f"  （1 BLAS 线程比 {cpu_count} BLAS 线程慢 {blas_ratio:.1f} 倍）")

    if plan_b12 < plan_a:
        speedup = plan_a / plan_b12
        print(f"\n  ✅ 并行 {workers} workers 最快，比串行快 {speedup:.1f}x")
        print(f"  → 应该把 max_workers 从 4 改为 {workers}")
    else:
        print("\n  ✅ 串行 + 全 BLAS 最快")
        print("  → 不需要并行 embedding，回退到 IngestionPipeline.run()")

    if plan_b4 > plan_a:
        print(f"\n  ⚠️ 当前 max_workers=4 比串行还慢！（{plan_b4 / plan_a:.2f}x）")


if __name__ == "__main__":
    main()
