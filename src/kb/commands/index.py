from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from kb.indexing import rebuild_fts, rebuild_vectors_with_info


def command_index(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> int:
    root = project_root_from_args(args.project_root)
    rows, count = rebuild_fts(root)
    vector_rows, vector_count, model, dim, fallback_reason = rebuild_vectors_with_info(root)
    vector_note = root / "index" / "chroma" / "README.md"
    vector_note.parent.mkdir(parents=True, exist_ok=True)
    fallback_note = f"\n\nFallback: {fallback_reason}\n" if fallback_reason else ""
    vector_note.write_text(
        f"# Local vector index\n\n当前向量模型：`{model}`，维度：{dim}。向量保存在 `chunk_vectors` 表，用于补充中文长问题和近义主题检索。{fallback_note}\n",
        encoding="utf-8",
    )
    log_operation(root, "index", "ok", f"rebuilt sqlite fts: {count} chunks; vectors: {vector_count}", {"source_chunks": rows, "vector_source_chunks": vector_rows, "vector_model": model, "vector_dim": dim, "fallback_reason": fallback_reason})
    print(f"SQLite FTS indexed chunks: {count}")
    print(f"Local vector indexed chunks: {vector_count}")
    print(f"Vector model: {model} ({dim} dim)")
    if fallback_reason:
        print(f"Vector fallback: {fallback_reason}")
    print(f"Vector note: {vector_note}")
    return 0


def command_reindex_vectors(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> int:
    root = project_root_from_args(args.project_root)
    vector_rows, vector_count, model, dim, fallback_reason = rebuild_vectors_with_info(root, args.model)
    status = "fallback" if fallback_reason else "ok"
    log_operation(root, "reindex-vectors", status, f"vectors: {vector_count}", {"vector_source_chunks": vector_rows, "vector_model": model, "vector_dim": dim, "fallback_reason": fallback_reason})
    print(f"Local vector indexed chunks: {vector_count}")
    print(f"Vector model: {model} ({dim} dim)")
    if fallback_reason:
        print(f"Vector fallback: {fallback_reason}")
    return 0
