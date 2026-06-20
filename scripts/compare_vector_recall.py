#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kb.embeddings import DEFAULT_CHINESE_MODEL, HASH_MODEL  # noqa: E402
from kb.indexing import rebuild_vectors_with_info, semantic_rows  # noqa: E402


DEFAULT_QUERIES = [
    "沈钧儒在中国民主同盟历史和多党合作中的作用",
    "五一口号与民盟响应新政协的历史线索",
    "上海民盟参政议政社情民意调研建议素材",
    "上海民盟主题教育基层组织落实机制报道写法",
]


def prepare_temp_root(project_root: Path) -> Path:
    temp_root = Path(tempfile.mkdtemp(prefix="kb-vector-compare-"))
    (temp_root / "index").mkdir(parents=True)
    shutil.copy2(project_root / "schema.sql", temp_root / "schema.sql")
    shutil.copy2(project_root / "index" / "kb.sqlite", temp_root / "index" / "kb.sqlite")
    return temp_root


def titles_for_queries(root: Path, queries: list[str], top_k: int) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for query in queries:
        rows = semantic_rows(root, query, top_k)
        out[query] = [str(row["title"] or "") for row in rows]
    return out


def print_results(name: str, model: str, dim: int, fallback: str | None, results: dict[str, list[str]]) -> None:
    print(f"## {name}")
    print(f"model: {model} dim: {dim}")
    if fallback:
        print(f"fallback: {fallback}")
    for query, titles in results.items():
        print(f"\n### {query}")
        for idx, title in enumerate(titles, 1):
            print(f"{idx}. {title}")
        if not titles:
            print("- no semantic hits")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare hash vectors with a local Chinese embedding model on a temp DB copy.")
    parser.add_argument("--project-root", default=".", help="mllm-wiki-kb project root")
    parser.add_argument("--model", default=DEFAULT_CHINESE_MODEL, help="sentence-transformers model to compare")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--query", action="append", default=None, help="custom query; may be repeated")
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    db_path = project_root / "index" / "kb.sqlite"
    if not db_path.exists():
        print(f"SQLite DB not found: {db_path}", file=sys.stderr)
        return 2

    queries = args.query or DEFAULT_QUERIES
    temp_root = prepare_temp_root(project_root)
    try:
        _, _, hash_model, hash_dim, hash_fallback = rebuild_vectors_with_info(temp_root, HASH_MODEL)
        hash_results = titles_for_queries(temp_root, queries, args.top_k)

        _, _, model, dim, fallback = rebuild_vectors_with_info(temp_root, args.model)
        model_results = titles_for_queries(temp_root, queries, args.top_k)

        print_results("hash baseline", hash_model, hash_dim, hash_fallback, hash_results)
        print()
        print_results("candidate embedding", model, dim, fallback, model_results)
        if fallback:
            print("\nNote: candidate model fell back to hash. Install optional deps with `pip install -e '.[embeddings]'` and ensure the model is available locally.")
    finally:
        shutil.rmtree(temp_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
