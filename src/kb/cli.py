from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from kb.commands import export as export_commands
from kb.commands import corpus as corpus_commands
from kb.commands import compile as compile_commands
from kb.commands import ingest as ingest_commands
from kb.commands import index as index_commands
from kb.commands import obsidian as obsidian_commands
from kb.commands import search_ask as search_ask_commands
from kb.commands import sources as sources_commands
from kb.commands import staff as staff_commands
from kb.commands.obsidian import (
    DEFAULT_OBSIDIAN_VAULT,
    generated_region,
    merge_generated,
    merge_generated_with_fresh_metadata,
    obsidian_sync_pairs,
    obsidian_sync_status,
    sync_file,
    write_obsidian_manifest,
)
from kb.indexing import authority_rank, dict_to_row, query_terms, rebuild_fts, rebuild_vectors, search_rows
from kb.ingest import (
    ArticleDoc,
    extract_doc,
    iter_input_files,
    sha256_text,
    slugify,
)

from kb.staff_check import (
    issue_table,
    load_blacklist,
    load_formulations,
    match_blacklist,
    match_staff_items,
    staff_check_issues,
    staff_severity_rank,
    severity_label,
)
from kb.store import connect_db, db_path, ensure_schema_columns, now_iso


WIKI_DIRS = [
    "研究助手",
    "人物",
    "组织",
    "事件",
    "会议",
    "盟史",
    "参政议政",
    "思想宣传",
    "社会服务",
    "主题教育",
    "传统教育基地",
    "文稿素材",
    "口述史",
]

