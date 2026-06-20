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
from kb.commands import refresh as refresh_commands
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


def _staff(name, *args, **kwargs):
    _configure_staff_commands()
    return getattr(staff_commands, name)(*args, **kwargs)

clean_snippet = lambda *a, _n="clean_snippet", **k: _staff(_n, *a, **k)
row_source_line = lambda *a, _n="row_source_line", **k: _staff(_n, *a, **k)
row_authority_label = lambda *a, _n="row_authority_label", **k: _staff(_n, *a, **k)
row_source_md = lambda *a, _n="row_source_md", **k: _staff(_n, *a, **k)
unique_source_rows = lambda *a, _n="unique_source_rows", **k: _staff(_n, *a, **k)
staff_index_dir = lambda *a, _n="staff_index_dir", **k: _staff(_n, *a, **k)
load_jsonl = lambda *a, _n="load_jsonl", **k: _staff(_n, *a, **k)
load_staff_entities = lambda *a, _n="load_staff_entities", **k: _staff(_n, *a, **k)
load_external_inventory = lambda *a, _n="load_external_inventory", **k: _staff(_n, *a, **k)
citation_table = lambda *a, _n="citation_table", **k: _staff(_n, *a, **k)
cited_excerpts = lambda *a, _n="cited_excerpts", **k: _staff(_n, *a, **k)
staff_formulation_lines = lambda *a, _n="staff_formulation_lines", **k: _staff(_n, *a, **k)
staff_entity_lines = lambda *a, _n="staff_entity_lines", **k: _staff(_n, *a, **k)
staff_risk_lines = lambda *a, _n="staff_risk_lines", **k: _staff(_n, *a, **k)
staff_query = lambda *a, _n="staff_query", **k: _staff(_n, *a, **k)
topic_query_variants = lambda *a, _n="topic_query_variants", **k: _staff(_n, *a, **k)
history_query_variants = lambda *a, _n="history_query_variants", **k: _staff(_n, *a, **k)
merge_search_rows = lambda *a, _n="merge_search_rows", **k: _staff(_n, *a, **k)
staff_search_rows = lambda *a, _n="staff_search_rows", **k: _staff(_n, *a, **k)
staff_card_matches = lambda *a, _n="staff_card_matches", **k: _staff(_n, *a, **k)
staff_cards_block = lambda *a, _n="staff_cards_block", **k: _staff(_n, *a, **k)
external_reference_matches = lambda *a, _n="external_reference_matches", **k: _staff(_n, *a, **k)
external_reference_block = lambda *a, _n="external_reference_block", **k: _staff(_n, *a, **k)
external_sources_report_markdown = lambda *a, _n="external_sources_report_markdown", **k: _staff(_n, *a, **k)
research_dossier_matches = lambda *a, _n="research_dossier_matches", **k: _staff(_n, *a, **k)
research_dossier_block = lambda *a, _n="research_dossier_block", **k: _staff(_n, *a, **k)
staff_history_research_route = lambda *a, _n="staff_history_research_route", **k: _staff(_n, *a, **k)
staff_draft_article_type = lambda *a, _n="staff_draft_article_type", **k: _staff(_n, *a, **k)
staff_curated_writing_samples = lambda *a, _n="staff_curated_writing_samples", **k: _staff(_n, *a, **k)
staff_draft_structure_block = lambda *a, _n="staff_draft_structure_block", **k: _staff(_n, *a, **k)
material_points = lambda *a, _n="material_points", **k: _staff(_n, *a, **k)
material_field_hints = lambda *a, _n="material_field_hints", **k: _staff(_n, *a, **k)
title_suggestions = lambda *a, _n="title_suggestions", **k: _staff(_n, *a, **k)
draft_paragraphs_from_material = lambda *a, _n="draft_paragraphs_from_material", **k: _staff(_n, *a, **k)
staff_material_draft_body = lambda *a, _n="staff_material_draft_body", **k: _staff(_n, *a, **k)
staff_draft_body = lambda *a, _n="staff_draft_body", **k: _staff(_n, *a, **k)
staff_history_body = lambda *a, _n="staff_history_body", **k: _staff(_n, *a, **k)
normalized_similarity = lambda *a, _n="normalized_similarity", **k: _staff(_n, *a, **k)
topic_similarity_rows = lambda *a, _n="topic_similarity_rows", **k: _staff(_n, *a, **k)
staff_topic_body = lambda *a, _n="staff_topic_body", **k: _staff(_n, *a, **k)
staff_info_body = lambda *a, _n="staff_info_body", **k: _staff(_n, *a, **k)
staff_stats_matches = lambda *a, _n="staff_stats_matches", **k: _staff(_n, *a, **k)
counter_table = lambda *a, _n="counter_table", **k: _staff(_n, *a, **k)
staff_stats_body = lambda *a, _n="staff_stats_body", **k: _staff(_n, *a, **k)
staff_check_body = lambda *a, _n="staff_check_body", **k: _staff(_n, *a, **k)
command_pro_sources = lambda *a, _n="command_pro_sources", **k: _staff(_n, *a, **k)
command_sources = lambda *a, _n="command_sources", **k: _staff(_n, *a, **k)
command_source_urls = lambda *a, _n="command_source_urls", **k: _staff(_n, *a, **k)
command_staff = lambda *a, _n="command_staff", **k: _staff(_n, *a, **k)

