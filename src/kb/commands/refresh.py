from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable

from kb.indexing import rebuild_fts, rebuild_vectors
from kb.ingest import extract_doc, iter_input_files, sha256_text
from kb.store import connect_db

_project_root_from_args: Callable[[str | None], Path] | None = None
_ensure_dirs: Callable[[Path], None] | None = None
_init_db: Callable[[Path], None] | None = None
_append_wiki_log: Callable[[Path, str], None] | None = None
_log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None] | None = None
_command_import: Callable[[argparse.Namespace], int] | None = None
_command_build_cards: Callable[[argparse.Namespace], int] | None = None
_command_build_packs: Callable[[argparse.Namespace], int] | None = None
_command_build_writing_workflows: Callable[[argparse.Namespace], int] | None = None
_command_corpus: Callable[[argparse.Namespace], int] | None = None
_command_corpus_audit: Callable[[argparse.Namespace], int] | None = None
_command_corpus_style: Callable[[argparse.Namespace], int] | None = None
_command_build_research_dossiers: Callable[[argparse.Namespace], int] | None = None
_command_external_sources: Callable[[argparse.Namespace], int] | None = None
_command_guardrails: Callable[[argparse.Namespace], int] | None = None
_command_assistant: Callable[[argparse.Namespace], int] | None = None
_command_verify: Callable[[argparse.Namespace], int] | None = None
_apply_priority_card_status: Callable[[Path], int] | None = None
_command_obsidian_sync: Callable[[argparse.Namespace], int] | None = None
_command_obsidian_status: Callable[[argparse.Namespace], int] | None = None


def configure(
    project_root_from_args: Callable[[str | None], Path],
    ensure_dirs: Callable[[Path], None],
    init_db: Callable[[Path], None],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
    command_import: Callable[[argparse.Namespace], int],
    command_build_cards: Callable[[argparse.Namespace], int],
    command_build_packs: Callable[[argparse.Namespace], int],
    command_build_writing_workflows: Callable[[argparse.Namespace], int],
    command_corpus: Callable[[argparse.Namespace], int],
    command_corpus_audit: Callable[[argparse.Namespace], int],
    command_corpus_style: Callable[[argparse.Namespace], int],
    command_build_research_dossiers: Callable[[argparse.Namespace], int],
    command_external_sources: Callable[[argparse.Namespace], int],
    command_guardrails: Callable[[argparse.Namespace], int],
    command_assistant: Callable[[argparse.Namespace], int],
    command_verify: Callable[[argparse.Namespace], int],
    apply_priority_card_status: Callable[[Path], int],
    command_obsidian_sync: Callable[[argparse.Namespace], int],
    command_obsidian_status: Callable[[argparse.Namespace], int],
) -> None:
    global _project_root_from_args, _ensure_dirs, _init_db, _append_wiki_log, _log_operation
    global _command_import, _command_build_cards, _command_build_packs, _command_build_writing_workflows
    global _command_corpus, _command_corpus_audit, _command_corpus_style, _command_build_research_dossiers
    global _command_external_sources, _command_guardrails, _command_assistant, _command_verify
    global _apply_priority_card_status, _command_obsidian_sync, _command_obsidian_status
    _project_root_from_args = project_root_from_args
    _ensure_dirs = ensure_dirs
    _init_db = init_db
    _append_wiki_log = append_wiki_log
    _log_operation = log_operation
    _command_import = command_import
    _command_build_cards = command_build_cards
    _command_build_packs = command_build_packs
    _command_build_writing_workflows = command_build_writing_workflows
    _command_corpus = command_corpus
    _command_corpus_audit = command_corpus_audit
    _command_corpus_style = command_corpus_style
    _command_build_research_dossiers = command_build_research_dossiers
    _command_external_sources = command_external_sources
    _command_guardrails = command_guardrails
    _command_assistant = command_assistant
    _command_verify = command_verify
    _apply_priority_card_status = apply_priority_card_status
    _command_obsidian_sync = command_obsidian_sync
    _command_obsidian_status = command_obsidian_status


def project_root_from_args(value: str | None) -> Path:
    if _project_root_from_args is None:
        raise RuntimeError("refresh command callbacks are not configured")
    return _project_root_from_args(value)


def ensure_dirs(root: Path) -> None:
    if _ensure_dirs is None:
        raise RuntimeError("refresh command callbacks are not configured")
    _ensure_dirs(root)


def init_db(root: Path) -> None:
    if _init_db is None:
        raise RuntimeError("refresh command callbacks are not configured")
    _init_db(root)


def append_wiki_log(root: Path, message: str) -> None:
    if _append_wiki_log is None:
        raise RuntimeError("refresh command callbacks are not configured")
    _append_wiki_log(root, message)


def log_operation(root: Path, operation: str, status: str, message: str, details: dict | None = None) -> None:
    if _log_operation is None:
        raise RuntimeError("refresh command callbacks are not configured")
    _log_operation(root, operation, status, message, details)


def _call(callback: Callable[[argparse.Namespace], int] | None, args: argparse.Namespace) -> int:
    if callback is None:
        raise RuntimeError("refresh command callbacks are not configured")
    return callback(args)


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
    import_code = _call(_command_import, import_args)
    rows, indexed = rebuild_fts(root)
    _, vector_indexed = rebuild_vectors(root)
    build_args = argparse.Namespace(project_root=args.project_root, set="all", limit=0, top_k=args.top_k)
    _call(_command_build_cards, build_args)
    _call(_command_build_packs, argparse.Namespace(project_root=args.project_root, top_k=args.top_k))
    _call(_command_build_writing_workflows, argparse.Namespace(project_root=args.project_root, top_k=args.top_k))
    _call(_command_corpus, argparse.Namespace(project_root=args.project_root))
    _call(_command_corpus_audit, 
        argparse.Namespace(
            project_root=args.project_root,
            per_type=20,
            low_confidence=80,
            other=80,
            priority=100,
        )
    )
    _call(_command_corpus_style, argparse.Namespace(project_root=args.project_root))
    _call(_command_build_research_dossiers, argparse.Namespace(project_root=args.project_root, set="core-people", limit=0, top_k=args.top_k))
    _call(_command_build_research_dossiers, argparse.Namespace(project_root=args.project_root, set="core-events", limit=0, top_k=args.top_k))
    _call(_command_external_sources, argparse.Namespace(project_root=args.project_root, save=True))
    _call(_command_guardrails, argparse.Namespace(project_root=args.project_root, save=True))
    _call(_command_assistant, argparse.Namespace(project_root=args.project_root, query=None, mode="auto", top_k=args.top_k, save=False, install=True, sync_vault=None))
    _call(_command_verify, argparse.Namespace(project_root=args.project_root, save=True))
    priority_count = (_apply_priority_card_status(root) if _apply_priority_card_status is not None else 0)
    sync_count = 0
    if args.vault:
        sync_args = argparse.Namespace(project_root=args.project_root, vault=args.vault, dry_run=False)
        sync_code = _call(_command_obsidian_sync, sync_args)
        sync_count = 0 if sync_code else len(list((Path(args.vault).expanduser()).rglob("*.md")))
        _call(_command_obsidian_status, argparse.Namespace(project_root=args.project_root, vault=args.vault, save=True))
        _call(_command_verify, argparse.Namespace(project_root=args.project_root, save=True))
        _call(_command_obsidian_sync, sync_args)
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


