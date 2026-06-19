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
from kb.commands import assistant as assistant_commands
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


def _configure_assistant_commands() -> None:
    assistant_commands.configure(
        project_root_from_args,
        report_dir,
        append_wiki_log,
        log_operation,
        markdown_table,
        external_sources_report_markdown,
        cited_excerpts,
        write_wiki_page,
        load_article_labels,
        load_external_inventory,
        obsidian_sync_status,
        row_source_md,
        clean_snippet,
        command_obsidian_sync,
    )


def command_external_sources(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.command_external_sources(*args, **kwargs)

def guardrails_report_markdown(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.guardrails_report_markdown(*args, **kwargs)

def command_guardrails(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.command_guardrails(*args, **kwargs)

def command_brief(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.command_brief(*args, **kwargs)

def count_db_rows(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.count_db_rows(*args, **kwargs)

def status_label(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.status_label(*args, **kwargs)

def verify_report_markdown(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.verify_report_markdown(*args, **kwargs)

def command_verify(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.command_verify(*args, **kwargs)

def source_title_list(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.source_title_list(*args, **kwargs)

def timeline_candidates(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.timeline_candidates(*args, **kwargs)

def entity_candidates(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.entity_candidates(*args, **kwargs)

def infer_assistant_mode(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.infer_assistant_mode(*args, **kwargs)

def assistant_mode_name(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.assistant_mode_name(*args, **kwargs)

def assistant_body(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.assistant_body(*args, **kwargs)

def brief_body(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.brief_body(*args, **kwargs)

def assistant_home_body(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.assistant_home_body(*args, **kwargs)

def command_assistant(*args, **kwargs):
    _configure_assistant_commands()
    return assistant_commands.command_assistant(*args, **kwargs)

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