ARTICLE_TYPE_RULES = corpus_commands.ARTICLE_TYPE_RULES
ARTICLE_TYPE_NAMES = corpus_commands.ARTICLE_TYPE_NAMES
WRITING_STYLE_GUIDES = corpus_commands.WRITING_STYLE_GUIDES
TOPIC_KEYWORDS = corpus_commands.TOPIC_KEYWORDS


def corpus_dir(root: Path) -> Path:
    return corpus_commands.corpus_dir(root)

def report_dir(root: Path) -> Path:
    return root / "wiki" / "研究助手"

def write_jsonl(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in items) + "\n", encoding="utf-8")

def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * len(rows[0])) + " |"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows[1:])
    return "\n".join(lines)

def _corpus(name, *args, **kwargs):
    return getattr(corpus_commands, name)(*args, **kwargs)

article_year = lambda *a, _n="article_year", **k: _corpus(_n, *a, **k)
year_at_least = lambda *a, _n="year_at_least", **k: _corpus(_n, *a, **k)
classify_article = lambda *a, _n="classify_article", **k: _corpus(_n, *a, **k)
build_article_label = lambda *a, _n="build_article_label", **k: _corpus(_n, *a, **k)
load_article_labels = lambda *a, _n="load_article_labels", **k: _corpus(_n, *a, **k)
corpus_dashboard_markdown = lambda *a, _n="corpus_dashboard_markdown", **k: _corpus(_n, *a, **k)
apply_review_decisions_to_labels = lambda *a, _n="apply_review_decisions_to_labels", **k: _corpus(_n, *a, **k)
collect_review_decisions = lambda *a, _n="collect_review_decisions", **k: _corpus(_n, *a, **k)
corpus_audit_markdown = lambda *a, _n="corpus_audit_markdown", **k: _corpus(_n, *a, **k)
corpus_review_apply_markdown = lambda *a, _n="corpus_review_apply_markdown", **k: _corpus(_n, *a, **k)
corpus_quality_diagnostic_markdown = lambda *a, _n="corpus_quality_diagnostic_markdown", **k: _corpus(_n, *a, **k)
corpus_priority_review_markdown = lambda *a, _n="corpus_priority_review_markdown", **k: _corpus(_n, *a, **k)
corpus_priority_review_rows = lambda *a, _n="corpus_priority_review_rows", **k: _corpus(_n, *a, **k)
corpus_review_rows = lambda *a, _n="corpus_review_rows", **k: _corpus(_n, *a, **k)
curated_writing_samples_markdown = lambda *a, _n="curated_writing_samples_markdown", **k: _corpus(_n, *a, **k)
history_research_entry_markdown = lambda *a, _n="history_research_entry_markdown", **k: _corpus(_n, *a, **k)
policy_advice_material_index_markdown = lambda *a, _n="policy_advice_material_index_markdown", **k: _corpus(_n, *a, **k)
people_hits_for_text = lambda *a, _n="people_hits_for_text", **k: _corpus(_n, *a, **k)
shanghai_style_rule_card_markdown = lambda *a, _n="shanghai_style_rule_card_markdown", **k: _corpus(_n, *a, **k)
write_corpus_review_csv = lambda *a, _n="write_corpus_review_csv", **k: _corpus(_n, *a, **k)
writing_sample_score = lambda *a, _n="writing_sample_score", **k: _corpus(_n, *a, **k)
writing_style_templates_markdown = lambda *a, _n="writing_style_templates_markdown", **k: _corpus(_n, *a, **k)

