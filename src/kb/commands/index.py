from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from kb.indexing import rebuild_fts, rebuild_vectors


def command_index(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> int:
    root = project_root_from_args(args.project_root)
    rows, count = rebuild_fts(root)
    vector_rows, vector_count = rebuild_vectors(root)
    vector_note = root / "index" / "chroma" / "README.md"
    vector_note.parent.mkdir(parents=True, exist_ok=True)
    vector_note.write_text(
        "# Local vector index\n\n当前版本使用 SQLite FTS5 + 本地哈希向量索引。哈希向量保存在 `chunk_vectors` 表，用于补充中文长问题和近义主题检索。\n",
        encoding="utf-8",
    )
    log_operation(root, "index", "ok", f"rebuilt sqlite fts: {count} chunks; vectors: {vector_count}", {"source_chunks": rows, "vector_source_chunks": vector_rows})
    print(f"SQLite FTS indexed chunks: {count}")
    print(f"Local vector indexed chunks: {vector_count}")
    print(f"Vector note: {vector_note}")
    return 0
