from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from kb.sources import (
    load_pro_sources,
    pro_source_intake_tasks,
    pro_source_query_seeds,
    pro_sources_dir,
    pro_sources_report_markdown,
    sources_dashboard_markdown,
    sync_sources_table,
    url_candidates_markdown,
)
from kb.store import connect_db, ensure_schema_columns, now_iso


def command_pro_sources(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    report_dir: Callable[[Path], Path],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
    write_jsonl: Callable[[Path, list[dict]], None],
) -> int:
    root = project_root_from_args(args.project_root)
    created_at = now_iso()
    body = pro_sources_report_markdown(root, created_at, args.priority)
    if args.save:
        sources = load_pro_sources(root)
        tasks = pro_source_intake_tasks(sources, args.priority)
        seeds = pro_source_query_seeds(tasks)
        out_dir = pro_sources_dir(root)
        write_jsonl(out_dir / "intake_tasks.jsonl", tasks)
        write_jsonl(out_dir / "query_seeds.jsonl", seeds)
        path = report_dir(root) / "专业语料库首批来源入库工作台.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        append_wiki_log(root, f"生成专业来源入库任务：{path.relative_to(root)}")
        log_operation(root, "pro-sources", "ok", f"tasks={len(tasks)} seeds={len(seeds)}", {"output": str(path)})
        print(path)
    else:
        print(body)
    return 0


def command_sources(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    report_dir: Callable[[Path], Path],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> int:
    root = project_root_from_args(args.project_root)
    created_at = now_iso()
    body = sources_dashboard_markdown(root, created_at)
    if args.save:
        sources = load_pro_sources(root)
        conn = connect_db(root)
        try:
            ensure_schema_columns(conn)
            synced = sync_sources_table(conn, sources, created_at)
        finally:
            conn.close()
        path = report_dir(root) / "权威公开资料来源体检.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        append_wiki_log(root, f"生成权威公开资料来源体检：{path.relative_to(root)}")
        log_operation(root, "sources", "ok", f"authority source dashboard saved; synced={synced}", {"output": str(path), "synced": synced})
        print(f"Synced sources: {synced}")
        print(path)
    else:
        print(body)
    return 0


def command_source_urls(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    report_dir: Callable[[Path], Path],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> int:
    root = project_root_from_args(args.project_root)
    created_at = now_iso()
    body = url_candidates_markdown(root, created_at)
    if args.save:
        path = report_dir(root) / "第一批权威网页入库候选队列.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        append_wiki_log(root, f"生成权威网页入库候选队列：{path.relative_to(root)}")
        log_operation(root, "source-urls", "ok", "authority url candidates saved", {"output": str(path)})
        print(path)
    else:
        print(body)
    return 0