def _configure_corpus_commands() -> None:
    corpus_commands.configure(project_root_from_args, append_wiki_log, log_operation)


command_corpus_audit = lambda args, _n="command_corpus_audit": (_configure_corpus_commands(), getattr(corpus_commands, _n)(args))[1]


command_corpus_apply_reviews = lambda args, _n="command_corpus_apply_reviews": (_configure_corpus_commands(), getattr(corpus_commands, _n)(args))[1]


command_corpus = lambda args, _n="command_corpus": (_configure_corpus_commands(), getattr(corpus_commands, _n)(args))[1]


command_corpus_style = lambda args, _n="command_corpus_style": (_configure_corpus_commands(), getattr(corpus_commands, _n)(args))[1]


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


def _assistant(name, *args, **kwargs):
    _configure_assistant_commands()
    return getattr(assistant_commands, name)(*args, **kwargs)

command_external_sources = lambda *a, _n="command_external_sources", **k: _assistant(_n, *a, **k)
guardrails_report_markdown = lambda *a, _n="guardrails_report_markdown", **k: _assistant(_n, *a, **k)
command_guardrails = lambda *a, _n="command_guardrails", **k: _assistant(_n, *a, **k)
command_brief = lambda *a, _n="command_brief", **k: _assistant(_n, *a, **k)
count_db_rows = lambda *a, _n="count_db_rows", **k: _assistant(_n, *a, **k)
status_label = lambda *a, _n="status_label", **k: _assistant(_n, *a, **k)
verify_report_markdown = lambda *a, _n="verify_report_markdown", **k: _assistant(_n, *a, **k)
command_verify = lambda *a, _n="command_verify", **k: _assistant(_n, *a, **k)
source_title_list = lambda *a, _n="source_title_list", **k: _assistant(_n, *a, **k)
timeline_candidates = lambda *a, _n="timeline_candidates", **k: _assistant(_n, *a, **k)
entity_candidates = lambda *a, _n="entity_candidates", **k: _assistant(_n, *a, **k)
infer_assistant_mode = lambda *a, _n="infer_assistant_mode", **k: _assistant(_n, *a, **k)
assistant_mode_name = lambda *a, _n="assistant_mode_name", **k: _assistant(_n, *a, **k)
assistant_body = lambda *a, _n="assistant_body", **k: _assistant(_n, *a, **k)
brief_body = lambda *a, _n="brief_body", **k: _assistant(_n, *a, **k)
assistant_home_body = lambda *a, _n="assistant_home_body", **k: _assistant(_n, *a, **k)
command_assistant = lambda *a, _n="command_assistant", **k: _assistant(_n, *a, **k)

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


def _compile(name, *args, **kwargs):
    _configure_compile_commands()
    return getattr(compile_commands, name)(*args, **kwargs)

