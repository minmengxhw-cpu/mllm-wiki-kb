from __future__ import annotations

import argparse

from kb.indexing import search_rows


def command_search(args: argparse.Namespace, project_root_from_args, log_operation, row_authority_label) -> int:
    root = project_root_from_args(args.project_root)
    rows = search_rows(root, args.query, args.top_k)
    log_operation(root, "search", "ok", f"{len(rows)} results", {"query": args.query, "top_k": args.top_k})
    print(f"Query: {args.query}")
    print(f"Results: {len(rows)}")
    for idx, row in enumerate(rows, 1):
        print(f"\n{idx}. {row['title']}")
        print(
            f"   {row['account']} | {row['published_at']} | source={row_authority_label(row)} | "
            f"article_id={row['article_id']} chunk_id={row['chunk_id']}"
        )
        print(f"   raw: {row['raw_path']}")
        print(f"   {row['snippet']}")
    return 0


def command_ask(args: argparse.Namespace, project_root_from_args, log_operation, clean_snippet, row_source_line) -> int:
    root = project_root_from_args(args.project_root)
    rows = search_rows(root, args.query, args.top_k)
    if not rows:
        print("未检索到可靠来源。")
        log_operation(root, "ask", "no-results", "no sources", {"query": args.query})
        return 0

    print(f"# 回答：{args.query}\n")
    print("以下回答基于本地知识库检索结果生成；涉及事实细节应继续打开 raw 原文核对。\n")
    print("## 初步判断\n")
    print(
        "本地资料中已检索到相关来源，可以作为研究和写作的依据。"
        "从命中材料看，主题通常需要结合中央民盟材料的全国主线与上海民盟材料的地方实践来判断。"
        "下面列出可支撑判断的来源片段。\n"
    )
    print("## 证据摘录\n")
    for idx, row in enumerate(rows, 1):
        print(f"{idx}. {clean_snippet(row['snippet'])} [S{idx}]")
    print("\n## 来源\n")
    for idx, row in enumerate(rows, 1):
        print(f"- {row_source_line(row, idx)}")
    log_operation(root, "ask", "ok", f"{len(rows)} cited sources", {"query": args.query, "top_k": args.top_k})
    return 0
