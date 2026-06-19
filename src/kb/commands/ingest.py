from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from kb.ingest import (
    ArticleDoc,
    chunk_text,
    extract_doc,
    extract_doc_from_url,
    iter_input_files,
    quarantine_file,
    sha256_text,
    write_raw,
)
from kb.store import connect_db, now_iso


def command_init(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    ensure_dirs: Callable[[Path], None],
    init_db: Callable[[Path], None],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> int:
    root = project_root_from_args(args.project_root)
    ensure_dirs(root)
    init_db(root)
    append_wiki_log(root, "初始化项目目录和数据库")
    log_operation(root, "init", "ok", "initialized project")
    print(f"Initialized: {root}")
    return 0


def command_scan(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> int:
    root = project_root_from_args(args.project_root)
    input_dir = Path(args.input).expanduser().resolve()
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 2
    files = list(iter_input_files(input_dir))
    by_ext = {}
    by_account = {}
    for path in files:
        by_ext[path.suffix.lower()] = by_ext.get(path.suffix.lower(), 0) + 1
        try:
            account = path.relative_to(input_dir).parts[0]
        except Exception:
            account = "(root)"
        by_account[account] = by_account.get(account, 0) + 1
    manifest = root / "index" / "manifests" / f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "input": str(input_dir),
                "total": len(files),
                "by_ext": by_ext,
                "by_account": by_account,
                "created_at": now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log_operation(root, "scan", "ok", f"scanned {len(files)} files", {"input": str(input_dir), "manifest": str(manifest)})
    print(f"Input: {input_dir}")
    print(f"Supported files: {len(files)}")
    print("By account:")
    for key, value in sorted(by_account.items()):
        print(f"  {key}: {value}")
    print(f"Manifest: {manifest}")
    return 0


def command_import(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    ensure_dirs: Callable[[Path], None],
    init_db: Callable[[Path], None],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> int:
    root = project_root_from_args(args.project_root)
    ensure_dirs(root)
    init_db(root)
    input_dir = Path(args.input).expanduser().resolve()
    if not input_dir.exists():
        print(f"Input directory not found: {input_dir}", file=sys.stderr)
        return 2
    limit = args.limit if args.limit is not None else 20
    files = list(iter_input_files(input_dir))[:limit]
    imported = 0
    skipped = 0
    failed = 0
    preview_rows = []
    source_id = getattr(args, "source_id", None) or None
    authority_level = getattr(args, "authority_level", None) or "L4"
    source_tier = getattr(args, "source_tier", None) or authority_level
    is_citable = 1 if getattr(args, "is_citable", False) else 0
    conn = connect_db(root)
    try:
        for path in files:
            try:
                doc = extract_doc(path, input_dir)
                content_hash = sha256_text(doc.text)
                exists = conn.execute("SELECT id FROM articles WHERE content_hash = ?", (content_hash,)).fetchone()
                preview_rows.append((doc.title, doc.account, doc.published_at, content_hash[:12], str(path)))
                if exists:
                    skipped += 1
                    continue
                if args.dry_run:
                    imported += 1
                    continue
                with conn:
                    insert_article_doc(
                        conn,
                        root,
                        doc,
                        source_path=str(path),
                        source_id=source_id,
                        authority_level=authority_level,
                        source_tier=source_tier,
                        is_citable=is_citable,
                        content_hash=content_hash,
                    )
                    imported += 1
            except Exception as exc:
                failed += 1
                if not args.dry_run:
                    quarantine_file(root, path, repr(exc))
        status = "dry-run" if args.dry_run else "ok"
        log_operation(
            root,
            "import",
            status,
            f"imported={imported} skipped={skipped} failed={failed}",
            {
                "input": str(input_dir),
                "limit": limit,
                "dry_run": args.dry_run,
                "source_id": source_id,
                "authority_level": authority_level,
                "source_tier": source_tier,
                "is_citable": bool(is_citable),
            },
        )
        if not args.dry_run:
            append_wiki_log(root, f"导入测试样本：imported={imported} skipped={skipped} failed={failed}")
    finally:
        conn.close()
    print(f"Input: {input_dir}")
    print(f"Limit: {limit}")
    print(f"Dry run: {args.dry_run}")
    print(f"Authority: {authority_level} | Source tier: {source_tier} | Citable: {bool(is_citable)}")
    if source_id:
        print(f"Source id: {source_id}")
    print(f"Imported/planned: {imported}")
    print(f"Skipped duplicate: {skipped}")
    print(f"Failed: {failed}")
    print("Preview:")
    for row in preview_rows[:10]:
        print(f"  [{row[3]}] {row[0]} | {row[1]} | {row[2]}")
    return 0 if failed == 0 else 1


def command_ingest_file(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    ensure_dirs: Callable[[Path], None],
    init_db: Callable[[Path], None],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
    clean_snippet: Callable[[str, int], str],
) -> int:
    root = project_root_from_args(args.project_root)
    ensure_dirs(root)
    init_db(root)
    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 2
    source_tier = args.source_tier or args.authority_level
    is_citable = 1 if args.is_citable else 0
    try:
        doc = extract_doc(path, path.parent)
        if args.account:
            doc.account = args.account
        if args.title:
            doc.title = args.title
        if args.published_at:
            doc.published_at = args.published_at
        if args.source_url:
            doc.source_url = args.source_url
        content_hash = sha256_text(doc.text)
    except Exception as exc:
        print(f"File extract failed: {exc}", file=sys.stderr)
        return 1
    conn = connect_db(root)
    try:
        exists = conn.execute("SELECT id FROM articles WHERE content_hash = ?", (content_hash,)).fetchone()
        print(f"File: {path}")
        print(f"Title: {doc.title}")
        print(f"Account: {doc.account or ''}")
        print(f"Published at: {doc.published_at or ''}")
        print(f"Source URL: {doc.source_url or ''}")
        print(f"Authority: {args.authority_level} | Source tier: {source_tier} | Citable: {bool(is_citable)}")
        if args.source_id:
            print(f"Source id: {args.source_id}")
        print(f"Chars: {len(doc.text)}")
        print(f"Content hash: {content_hash[:12]}")
        if exists:
            print(f"Skipped duplicate: article_id={exists['id']}")
            return 0
        if args.dry_run:
            print("Dry run: True")
            print(clean_snippet(doc.text, 500))
            log_operation(root, "ingest-file", "dry-run", "file previewed", {"file": str(path), "authority_level": args.authority_level})
            return 0
        with conn:
            article_id = insert_article_doc(
                conn,
                root,
                doc,
                source_path=str(path),
                source_id=args.source_id,
                authority_level=args.authority_level,
                source_tier=source_tier,
                is_citable=is_citable,
                content_hash=content_hash,
            )
        append_wiki_log(root, f"导入权威公开文件：article_id={article_id} {doc.title}")
        log_operation(
            root,
            "ingest-file",
            "ok",
            f"article_id={article_id}",
            {"file": str(path), "source_url": doc.source_url, "authority_level": args.authority_level, "source_id": args.source_id},
        )
        print(f"Imported: article_id={article_id}")
        return 0
    finally:
        conn.close()


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
    raw_path = write_raw(root, doc, content_hash)
    cur = conn.execute(
        """
        INSERT INTO articles(title, account, author, published_at, source_path, raw_path, source_url,
                             source_id, authority_level, source_tier, is_citable,
                             content_hash, imported_at, file_type, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            doc.title,
            doc.account,
            doc.author,
            doc.published_at,
            source_path,
            str(raw_path),
            doc.source_url,
            source_id,
            authority_level,
            source_tier,
            is_citable,
            content_hash,
            now_iso(),
            doc.file_type,
            "imported",
        ),
    )
    article_id = cur.lastrowid
    for idx, chunk in enumerate(chunk_text(doc.text)):
        conn.execute(
            """
            INSERT INTO article_chunks(article_id, chunk_index, content, content_hash, token_estimate, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (article_id, idx, chunk, sha256_text(chunk), max(1, len(chunk) // 2), now_iso()),
        )
    return int(article_id)


def command_ingest_url(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    ensure_dirs: Callable[[Path], None],
    init_db: Callable[[Path], None],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
    clean_snippet: Callable[[str, int], str],
) -> int:
    root = project_root_from_args(args.project_root)
    ensure_dirs(root)
    init_db(root)
    source_tier = args.source_tier or args.authority_level
    is_citable = 1 if args.is_citable else 0
    try:
        doc = extract_doc_from_url(
            args.url,
            account=args.account,
            title=args.title,
            published_at=args.published_at,
            timeout=args.timeout,
            insecure=args.insecure,
        )
    except Exception as exc:
        print(f"URL fetch failed: {exc}", file=sys.stderr)
        return 1
    content_hash = sha256_text(doc.text)
    conn = connect_db(root)
    try:
        exists = conn.execute("SELECT id FROM articles WHERE content_hash = ?", (content_hash,)).fetchone()
        print(f"URL: {args.url}")
        print(f"Title: {doc.title}")
        print(f"Account: {doc.account or ''}")
        print(f"Published at: {doc.published_at or ''}")
        print(f"Authority: {args.authority_level} | Source tier: {source_tier} | Citable: {bool(is_citable)}")
        if args.source_id:
            print(f"Source id: {args.source_id}")
        print(f"Chars: {len(doc.text)}")
        print(f"Content hash: {content_hash[:12]}")
        if exists:
            print(f"Skipped duplicate: article_id={exists['id']}")
            return 0
        if args.dry_run:
            print("Dry run: True")
            print(clean_snippet(doc.text, 500))
            log_operation(root, "ingest-url", "dry-run", "url previewed", {"url": args.url, "authority_level": args.authority_level})
            return 0
        with conn:
            article_id = insert_article_doc(
                conn,
                root,
                doc,
                source_path=args.url,
                source_id=args.source_id,
                authority_level=args.authority_level,
                source_tier=source_tier,
                is_citable=is_citable,
                content_hash=content_hash,
            )
        append_wiki_log(root, f"导入权威公开 URL：article_id={article_id} {doc.title}")
        log_operation(
            root,
            "ingest-url",
            "ok",
            f"article_id={article_id}",
            {"url": args.url, "authority_level": args.authority_level, "source_id": args.source_id},
        )
        print(f"Imported: article_id={article_id}")
        return 0
    finally:
        conn.close()