wiki_dir_for_page_type = lambda *a, _n="wiki_dir_for_page_type", **k: _compile(_n, *a, **k)
make_frontmatter = lambda *a, _n="make_frontmatter", **k: _compile(_n, *a, **k)
write_wiki_page = lambda *a, _n="write_wiki_page", **k: _compile(_n, *a, **k)
compile_body = lambda *a, _n="compile_body", **k: _compile(_n, *a, **k)
topic_pack_body = lambda *a, _n="topic_pack_body", **k: _compile(_n, *a, **k)
writing_workflow_body = lambda *a, _n="writing_workflow_body", **k: _compile(_n, *a, **k)
writing_workflow_section_text = lambda *a, _n="writing_workflow_section_text", **k: _compile(_n, *a, **k)
command_compile = lambda *a, _n="command_compile", **k: _compile(_n, *a, **k)
update_frontmatter_fields = lambda *a, _n="update_frontmatter_fields", **k: _compile(_n, *a, **k)
apply_priority_card_status = lambda *a, _n="apply_priority_card_status", **k: _compile(_n, *a, **k)
card_body = lambda *a, _n="card_body", **k: _compile(_n, *a, **k)
dossier_dir = lambda *a, _n="dossier_dir", **k: _compile(_n, *a, **k)
event_dossier_dir = lambda *a, _n="event_dossier_dir", **k: _compile(_n, *a, **k)
row_text = lambda *a, _n="row_text", **k: _compile(_n, *a, **k)
rows_matching_keywords = lambda *a, _n="rows_matching_keywords", **k: _compile(_n, *a, **k)
dossier_theme_sections = lambda *a, _n="dossier_theme_sections", **k: _compile(_n, *a, **k)
person_research_dossier_body = lambda *a, _n="person_research_dossier_body", **k: _compile(_n, *a, **k)
event_research_dossier_body = lambda *a, _n="event_research_dossier_body", **k: _compile(_n, *a, **k)
write_person_research_dossier = lambda *a, _n="write_person_research_dossier", **k: _compile(_n, *a, **k)
write_event_research_dossier = lambda *a, _n="write_event_research_dossier", **k: _compile(_n, *a, **k)
person_dossier_rows = lambda *a, _n="person_dossier_rows", **k: _compile(_n, *a, **k)
event_dossier_rows = lambda *a, _n="event_dossier_rows", **k: _compile(_n, *a, **k)
authority_level_counts = lambda *a, _n="authority_level_counts", **k: _compile(_n, *a, **k)
authority_coverage_status = lambda *a, _n="authority_coverage_status", **k: _compile(_n, *a, **k)
authority_coverage_action = lambda *a, _n="authority_coverage_action", **k: _compile(_n, *a, **k)
authority_coverage_records = lambda *a, _n="authority_coverage_records", **k: _compile(_n, *a, **k)
authority_coverage_markdown = lambda *a, _n="authority_coverage_markdown", **k: _compile(_n, *a, **k)
research_dossier_index_body = lambda *a, _n="research_dossier_index_body", **k: _compile(_n, *a, **k)
event_research_dossier_index_body = lambda *a, _n="event_research_dossier_index_body", **k: _compile(_n, *a, **k)
command_build_research_dossiers = lambda *a, _n="command_build_research_dossiers", **k: _compile(_n, *a, **k)
command_authority_coverage = lambda *a, _n="command_authority_coverage", **k: _compile(_n, *a, **k)
command_build_cards = lambda *a, _n="command_build_cards", **k: _compile(_n, *a, **k)
command_curate_cards = lambda *a, _n="command_curate_cards", **k: _compile(_n, *a, **k)
command_build_packs = lambda *a, _n="command_build_packs", **k: _compile(_n, *a, **k)
command_build_writing_workflows = lambda *a, _n="command_build_writing_workflows", **k: _compile(_n, *a, **k)

def _configure_refresh_commands() -> None:
    refresh_commands.configure(
        project_root_from_args,
        ensure_dirs,
        init_db,
        append_wiki_log,
        log_operation,
        command_import,
        command_build_cards,
        command_build_packs,
        command_build_writing_workflows,
        command_corpus,
        command_corpus_audit,
        command_corpus_style,
        command_build_research_dossiers,
        command_external_sources,
        command_guardrails,
        command_assistant,
        command_verify,
        apply_priority_card_status,
        command_obsidian_sync,
        command_obsidian_status,
    )


def _refresh(name, *args, **kwargs):
    _configure_refresh_commands()
    return getattr(refresh_commands, name)(*args, **kwargs)

preview_import = lambda *a, **k: _refresh("preview_import", *a, **k)
command_refresh = lambda *a, **k: _refresh("command_refresh", *a, **k)

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