def project_root_from_args(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    env_value = os.environ.get("KB_PROJECT_ROOT")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return Path(__file__).resolve().parents[2]
def ensure_dirs(root: Path) -> None:
    dirs = [
        "data/incoming",
        "data/raw",
        "data/attachments",
        "data/quarantine",
        "wiki",
        "obsidian",
        "index/chroma",
        "index/manifests",
        "exports/markdown",
        "exports/docx",
        "exports/pdf",
        "templates",
        "src/kb",
        "tests",
    ]
    for item in dirs:
        (root / item).mkdir(parents=True, exist_ok=True)
    for name in WIKI_DIRS:
        (root / "wiki" / name).mkdir(parents=True, exist_ok=True)


def init_db(root: Path) -> None:
    schema = (root / "schema.sql").read_text(encoding="utf-8")
    conn = connect_db(root)
    try:
        conn.executescript(schema)
        ensure_schema_columns(conn)
        conn.commit()
    finally:
        conn.close()


def log_operation(root: Path, operation: str, status: str, message: str, details: dict | None = None) -> None:
    try:
        conn = connect_db(root)
        conn.execute(
            "INSERT INTO operations_log(operation, status, message, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (operation, status, message, json.dumps(details or {}, ensure_ascii=False), now_iso()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def append_wiki_log(root: Path, message: str) -> None:
    path = root / "wiki" / "log.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else "# 操作日志\n"
    marker = "<!-- KB-GENERATED:END -->"
    entry = f"\n- {now_iso()} {message}\n"
    if marker in text:
        text = text.replace(marker, entry + "\n" + marker)
    else:
        text += entry
    path.write_text(text, encoding="utf-8")


def command_init(args: argparse.Namespace) -> int:
    return ingest_commands.command_init(
        args,
        project_root_from_args,
        ensure_dirs,
        init_db,
        append_wiki_log,
        log_operation,
    )


def command_scan(args: argparse.Namespace) -> int:
    return ingest_commands.command_scan(args, project_root_from_args, log_operation)


def command_import(args: argparse.Namespace) -> int:
    return ingest_commands.command_import(
        args,
        project_root_from_args,
        ensure_dirs,
        init_db,
        append_wiki_log,
        log_operation,
    )


def command_ingest_file(args: argparse.Namespace) -> int:
    return ingest_commands.command_ingest_file(
        args,
        project_root_from_args,
        ensure_dirs,
        init_db,
        append_wiki_log,
        log_operation,
        clean_snippet,
    )


def insert_article_doc(
    conn: sqlite3.Connection,
    root: Path,
    doc: ArticleDoc,
    source_path: str,
    source_id: str | None,
    authority_level: str,
    source_tier: str,
    is_citable: int,
    content_hash: str,
) -> int:
    return ingest_commands.insert_article_doc(
        conn,
        root,
        doc,
        source_path,
        source_id,
        authority_level,
        source_tier,
        is_citable,
        content_hash,
    )


def command_ingest_url(args: argparse.Namespace) -> int:
    return ingest_commands.command_ingest_url(
        args,
        project_root_from_args,
        ensure_dirs,
        init_db,
        append_wiki_log,
        log_operation,
        clean_snippet,
    )


def command_check(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    text_parts = getattr(args, "text", None) or []
    file_arg = getattr(args, "file", None)
    if file_arg or text_parts:
        if file_arg:
            path = Path(file_arg).expanduser()
            if not path.exists():
                print(f"check file not found: {path}", file=sys.stderr)
                return 2
            text = path.read_text(encoding="utf-8")
        else:
            text = " ".join(text_parts)
        issues = staff_check_issues(root, text)
        body = staff_check_body(root, text, [])
        print(body)
        hard_issues = [issue for issue in issues if staff_severity_rank(issue.get("severity")) <= staff_severity_rank("high")]
        log_operation(root, "check", "blocked" if hard_issues else "ok", f"draft issues={len(issues)} hard={len(hard_issues)}")
        return 1 if hard_issues else 0
    db_exists = db_path(root).exists()
    article_count = 0
    chunk_count = 0
    if db_exists:
        conn = connect_db(root)
        article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        chunk_count = conn.execute("SELECT COUNT(*) FROM article_chunks").fetchone()[0]
        conn.close()
    raw_count = len(list((root / "data" / "raw").rglob("*.md"))) if (root / "data" / "raw").exists() else 0
    wiki_count = len(list((root / "wiki").rglob("*.md"))) if (root / "wiki").exists() else 0
    print(f"Project: {root}")
    print(f"SQLite: {'ok' if db_exists else 'missing'} ({db_path(root)})")
    print(f"Articles: {article_count}")
    print(f"Chunks: {chunk_count}")
    print(f"Raw markdown files: {raw_count}")
    print(f"Wiki markdown files: {wiki_count}")
    print(f"Chroma index dir: {root / 'index' / 'chroma'}")
    log_operation(root, "check", "ok", "checked project status")
    return 0


def obsidian_status_markdown(root: Path, vault: Path, created_at: str) -> str:
    return obsidian_commands.obsidian_status_markdown(root, vault, created_at, markdown_table, status_label)


def command_obsidian_sync(args: argparse.Namespace) -> int:
    return obsidian_commands.command_obsidian_sync(args, project_root_from_args, log_operation)


def command_obsidian_status(args: argparse.Namespace) -> int:
    return obsidian_commands.command_obsidian_status(
        args,
        project_root_from_args,
        report_dir,
        markdown_table,
        status_label,
        append_wiki_log,
        log_operation,
    )


def command_log(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    conn = connect_db(root)
    rows = conn.execute(
        "SELECT created_at, operation, status, message FROM operations_log ORDER BY id DESC LIMIT ?",
        (args.limit,),
    ).fetchall()
    conn.close()
    for row in rows:
        print(f"{row['created_at']} {row['operation']} {row['status']} {row['message']}")
    return 0


def command_index(args: argparse.Namespace) -> int:
    return index_commands.command_index(args, project_root_from_args, log_operation)


def _configure_staff_commands() -> None:
    staff_commands.configure(
        project_root_from_args,
        append_wiki_log,
        log_operation,
        init_db,
        markdown_table,
        report_dir,
        write_jsonl,
        classify_article,
        corpus_dir,
        year_at_least,
        writing_sample_score,
        load_article_labels,
        write_wiki_page,
        timeline_candidates,
        ARTICLE_TYPE_NAMES,
        WRITING_STYLE_GUIDES,
    )


def clean_snippet(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.clean_snippet(*args, **kwargs)

def row_source_line(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.row_source_line(*args, **kwargs)

def row_authority_label(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.row_authority_label(*args, **kwargs)

def row_source_md(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.row_source_md(*args, **kwargs)

def unique_source_rows(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.unique_source_rows(*args, **kwargs)

def staff_index_dir(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_index_dir(*args, **kwargs)

def load_jsonl(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.load_jsonl(*args, **kwargs)

def load_staff_entities(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.load_staff_entities(*args, **kwargs)

def load_external_inventory(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.load_external_inventory(*args, **kwargs)

def citation_table(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.citation_table(*args, **kwargs)

def cited_excerpts(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.cited_excerpts(*args, **kwargs)

def staff_formulation_lines(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_formulation_lines(*args, **kwargs)

def staff_entity_lines(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_entity_lines(*args, **kwargs)

def staff_risk_lines(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_risk_lines(*args, **kwargs)

def staff_query(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_query(*args, **kwargs)

def topic_query_variants(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.topic_query_variants(*args, **kwargs)

def history_query_variants(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.history_query_variants(*args, **kwargs)

def merge_search_rows(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.merge_search_rows(*args, **kwargs)

def staff_search_rows(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_search_rows(*args, **kwargs)

def staff_card_matches(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_card_matches(*args, **kwargs)

def staff_cards_block(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_cards_block(*args, **kwargs)

def external_reference_matches(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.external_reference_matches(*args, **kwargs)

def external_reference_block(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.external_reference_block(*args, **kwargs)

def external_sources_report_markdown(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.external_sources_report_markdown(*args, **kwargs)

def research_dossier_matches(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.research_dossier_matches(*args, **kwargs)

def research_dossier_block(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.research_dossier_block(*args, **kwargs)

def staff_history_research_route(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_history_research_route(*args, **kwargs)

def staff_draft_article_type(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_draft_article_type(*args, **kwargs)

def staff_curated_writing_samples(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_curated_writing_samples(*args, **kwargs)

def staff_draft_structure_block(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_draft_structure_block(*args, **kwargs)

def material_points(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.material_points(*args, **kwargs)

def material_field_hints(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.material_field_hints(*args, **kwargs)

def title_suggestions(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.title_suggestions(*args, **kwargs)

def draft_paragraphs_from_material(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.draft_paragraphs_from_material(*args, **kwargs)

def staff_material_draft_body(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_material_draft_body(*args, **kwargs)

def staff_draft_body(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_draft_body(*args, **kwargs)

def staff_history_body(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_history_body(*args, **kwargs)

def normalized_similarity(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.normalized_similarity(*args, **kwargs)

def topic_similarity_rows(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.topic_similarity_rows(*args, **kwargs)

def staff_topic_body(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_topic_body(*args, **kwargs)

def staff_info_body(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_info_body(*args, **kwargs)

def staff_stats_matches(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_stats_matches(*args, **kwargs)

def counter_table(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.counter_table(*args, **kwargs)

def staff_stats_body(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_stats_body(*args, **kwargs)

def staff_check_body(*args, **kwargs):
    _configure_staff_commands()
    return staff_commands.staff_check_body(*args, **kwargs)

def command_pro_sources(args: argparse.Namespace) -> int:
    _configure_staff_commands()
    return staff_commands.command_pro_sources(args)


def command_sources(args: argparse.Namespace) -> int:
    _configure_staff_commands()
    return staff_commands.command_sources(args)


def command_source_urls(args: argparse.Namespace) -> int:
    _configure_staff_commands()
    return staff_commands.command_source_urls(args)


def command_staff(args: argparse.Namespace) -> int:
    _configure_staff_commands()
    return staff_commands.command_staff(args)


ARTICLE_TYPE_RULES = corpus_commands.ARTICLE_TYPE_RULES
ARTICLE_TYPE_NAMES = corpus_commands.ARTICLE_TYPE_NAMES
WRITING_STYLE_GUIDES = corpus_commands.WRITING_STYLE_GUIDES
TOPIC_KEYWORDS = corpus_commands.TOPIC_KEYWORDS


def corpus_dir(root: Path) -> Path:
    return corpus_commands.corpus_dir(root)


def report_dir(root: Path) -> Path:
    return root / "wiki" / "研究助手"


def article_year(published_at: str | None) -> str:
    return corpus_commands.article_year(published_at)


def year_at_least(year: str | None, minimum: str) -> bool:
    return corpus_commands.year_at_least(year, minimum)


def classify_article(title: str, account: str | None, text: str) -> tuple[str, int, list[str]]:
    return corpus_commands.classify_article(title, account, text)


def write_jsonl(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in items) + "\n",
        encoding="utf-8",
    )


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * len(rows[0])) + " |"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows[1:])
    return "\n".join(lines)


def build_article_label(row: sqlite3.Row) -> dict:
    return corpus_commands.build_article_label(row)


def load_article_labels(root: Path) -> list[dict]:
    return corpus_commands.load_article_labels(root)


def corpus_dashboard_markdown(labels: list[dict], created_at: str) -> str:
    return corpus_commands.corpus_dashboard_markdown(labels, created_at)


def apply_review_decisions_to_labels(labels: list[dict], decisions: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    return corpus_commands.apply_review_decisions_to_labels(labels, decisions)


def collect_review_decisions(root: Path) -> tuple[dict[int, dict], list[str]]:
    return corpus_commands.collect_review_decisions(root)


def corpus_audit_markdown(labels: list[dict], created_at: str) -> str:
    return corpus_commands.corpus_audit_markdown(labels, created_at)


def corpus_review_apply_markdown(applied: list[dict], warnings: list[str], created_at: str) -> str:
    return corpus_commands.corpus_review_apply_markdown(applied, warnings, created_at)


def corpus_quality_diagnostic_markdown(labels: list[dict], created_at: str, limit: int = 30) -> str:
    return corpus_commands.corpus_quality_diagnostic_markdown(labels, created_at, limit)


def corpus_priority_review_markdown(rows: list[dict], created_at: str) -> str:
    return corpus_commands.corpus_priority_review_markdown(rows, created_at)


def corpus_priority_review_rows(labels: list[dict], limit: int = 100) -> list[dict]:
    return corpus_commands.corpus_priority_review_rows(labels, limit)


def corpus_review_rows(labels: list[dict], per_type: int, low_confidence_limit: int, other_limit: int) -> list[dict]:
    return corpus_commands.corpus_review_rows(labels, per_type, low_confidence_limit, other_limit)


def curated_writing_samples_markdown(labels: list[dict], created_at: str, limit_per_type: int = 8) -> str:
    return corpus_commands.curated_writing_samples_markdown(labels, created_at, limit_per_type)


def history_research_entry_markdown(labels: list[dict], created_at: str, limit_per_group: int = 40) -> str:
    return corpus_commands.history_research_entry_markdown(labels, created_at, limit_per_group)


def policy_advice_material_index_markdown(labels: list[dict], created_at: str, limit: int = 80) -> str:
    return corpus_commands.policy_advice_material_index_markdown(labels, created_at, limit)


def people_hits_for_text(text: str) -> list[str]:
    return corpus_commands.people_hits_for_text(text)


def shanghai_style_rule_card_markdown(labels: list[dict], created_at: str) -> str:
    return corpus_commands.shanghai_style_rule_card_markdown(labels, created_at)


def write_corpus_review_csv(path: Path, rows: list[dict]) -> None:
    corpus_commands.write_corpus_review_csv(path, rows)


def writing_sample_score(label: dict) -> tuple[int, list[str]]:
    return corpus_commands.writing_sample_score(label)


def writing_style_templates_markdown(labels: list[dict], created_at: str, limit_per_type: int = 12) -> str:
    return corpus_commands.writing_style_templates_markdown(labels, created_at, limit_per_type)


def _configure_corpus_commands() -> None:
    corpus_commands.configure(project_root_from_args, append_wiki_log, log_operation)


def command_corpus_audit(args: argparse.Namespace) -> int:
    _configure_corpus_commands()
    return corpus_commands.command_corpus_audit(args)


def command_corpus_apply_reviews(args: argparse.Namespace) -> int:
    _configure_corpus_commands()
    return corpus_commands.command_corpus_apply_reviews(args)


def command_corpus(args: argparse.Namespace) -> int:
    _configure_corpus_commands()
    return corpus_commands.command_corpus(args)


def command_corpus_style(args: argparse.Namespace) -> int:
    _configure_corpus_commands()
    return corpus_commands.command_corpus_style(args)


def command_external_sources(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    created_at = now_iso()
    body = external_sources_report_markdown(root, created_at)
    if args.save:
        path = report_dir(root) / "Google Drive外部参考层状态.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        append_wiki_log(root, f"生成 Drive 外部参考层状态：{path.relative_to(root)}")
        log_operation(root, "external-sources", "ok", "report saved", {"output": str(path)})
        print(path)
    else:
        print(body)
    return 0


def guardrails_report_markdown(root: Path, created_at: str) -> str:
    blacklist = load_blacklist(root)
    formulations = load_formulations(root)
    severity_counts = Counter((item.get("severity") or "未标注").strip() for item in blacklist)
    category_counts = Counter((item.get("category") or "未分类").strip() for item in blacklist)
    formulation_status_counts = Counter((item.get("status") or "未标注").strip() for item in formulations)

    severity_rows = [["严重程度", "数量"]]
    for key in ["blocker", "high", "medium", "low", "未标注"]:
        if severity_counts.get(key):
            severity_rows.append([key, str(severity_counts[key])])

    category_rows = [["类别", "数量"]]
    for category, count in category_counts.most_common():
        category_rows.append([category, str(count)])

    formulation_rows = [["状态", "数量"]]
    for status, count in formulation_status_counts.most_common():
        formulation_rows.append([status, str(count)])

    blacklist_rows = [["风险词", "类别", "严重程度", "建议表述"]]
    for item in sorted(blacklist, key=lambda row: staff_severity_rank(row.get("severity")))[:80]:
        blacklist_rows.append(
            [
                item.get("pattern") or "",
                item.get("category") or "",
                item.get("severity") or "",
                item.get("canonical") or "",
            ]
        )

    formulation_detail_rows = [["口径项", "状态", "规范表述", "风险变体"]]
    for item in formulations[:80]:
        variants = item.get("variants") or []
        variant_text = "、".join(str(v) for v in variants) if isinstance(variants, list) else str(variants)
        formulation_detail_rows.append(
            [
                item.get("term") or "",
                item.get("status") or "",
                item.get("canonical") or "",
                variant_text,
            ]
        )

    return f"""# 口径风险清单

生成时间：{created_at}

## 总体判断

- 当前黑名单词条：{len(blacklist)} 条。
- 当前口径库条目：{len(formulations)} 条。
- 这些条目会被 `/核` 和 `kb staff check` 调用，用于提醒机构名称、人物姓名、史实雷区、公开语料边界和成稿边界。
- 本清单是工作用种子库，不替代红头文件、内部口径和人工终审。

## 黑名单严重程度

{markdown_table(severity_rows)}

## 黑名单类别

{markdown_table(category_rows)}

## 口径库状态

{markdown_table(formulation_rows)}

## 黑名单明细

{markdown_table(blacklist_rows)}

## 口径库明细

{markdown_table(formulation_detail_rows)}

## 使用办法

1. 写稿前先用 `/稿` 输出素材包，必要时查看本页确认高风险表述。
2. 成稿后用 `/核` 或 `kb staff check` 逐条拦截。
3. 命中 blocker 或 high 的内容，不直接定稿，必须回到权威来源或正式口径核定。
"""


def command_guardrails(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    body = guardrails_report_markdown(root, now_iso())
    if args.save:
        path = report_dir(root) / "口径风险清单.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        append_wiki_log(root, f"生成口径风险清单：{path.relative_to(root)}")
        log_operation(root, "guardrails", "ok", "report saved", {"output": str(path)})
        print(path)
    else:
        print(body)
        log_operation(root, "guardrails", "ok", "printed")
    return 0


def command_brief(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    query = " ".join(args.query).strip()
    if not query:
        print("brief requires a query", file=sys.stderr)
        return 2
    rows = search_rows(root, query, args.top_k)
    body = brief_body(query, rows)
    if args.save:
        path = write_wiki_page(root, f"简报素材：{query}", "assistant", body, rows)
        append_wiki_log(root, f"生成简报素材：{path.relative_to(root)}")
        print(f"Saved: {path}\n")
    print(body)
    log_operation(root, "brief", "ok", f"{len(rows)} sources", {"sources": len(rows)})
    return 0


def count_db_rows(root: Path, table: str) -> int:
    if not (root / "index" / "kb.sqlite").exists():
        return 0
    conn = connect_db(root)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def status_label(ok: bool) -> str:
    return "可用" if ok else "缺失"


def verify_report_markdown(root: Path, created_at: str) -> str:
    labels = load_article_labels(root)
    article_count = count_db_rows(root, "articles")
    chunk_count = count_db_rows(root, "article_chunks")
    fts_count = count_db_rows(root, "article_chunks_fts")
    vector_count = count_db_rows(root, "chunk_vectors")
    core_files = [
        "wiki/研究助手/民盟研究助手首页.md",
        "wiki/研究助手/上海民盟微信公众号写作风格规则卡.md",
        "wiki/研究助手/上海民盟微信公众号精选写作样本.md",
        "wiki/研究助手/上海民盟微信公众号分体裁写作模板.md",
        "wiki/研究助手/微信公众号参政议政素材主题库.md",
        "wiki/研究助手/微信公众号文史盟史研究入口清单.md",
        "wiki/研究助手/Google Drive外部参考层状态.md",
        "wiki/研究助手/Obsidian同步状态.md",
        "wiki/研究助手/口径风险清单.md",
        "wiki/研究助手/微信公众号分类人工校订应用报告.md",
        "index/corpus/article_labels.jsonl",
        "index/formulations.jsonl",
        "index/blacklist.csv",
        "index/external_sources/google_drive_inventory.jsonl",
    ]
    file_rows = [["产物", "状态"]]
    for rel in core_files:
        file_rows.append([f"`{rel}`", status_label((root / rel).exists())])
    modes = [
        ["/稿", "`kb staff draft`", "微信公众号文稿素材和初稿"],
        ["/史", "`kb staff history`", "盟史、人物、事件研究入口"],
        ["/信", "`kb staff info`", "统战信息/参政议政素材包"],
        ["/题", "`kb staff topic`", "选题查重和差异化角度"],
        ["/数", "`kb staff stats`", "语料统计和选题分布"],
        ["/核", "`kb staff check`", "口径、史实和引用风险预审"],
    ]
    people_dossiers = len(list((root / "wiki" / "研究助手" / "核心人物研究档案").glob("*.md")))
    event_dossiers = len(list((root / "wiki" / "研究助手" / "核心事件研究档案").glob("*.md")))
    external_items = len(load_external_inventory(root))
    obsidian_manifest = root / "obsidian" / "vault_manifest.json"
    obsidian_note = "未生成"
    if obsidian_manifest.exists():
        try:
            manifest = json.loads(obsidian_manifest.read_text(encoding="utf-8"))
            obsidian_note = f"{manifest.get('current', 0)}/{manifest.get('source_files', 0)} 已一致，缺失 {manifest.get('missing', 0)}，需更新 {manifest.get('stale', 0)}"
        except json.JSONDecodeError:
            obsidian_note = "清单损坏，需重新运行 kb obsidian-status --save"
    ready = bool(article_count and labels and fts_count and all((root / rel).exists() for rel in core_files))
    return f"""# 盟参系统可用性验收报告

生成时间：{created_at}

## 总体判断

- 当前状态：{status_label(ready)}。
- 公开微信公众号文章、写作规则、盟史研究入口、参政议政素材、Drive 外部参考层和 staff 指令入口均已纳入本地工作台。
- 本报告只证明本地公开语料库和工具链可用；正式发稿、史实结论和内部口径仍需人工终审。

## 数据底座

| 项目 | 数量 |
| --- | ---: |
| 数据库文章 | {article_count} |
| 检索片段 | {chunk_count} |
| FTS 索引片段 | {fts_count} |
| 本地向量片段 | {vector_count} |
| 文章标签 | {len(labels)} |
| 核心人物研究档案 | {people_dossiers} |
| 核心事件研究档案 | {event_dossiers} |
| Drive 外部参考记录 | {external_items} |
| Obsidian 同步清单 | {obsidian_note} |

## 关键产物

{markdown_table(file_rows)}

## 可用指令

{markdown_table([["口令", "命令", "用途"]] + modes)}

## 验收结论

- 写作：可用。可通过 `/稿`、写作风格规则卡、精选样本和分体裁模板生成素材包或初稿。
- 盟史：可用。可通过 `/史`、人物/事件研究档案、自动卡片和 raw 原文进入研究。
- 参政议政：可用。可通过 `/信` 和参政议政素材主题库归集问题、依据和建议。
- 统计：可用。可通过 `/数` 查看账号、年份、体裁、主题和最近样本分布。
- 核验：基础可用。`/核` 已能拦截种子黑名单、口径库错误变体、来源缺失和部分史实风险；高风险公文仍必须人工终审。

## 继续完善方向

1. 扩充口径库和黑名单，尤其是 80 周年、建盟日期、核心人物职务和上海民盟机构表述。
2. 对分类优先校订清单进行人工复核，减少参政议政、主题教育、组织建设之间的交叉误判。
3. 继续补核心人物、事件、机构和地点研究档案，形成更稳定的盟史研究底座。
4. Drive 工作资料继续保持外部参考层，导入前逐条判断公开属性和使用边界。
"""


def command_verify(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    created_at = now_iso()
    body = verify_report_markdown(root, created_at)
    if args.save:
        path = report_dir(root) / "盟参系统可用性验收报告.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        append_wiki_log(root, f"生成盟参系统可用性验收报告：{path.relative_to(root)}")
        log_operation(root, "verify", "ok", "report saved", {"output": str(path)})
        print(path)
    else:
        print(body)
    return 0


def source_title_list(rows: list[sqlite3.Row], limit: int = 12) -> str:
    lines = []
    seen: set[int] = set()
    for row in rows:
        article_id = int(row["article_id"])
        if article_id in seen:
            continue
        seen.add(article_id)
        lines.append(f"- {row['published_at'] or '日期不详'}｜{row['account'] or ''}｜《{row['title']}》")
        if len(lines) >= limit:
            break
    return "\n".join(lines) or "- 暂无"


def timeline_candidates(rows: list[sqlite3.Row], limit: int = 10) -> str:
    items = []
    seen = set()
    for row in rows:
        date = row["published_at"] or ""
        title = row["title"] or ""
        if not date or (date, title) in seen:
            continue
        seen.add((date, title))
        items.append(f"- {date}：可核查《{title}》")
        if len(items) >= limit:
            break
    return "\n".join(items) or "- 待从原文补充明确时间线。"


def entity_candidates(rows: list[sqlite3.Row], query: str, limit: int = 16) -> str:
    text = " ".join([query] + [row["title"] or "" for row in rows] + [clean_snippet(row["snippet"], 160) for row in rows])
    candidates = re.findall(r"[\u4e00-\u9fff]{2,12}(?:民盟|委员会|市委|中央|省委|区委|支部|会议|大会|活动|基地|纪念馆|档案馆|口号|事件|先生|同志)?", text)
    stop = {"本地资料", "研究助手", "相关来源", "当前材料", "来源文章", "日期不详", "微信公众号"}
    out = []
    seen = set()
    for item in candidates:
        item = item.strip()
        if len(item) < 2 or item in stop or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= limit:
            break
    return "、".join(out) if out else "待从原文补充。"


def infer_assistant_mode(query: str, mode: str) -> str:
    if mode != "auto":
        return mode
    if any(k in query for k in ["写", "文章", "微信", "报道", "采访", "标题", "成稿"]):
        return "writing"
    if any(k in query for k in ["历史", "盟史", "先贤", "事件", "五一口号", "旧政协", "李闻", "特园"]):
        return "history"
    if any(k in query for k in ["参政", "提案", "社情民意", "建言", "议政"]):
        return "policy"
    if any(k in query for k in ["主题教育", "参政为公", "实干为民"]):
        return "theme"
    return "research"


def assistant_mode_name(mode: str) -> str:
    return {
        "research": "综合研究",
        "history": "盟史研究",
        "writing": "微信公众号写作",
        "policy": "参政议政研究",
        "theme": "主题教育研究",
    }.get(mode, mode)


def assistant_body(query: str, mode: str, rows: list[sqlite3.Row]) -> str:
    if not rows:
        return f"""# 民盟研究助手：{query}

## 初步判断

未检索到足够可靠的本地来源。建议扩大关键词，或补充原始材料后重新提问。

## 下一步

- 换用更短的关键词。
- 同时尝试全国民盟史词和上海地方实践词。
- 若是写稿任务，先补充时间、地点、人物、职务、活动流程和讲话要点。
"""

    mode_name = assistant_mode_name(mode)
    source_rows = "\n".join(row_source_md(row, idx) for idx, row in enumerate(rows, 1))
    excerpts = "\n".join(f"{idx}. {clean_snippet(row['snippet'], 300)} [S{idx}]" for idx, row in enumerate(rows, 1))
    cited_count = len({int(row["article_id"]) for row in rows})
    entities = entity_candidates(rows, query)
    timeline = timeline_candidates(rows)

    if mode == "writing":
        task_block = """## 写作处理

- 先判断稿件类型：活动新闻、会议报道、人物采访、参政议政、主题教育或文史纪念。
- 正式成稿前，必须补齐时间、地点、人物职务、主办承办单位、活动流程、讲话要点、数字和专有名词。
- 文史类写作应先做史实核验，再转化为上海民盟微信公众号体例。
"""
    elif mode == "history":
        task_block = """## 史学处理

- 先区分全国民盟史主线、上海民盟地方史线索和公众号纪念性表述。
- 涉及年份、会议、人物身份、组织名称和地点时，以 raw 原文为核验入口。
- 当前回答只形成研究线索，不把纪念性语言直接当作史实结论。
"""
    elif mode == "policy":
        task_block = """## 参政议政处理

- 重点抽取问题意识、调研对象、建议方向、履职成果和可复用表述。
- 可进一步整理为提案线索、社情民意信息线索、调研报告素材和微信报道素材。
"""
    elif mode == "theme":
        task_block = """## 主题教育处理

- 重点抽取学习安排、组织方式、基层落实、履职结合和典型做法。
- 写作时应避免空泛表态，优先使用具体活动、具体机制和具体成效支撑。
"""
    else:
        task_block = """## 研究处理

- 先用来源建立事实链，再判断可写作、可研究、可继续核验的部分。
- 若需要长篇报告，应继续扩展检索词并按人物、事件、组织、地点、时间线拆分。
"""

    return f"""# 民盟研究助手：{query}

## 初步判断

本次按“{mode_name}”处理。知识库命中 {len(rows)} 条片段，覆盖 {cited_count} 篇文章。当前材料可以作为研究和写作的入口；正式引用或形成结论前，应打开 raw 原文核对上下文。

{task_block}

## 主要来源文章

{source_title_list(rows)}

## 证据摘录

{excerpts}

## 时间线线索

{timeline}

## 人物、组织、地点和主题词线索

{entities}

## 可转化成果

- 研究说明：围绕问题、史实链条、来源分歧和待核实点展开。
- 微信文章：围绕标题、导语、事实主体、意义落点和下一步工作展开。
- 资料卡：拆为人物卡、事件卡、地点卡、组织卡后继续补充。
- 参政议政素材：提取问题、建议、调研对象、政策表述和可引用案例。

## 待核实清单

- 来源片段是否来自同一篇文章的不同段落。
- 人名、职务、组织名称、会议名称、地点和日期是否完整准确。
- 如果涉及历史判断，是否有全国民盟史与上海地方史的交叉证据。
- 如果用于发稿，是否符合上海民盟微信公众号近期体例。

## 来源表

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{source_rows}
"""


def brief_body(query: str, rows: list[sqlite3.Row]) -> str:
    source_rows = "\n".join(row_source_md(row, idx) for idx, row in enumerate(rows, 1))
    return f"""# 民盟简报素材：{query}

## 初步判断

- 本次检索到 {len(rows)} 条片段，覆盖 {len({int(row['article_id']) for row in rows}) if rows else 0} 篇来源文章。
- 本页用于快速形成领导参阅、工作简报或材料准备提纲，不是最终定稿。
- 所有事实性表述必须回到 raw 原文核验；无法确认的判断标 `[待核]`。

## 三点摘要

- 主题相关材料已在本地公众号语料中形成可查线索，可先作为公开报道层依据。
- 可重点提炼背景、主要做法、典型案例、问题线索和下一步建议。
- 正式使用前需补充内部口径、最新数据、责任部门和权威来源。

## 可用素材

### 来源文章

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{source_rows}

### 证据摘录

{cited_excerpts(rows, 8)}

### 简报结构建议

- 背景：交代主题来源、工作场景或现实需求。
- 进展：列出已有做法、活动、调研或履职成果。
- 问题：从来源中提取可被证据支撑的问题，不做无来源推断。
- 建议：围绕机制、协同、资源、人才、宣传或后续调研提出方向性建议。[待核]

## 风险提示

- 简报类材料容易把公开报道拔高为工作结论，必须人工复核。
- 涉及领导、机构、会议、数据、政策和历史评价时，以权威口径为准。
"""


def assistant_home_body() -> str:
    return f"""# 民盟研究助手

本页是本地知识库的研究助手入口。它依托已导入的上海民盟、中国民主同盟、群言杂志微信公众号文章，服务于盟史研究、上海民盟地方史整理、人物事件地点卡片、参政议政素材和微信公众号写作。

## 当前底座

- 原始文章：本地数据库中的微信公众号文章。
- 检索片段：按文章切分后建立全文索引。
- 研究页面：自动编译的专题页、人物卡、事件卡、地点卡和写作规范。
- 来源原则：所有研究结论先回到 raw 原文核对。

## 常用命令

```bash
kb staff draft "主题教育会议报道" --material "粘贴材料"
kb staff history "沈钧儒"
kb staff info "科技创新人才"
kb staff topic "午间盟史课堂：费孝通与江村"
kb staff stats "2025 参政议政"
kb staff check --file ~/Desktop/draft.txt
kb assistant "五一口号在民盟史上的意义" --mode history --save
kb corpus-style
kb external-sources --save
kb verify --save
```

## 盟参模式

- `/稿`：微信公众号文稿素材和初稿入口，优先调用上海民盟写作风格规则卡、精选样本和分体裁模板。
- `/史`：盟史、人物、事件、机构、地点研究入口，优先调用核心研究档案、自动卡片和 raw 原文。
- `/信`：统战信息、社情民意、参政议政素材入口，按“问题发现、调研依据、对策建议、履职价值”组织。
- `/题`：选题查重和差异化角度建议。
- `/数`：账号、年份、体裁、主题热度和最近样本统计。
- `/核`：文稿口径、黑名单、错别字、史实和引用风险预审。

## 工作规则

1. 先检索本地来源，不凭记忆作史实判断。
2. 先输出证据和待核实点，再进入写作。
3. 历史研究区分全国民盟史主线与上海地方史实践。
4. 微信写作区分活动新闻、会议报道、人物采访、参政议政、主题教育、文史纪念等类型。
5. 正式发稿前必须核对人名、职务、组织、日期、地点和数字。

## 已有核心页面

- [[上海民盟微信公众号写作风格规则卡]]
- [[上海民盟微信公众号精选写作样本]]
- [[上海民盟微信公众号分体裁写作模板]]
- [[微信公众号参政议政素材主题库]]
- [[微信公众号文史盟史研究入口清单]]
- [[Google Drive外部参考层状态]]
- [[Google Drive工作资料接入清单]]
- [[盟参系统可用性验收报告]]
- [[民盟盟史资源总览]]
- [[上海民盟盟史独立专题]]
- [[民盟中央与上海民盟文史知识合并底座]]
- [[上海民盟微信公众号写作规范]]
- [[人物类采访写法]]
- [[参政议政写作素材]]
- [[主题教育基层落实机制]]

## 后续人工维护

- 在每个自动生成页面的 HUMAN-NOTES 区补充人工判断。
- 对重要人物、事件、地点逐步加入权威来源和外部档案。
- 对可发稿内容另存为文稿素材，不直接覆盖研究页面。
"""


def command_assistant(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    if args.install:
        path = write_wiki_page(root, "民盟研究助手首页", "assistant", assistant_home_body(), [])
        append_wiki_log(root, f"安装民盟研究助手首页：{path.relative_to(root)}")
        log_operation(root, "assistant", "ok", "installed assistant home", {"path": str(path)})
        print(f"Installed: {path}")
        return 0
    if not args.query:
        print("assistant requires a query, or use --install", file=sys.stderr)
        return 2
    mode = infer_assistant_mode(args.query, args.mode)
    rows = search_rows(root, args.query, args.top_k)
    body = assistant_body(args.query, mode, rows)
    if args.save:
        title = f"{assistant_mode_name(mode)}：{args.query}"
        path = write_wiki_page(root, title, "assistant", body, rows)
        append_wiki_log(root, f"研究助手生成页面：{path.relative_to(root)}")
        print(f"Saved: {path}")
    print(body)
    if args.sync_vault:
        sync_args = argparse.Namespace(project_root=args.project_root, vault=args.sync_vault, dry_run=False)
        command_obsidian_sync(sync_args)
    log_operation(root, "assistant", "ok", f"{len(rows)} sources", {"query": args.query, "mode": mode})
    return 0


def command_search(args: argparse.Namespace) -> int:
    return search_ask_commands.command_search(args, project_root_from_args, log_operation, row_authority_label)


def command_ask(args: argparse.Namespace) -> int:
    return search_ask_commands.command_ask(args, project_root_from_args, log_operation, clean_snippet, row_source_line)


CARD_SETS = compile_commands.CARD_SETS
CARD_QUERY_OVERRIDES = compile_commands.CARD_QUERY_OVERRIDES
PRIORITY_CARDS = compile_commands.PRIORITY_CARDS
TOPIC_PACKS = compile_commands.TOPIC_PACKS
RESEARCH_DOSSIER_SETS = compile_commands.RESEARCH_DOSSIER_SETS
AUTHORITY_COVERAGE_TOPICS = compile_commands.AUTHORITY_COVERAGE_TOPICS
PERSON_RESEARCH_THEMES = compile_commands.PERSON_RESEARCH_THEMES
EVENT_RESEARCH_THEMES = compile_commands.EVENT_RESEARCH_THEMES
WRITING_WORKFLOWS = compile_commands.WRITING_WORKFLOWS


def _configure_compile_commands() -> None:
    compile_commands.configure(
        project_root_from_args,
        append_wiki_log,
        log_operation,
        init_db,
        merge_generated_with_fresh_metadata,
        row_source_line,
        clean_snippet,
        row_source_md,
        timeline_candidates,
        entity_candidates,
        source_title_list,
        markdown_table,
        people_hits_for_text,
        unique_source_rows,
        row_authority_label,
    )


def wiki_dir_for_page_type(page_type: str, topic: str = "") -> str:
    return compile_commands.wiki_dir_for_page_type(page_type, topic)


def make_frontmatter(title: str, page_type: str, source_count: int, confidence: str = "medium") -> str:
    _configure_compile_commands()
    return compile_commands.make_frontmatter(title, page_type, source_count, confidence)


def write_wiki_page(root: Path, title: str, page_type: str, body: str, sources: list[sqlite3.Row]) -> Path:
    _configure_compile_commands()
    return compile_commands.write_wiki_page(root, title, page_type, body, sources)


def compile_body(topic: str, page_type: str, rows: list[sqlite3.Row]) -> str:
    _configure_compile_commands()
    return compile_commands.compile_body(topic, page_type, rows)


def topic_pack_body(topic: str, mode: str, rows: list[sqlite3.Row]) -> str:
    _configure_compile_commands()
    return compile_commands.topic_pack_body(topic, mode, rows)


def writing_workflow_body(name: str, spec: dict[str, list[str] | str], rows: list[sqlite3.Row]) -> str:
    _configure_compile_commands()
    return compile_commands.writing_workflow_body(name, spec, rows)


def writing_workflow_section_text(workflow: str, section: str) -> str:
    return compile_commands.writing_workflow_section_text(workflow, section)


def command_compile(args: argparse.Namespace) -> int:
    _configure_compile_commands()
    return compile_commands.command_compile(args)


def update_frontmatter_fields(path: Path, updates: dict[str, str]) -> None:
    return compile_commands.update_frontmatter_fields(path, updates)


def apply_priority_card_status(root: Path) -> int:
    _configure_compile_commands()
    return compile_commands.apply_priority_card_status(root)


def card_body(name: str, page_type: str, rows: list[sqlite3.Row]) -> str:
    _configure_compile_commands()
    return compile_commands.card_body(name, page_type, rows)


def dossier_dir(root: Path) -> Path:
    return compile_commands.dossier_dir(root)


def event_dossier_dir(root: Path) -> Path:
    return compile_commands.event_dossier_dir(root)


def row_text(row: sqlite3.Row) -> str:
    return compile_commands.row_text(row)


def rows_matching_keywords(rows: list[sqlite3.Row], keywords: list[str]) -> list[sqlite3.Row]:
    _configure_compile_commands()
    return compile_commands.rows_matching_keywords(rows, keywords)


def dossier_theme_sections(sources: list[sqlite3.Row], themes: dict[str, list[str]]) -> tuple[list[list[str]], list[str]]:
    _configure_compile_commands()
    return compile_commands.dossier_theme_sections(sources, themes)


def person_research_dossier_body(name: str, rows: list[sqlite3.Row], created_at: str) -> str:
    _configure_compile_commands()
    return compile_commands.person_research_dossier_body(name, rows, created_at)


def event_research_dossier_body(name: str, rows: list[sqlite3.Row], created_at: str) -> str:
    _configure_compile_commands()
    return compile_commands.event_research_dossier_body(name, rows, created_at)


def write_person_research_dossier(root: Path, name: str, rows: list[sqlite3.Row], created_at: str) -> Path:
    _configure_compile_commands()
    return compile_commands.write_person_research_dossier(root, name, rows, created_at)


def write_event_research_dossier(root: Path, name: str, rows: list[sqlite3.Row], created_at: str) -> Path:
    _configure_compile_commands()
    return compile_commands.write_event_research_dossier(root, name, rows, created_at)


def person_dossier_rows(root: Path, name: str, top_k: int) -> list[sqlite3.Row]:
    _configure_compile_commands()
    return compile_commands.person_dossier_rows(root, name, top_k)


def event_dossier_rows(root: Path, name: str, top_k: int) -> list[sqlite3.Row]:
    _configure_compile_commands()
    return compile_commands.event_dossier_rows(root, name, top_k)


def authority_level_counts(rows: list[sqlite3.Row]) -> Counter:
    _configure_compile_commands()
    return compile_commands.authority_level_counts(rows)


def authority_coverage_status(counts: Counter) -> str:
    return compile_commands.authority_coverage_status(counts)


def authority_coverage_action(counts: Counter) -> str:
    return compile_commands.authority_coverage_action(counts)


def authority_coverage_records(root: Path, top_k: int) -> list[dict]:
    _configure_compile_commands()
    return compile_commands.authority_coverage_records(root, top_k)


def authority_coverage_markdown(records: list[dict], created_at: str) -> str:
    _configure_compile_commands()
    return compile_commands.authority_coverage_markdown(records, created_at)


def research_dossier_index_body(created: list[Path], created_at: str) -> str:
    _configure_compile_commands()
    return compile_commands.research_dossier_index_body(created, created_at)


def event_research_dossier_index_body(created: list[Path], created_at: str) -> str:
    _configure_compile_commands()
    return compile_commands.event_research_dossier_index_body(created, created_at)


def command_build_research_dossiers(args: argparse.Namespace) -> int:
    _configure_compile_commands()
    return compile_commands.command_build_research_dossiers(args)


def command_authority_coverage(args: argparse.Namespace) -> int:
    _configure_compile_commands()
    return compile_commands.command_authority_coverage(args)


def command_build_cards(args: argparse.Namespace) -> int:
    _configure_compile_commands()
    return compile_commands.command_build_cards(args)


def command_curate_cards(args: argparse.Namespace) -> int:
    _configure_compile_commands()
    return compile_commands.command_curate_cards(args)


def command_build_packs(args: argparse.Namespace) -> int:
    _configure_compile_commands()
    return compile_commands.command_build_packs(args)


def command_build_writing_workflows(args: argparse.Namespace) -> int:
    _configure_compile_commands()
    return compile_commands.command_build_writing_workflows(args)


def preview_import(root: Path, input_dir: Path, limit: int) -> tuple[int, int, int]:
    conn = connect_db(root)
    new_count = 0
    duplicate_count = 0
    failed = 0
    try:
        for path in list(iter_input_files(input_dir))[:limit]:
            try:
                doc = extract_doc(path, input_dir)
                content_hash = sha256_text(doc.text)
                exists = conn.execute("SELECT id FROM articles WHERE content_hash = ?", (content_hash,)).fetchone()
                if exists:
                    duplicate_count += 1
                else:
                    new_count += 1
            except Exception:
                failed += 1
    finally:
        conn.close()
    return new_count, duplicate_count, failed


def command_refresh(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    ensure_dirs(root)
    init_db(root)
    input_dir = Path(args.input).expanduser().resolve()
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 2
    limit = args.limit
    new_count, duplicate_count, failed = preview_import(root, input_dir, limit)
    print(f"Input: {input_dir}")
    print(f"Dry run: {args.dry_run}")
    print(f"New articles: {new_count}")
    print(f"Duplicate articles: {duplicate_count}")
    print(f"Failed preview: {failed}")
    if args.dry_run:
        print("Would import, rebuild indexes, refresh cards, corpus reports, writing/style/history/policy materials, research dossiers, external-source status, guardrails report, verification report, Obsidian sync, and Obsidian status.")
        log_operation(root, "refresh", "dry-run", f"new={new_count} duplicate={duplicate_count} failed={failed}")
        return 0

    conn = connect_db(root)
    before_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    import_args = argparse.Namespace(project_root=args.project_root, input=str(input_dir), limit=limit, dry_run=False)
    import_code = command_import(import_args)
    rows, indexed = rebuild_fts(root)
    _, vector_indexed = rebuild_vectors(root)
    build_args = argparse.Namespace(project_root=args.project_root, set="all", limit=0, top_k=args.top_k)
    command_build_cards(build_args)
    command_build_packs(argparse.Namespace(project_root=args.project_root, top_k=args.top_k))
    command_build_writing_workflows(argparse.Namespace(project_root=args.project_root, top_k=args.top_k))
    command_corpus(argparse.Namespace(project_root=args.project_root))
    command_corpus_audit(
        argparse.Namespace(
            project_root=args.project_root,
            per_type=20,
            low_confidence=80,
            other=80,
            priority=100,
        )
    )
    command_corpus_style(argparse.Namespace(project_root=args.project_root))
    command_build_research_dossiers(argparse.Namespace(project_root=args.project_root, set="core-people", limit=0, top_k=args.top_k))
    command_build_research_dossiers(argparse.Namespace(project_root=args.project_root, set="core-events", limit=0, top_k=args.top_k))
    command_external_sources(argparse.Namespace(project_root=args.project_root, save=True))
    command_guardrails(argparse.Namespace(project_root=args.project_root, save=True))
    command_assistant(argparse.Namespace(project_root=args.project_root, query=None, mode="auto", top_k=args.top_k, save=False, install=True, sync_vault=None))
    command_verify(argparse.Namespace(project_root=args.project_root, save=True))
    priority_count = apply_priority_card_status(root)
    sync_count = 0
    if args.vault:
        sync_args = argparse.Namespace(project_root=args.project_root, vault=args.vault, dry_run=False)
        sync_code = command_obsidian_sync(sync_args)
        sync_count = 0 if sync_code else len(list((Path(args.vault).expanduser()).rglob("*.md")))
        command_obsidian_status(argparse.Namespace(project_root=args.project_root, vault=args.vault, save=True))
        command_verify(argparse.Namespace(project_root=args.project_root, save=True))
        command_obsidian_sync(sync_args)
    conn = connect_db(root)
    after_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    message = f"articles {before_articles}->{after_articles}; indexed={indexed}; vectors={vector_indexed}; corpus=refreshed; dossiers=refreshed; verify=refreshed; priority={priority_count}"
    append_wiki_log(root, f"一键刷新知识库：{message}")
    log_operation(
        root,
        "refresh",
        "ok" if import_code == 0 else "partial",
        message,
        {"input": str(input_dir), "new_preview": new_count, "duplicates_preview": duplicate_count, "vault_files": sync_count},
    )
    print(f"Refresh complete: {message}")
    return import_code


def page_path_for_export(root: Path, title: str | None, path_value: str | None) -> Path:
    return export_commands.page_path_for_export(root, title, path_value)


def markdown_to_plain_text(markdown: str) -> str:
    return export_commands.markdown_to_plain_text(markdown)


def write_docx(path: Path, title: str, markdown: str) -> None:
    export_commands.write_docx(path, title, markdown)


def write_pdf(path: Path, title: str, markdown: str) -> None:
    export_commands.write_pdf(path, title, markdown)


def command_export(args: argparse.Namespace) -> int:
    return export_commands.command_export(args, project_root_from_args, log_operation)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kb")
    parser.add_argument("--project-root", default=None, help="Project root, default: KB_PROJECT_ROOT or package root")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.set_defaults(func=command_init)

    p = sub.add_parser("scan")
    p.add_argument("--input", required=True)
    p.set_defaults(func=command_scan)

    p = sub.add_parser("import")
    p.add_argument("--input", required=True)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--source-id", default=None, help="来源登记 ID，如 AUTH-001")
    p.add_argument("--authority-level", choices=["L1", "L2", "L3", "L4"], default="L4")
    p.add_argument("--source-tier", default=None, help="来源层；默认等于 authority-level")
    p.add_argument("--is-citable", action="store_true", help="标记为可直接引用来源")
    p.set_defaults(func=command_import)

    p = sub.add_parser("ingest-file", help="导入已手动保存的公开网页/文本，并补权威来源元数据")
    p.add_argument("file")
    p.add_argument("--source-url", default=None, help="原始公开 URL")
    p.add_argument("--source-id", default=None, help="来源登记 ID，如 AUTH-001")
    p.add_argument("--authority-level", choices=["L1", "L2", "L3", "L4"], default="L3")
    p.add_argument("--source-tier", default=None, help="来源层；默认等于 authority-level")
    p.add_argument("--is-citable", action="store_true", help="标记为可直接引用来源")
    p.add_argument("--account", default=None, help="覆盖来源名称")
    p.add_argument("--title", default=None, help="覆盖标题")
    p.add_argument("--published-at", default=None, help="覆盖发布日期 YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=command_ingest_file)

    p = sub.add_parser("ingest-url", help="抓取公开 URL 并按权威来源级别入库")
    p.add_argument("url")
    p.add_argument("--source-id", default=None, help="来源登记 ID，如 AUTH-001")
    p.add_argument("--authority-level", choices=["L1", "L2", "L3", "L4"], default="L3")
    p.add_argument("--source-tier", default=None, help="来源层；默认等于 authority-level")
    p.add_argument("--is-citable", action="store_true", help="标记为可直接引用来源")
    p.add_argument("--account", default=None, help="覆盖来源名称")
    p.add_argument("--title", default=None, help="覆盖标题")
    p.add_argument("--published-at", default=None, help="覆盖发布日期 YYYY-MM-DD")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--insecure", action="store_true", help="显式允许跳过 TLS 证书校验，仅用于已确认的公开旧站页面")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=command_ingest_url)

    p = sub.add_parser("check")
    p.add_argument("text", nargs="*", help="可选：直接粘贴待核文稿；不填时检查项目状态")
    p.add_argument("--file", default=None, help="可选：从文本文件读取待核文稿")
    p.set_defaults(func=command_check)

    p = sub.add_parser("obsidian-sync")
    p.add_argument("--vault", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=command_obsidian_sync)

    p = sub.add_parser("obsidian-status")
    p.add_argument("--vault", default=DEFAULT_OBSIDIAN_VAULT)
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=command_obsidian_status)

    p = sub.add_parser("log")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=command_log)

    p = sub.add_parser("index")
    p.set_defaults(func=command_index)

    p = sub.add_parser("refresh")
    p.add_argument("--input", default="~/Downloads/微信公众号")
    p.add_argument("--vault", default=DEFAULT_OBSIDIAN_VAULT)
    p.add_argument("--limit", type=int, default=999999)
    p.add_argument("--top-k", type=int, default=15)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=command_refresh)

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=20)
    p.set_defaults(func=command_search)

    p = sub.add_parser("ask")
    p.add_argument("query")
    p.add_argument("--top-k", type=int, default=10)
    p.set_defaults(func=command_ask)

    p = sub.add_parser("assistant")
    p.add_argument("query", nargs="?")
    p.add_argument("--mode", choices=["auto", "research", "history", "writing", "policy", "theme"], default="auto")
    p.add_argument("--top-k", type=int, default=15)
    p.add_argument("--save", action="store_true")
    p.add_argument("--install", action="store_true")
    p.add_argument("--sync-vault", default=None)
    p.set_defaults(func=command_assistant)

    p = sub.add_parser("staff", help="盟参首席参谋入口：/稿 /史 /信 /题 /数 /核")
    staff_sub = p.add_subparsers(dest="staff_command", required=True)

    p_staff = staff_sub.add_parser("draft", help="/稿：文稿素材包")
    p_staff.add_argument("topic")
    p_staff.add_argument("--material", nargs="*", default=None, help="粘贴活动/会议/人物材料，生成公众号初稿")
    p_staff.add_argument("--file", default=None, help="从文本文件读取材料，生成公众号初稿")
    p_staff.add_argument("--top-k", type=int, default=12)
    p_staff.add_argument("--save", action="store_true")
    p_staff.set_defaults(func=command_staff)

    p_staff = staff_sub.add_parser("history", help="/史：史实卡片和研究入口")
    p_staff.add_argument("topic")
    p_staff.add_argument("--top-k", type=int, default=12)
    p_staff.add_argument("--save", action="store_true")
    p_staff.set_defaults(func=command_staff)

    p_staff = staff_sub.add_parser("topic", help="/题：选题查重")
    p_staff.add_argument("topic")
    p_staff.add_argument("--top-k", type=int, default=20)
    p_staff.add_argument("--save", action="store_true")
    p_staff.set_defaults(func=command_staff)

    p_staff = staff_sub.add_parser("info", help="/信：统战信息/参政议政素材包")
    p_staff.add_argument("topic")
    p_staff.add_argument("--top-k", type=int, default=12)
    p_staff.add_argument("--save", action="store_true")
    p_staff.set_defaults(func=command_staff)

    p_staff = staff_sub.add_parser("stats", help="/数：语料统计和选题分布")
    p_staff.add_argument("topic")
    p_staff.add_argument("--top-k", type=int, default=12)
    p_staff.add_argument("--save", action="store_true")
    p_staff.set_defaults(func=command_staff)

    p_staff = staff_sub.add_parser("check", help="/核：文稿口径和史实风险预审")
    p_staff.add_argument("text", nargs="*")
    p_staff.add_argument("--file", default=None)
    p_staff.add_argument("--top-k", type=int, default=8)
    p_staff.add_argument("--save", action="store_true")
    p_staff.set_defaults(func=command_staff)

    p = sub.add_parser("corpus", help="生成微信公众号语料库体检、分类标签和样本库")
    p.set_defaults(func=command_corpus)

    p = sub.add_parser("corpus-audit", help="生成微信公众号文章分类人工抽检表")
    p.add_argument("--per-type", type=int, default=20)
    p.add_argument("--low-confidence", type=int, default=80)
    p.add_argument("--other", type=int, default=80)
    p.add_argument("--priority", type=int, default=100)
    p.set_defaults(func=command_corpus_audit)

    p = sub.add_parser("corpus-apply-reviews", help="应用微信公众号分类人工校订结果")
    p.add_argument("--save", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=command_corpus_apply_reviews)

    p = sub.add_parser("corpus-style", help="生成上海民盟写作模板和文史盟史研究入口")
    p.set_defaults(func=command_corpus_style)

    p = sub.add_parser("external-sources", help="查看 Google Drive 外部参考层状态")
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=command_external_sources)

    p = sub.add_parser("pro-sources", help="生成专业多党合作语料库来源入库任务")
    p.add_argument("--priority", default="P0")
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=command_pro_sources)

    p = sub.add_parser("sources", help="生成权威公开资料来源分级体检")
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=command_sources)

    p = sub.add_parser("source-urls", help="生成第一批权威网页 URL 入库候选队列")
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=command_source_urls)

    p = sub.add_parser("guardrails", help="生成口径风险清单")
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=command_guardrails)

    p = sub.add_parser("verify", help="生成盟参系统可用性验收报告")
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=command_verify)

    p = sub.add_parser("compile")
    p.add_argument("--topic", default=None)
    p.add_argument("--query", default=None)
    p.add_argument("--page-type", default="topic")
    p.add_argument("--top-k", type=int, default=12)
    p.set_defaults(func=command_compile)

    p = sub.add_parser("build-cards")
    p.add_argument("--set", choices=["people", "shanghai-history-people", "events", "places", "all"], default="all")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--top-k", type=int, default=8)
    p.set_defaults(func=command_build_cards)

    p = sub.add_parser("build-research-dossiers")
    p.add_argument("--set", choices=sorted(RESEARCH_DOSSIER_SETS), default="core-people")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--top-k", type=int, default=24)
    p.set_defaults(func=command_build_research_dossiers)

    p = sub.add_parser("authority-coverage", help="生成核心人物/事件/制度主题的 L1-L4 权威覆盖仪表盘")
    p.add_argument("--top-k", type=int, default=24)
    p.set_defaults(func=command_authority_coverage)

    p = sub.add_parser("curate-cards")
    p.set_defaults(func=command_curate_cards)

    p = sub.add_parser("build-packs")
    p.add_argument("--top-k", type=int, default=15)
    p.set_defaults(func=command_build_packs)

    p = sub.add_parser("build-writing-workflows")
    p.add_argument("--top-k", type=int, default=15)
    p.set_defaults(func=command_build_writing_workflows)

    p = sub.add_parser("export")
    p.add_argument("--title", default=None)
    p.add_argument("--path", default=None)
    p.add_argument("--format", action="append", choices=["markdown", "docx", "pdf", "all"], default=None)
    p.add_argument("--output-name", default=None)
    p.set_defaults(func=command_export)

    p = sub.add_parser("brief", help="生成领导参阅/工作简报素材包")
    p.add_argument("query", nargs="+")
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=command_brief)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
