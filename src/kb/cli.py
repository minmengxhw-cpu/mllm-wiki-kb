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
from kb.commands import obsidian as obsidian_commands
from kb.commands import search_ask as search_ask_commands
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
    chunk_text,
    extract_doc,
    extract_doc_from_url,
    iter_input_files,
    quarantine_file,
    sha256_text,
    slugify,
    write_raw,
)

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
    root = project_root_from_args(args.project_root)
    ensure_dirs(root)
    init_db(root)
    append_wiki_log(root, "初始化项目目录和数据库")
    log_operation(root, "init", "ok", "initialized project")
    print(f"Initialized: {root}")
    return 0


def command_scan(args: argparse.Namespace) -> int:
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


def command_import(args: argparse.Namespace) -> int:
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


def command_ingest_file(args: argparse.Namespace) -> int:
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


def command_ingest_url(args: argparse.Namespace) -> int:
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


def clean_snippet(value: str, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = value.replace("[", "").replace("]", "")
    return value[:limit]


def row_source_line(row: sqlite3.Row, idx: int) -> str:
    authority = row_authority_label(row)
    return (
        f"[S{idx}] {authority} {row['account']}，{row['published_at'] or '日期不详'}，"
        f"《{row['title']}》，raw: `{row['raw_path']}`"
    )


def row_authority_label(row: sqlite3.Row) -> str:
    keys = row.keys()
    level = str(row["authority_level"] if "authority_level" in keys and row["authority_level"] else "L4")
    citable = bool(int(row["is_citable"] or 0)) if "is_citable" in keys and row["is_citable"] is not None else False
    if level in {"L1", "L2", "L3"}:
        return f"{level}{'/可引用' if citable else '/待核'}"
    return "L4/样本"


def row_source_md(row: sqlite3.Row, idx: int) -> str:
    raw_path = row["raw_path"] or ""
    source_label = f"{row['account'] or ''}（{row_authority_label(row)}）"
    return (
        f"| S{idx} | {source_label} | {row['published_at'] or '日期不详'} | "
        f"《{row['title']}》 | `{raw_path}` |"
    )


def unique_source_rows(rows: list[sqlite3.Row], limit: int = 12) -> list[sqlite3.Row]:
    out = []
    seen: set[int] = set()
    for row in rows:
        article_id = int(row["article_id"])
        if article_id in seen:
            continue
        seen.add(article_id)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def staff_index_dir(root: Path) -> Path:
    return root / "index"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def load_staff_entities(root: Path) -> list[dict]:
    base = staff_index_dir(root) / "entities"
    items: list[dict] = []
    for name in ["persons.jsonl", "orgs.jsonl", "events.jsonl", "places.jsonl"]:
        items.extend(load_jsonl(base / name))
    return items


def load_external_inventory(root: Path) -> list[dict]:
    return load_jsonl(staff_index_dir(root) / "external_sources" / "google_drive_inventory.jsonl")


def citation_table(rows: list[sqlite3.Row]) -> str:
    source_rows = unique_source_rows(rows, 12)
    if not source_rows:
        return "| - | - | - | 未检索到可靠来源 | - |"
    return "\n".join(row_source_md(row, idx) for idx, row in enumerate(source_rows, 1))


def cited_excerpts(rows: list[sqlite3.Row], limit: int = 10, chars: int = 260) -> str:
    source_rows = unique_source_rows(rows, limit)
    if not source_rows:
        return "- 未检索到可靠来源，相关判断须标注 [待核]。"
    return "\n".join(f"{idx}. {clean_snippet(row['snippet'], chars)} [S{idx}]" for idx, row in enumerate(source_rows, 1))


def staff_formulation_lines(items: list[dict]) -> str:
    if not items:
        return "- 未命中种子口径库；涉及正式表述时仍需按最新红头文件和内部口径核定。"
    lines = []
    for item in items:
        status = item.get("status") or "待核"
        term = item.get("term") or item.get("canonical") or ""
        canonical = item.get("canonical") or "待补充规范表述"
        note = item.get("note") or ""
        source = item.get("latest_source") or "待补来源"
        lines.append(f"- {term}：建议表述为“{canonical}”；状态：{status}；来源：{source}。{note}")
    return "\n".join(lines)


def staff_entity_lines(items: list[dict]) -> str:
    if not items:
        return "- 未命中种子实体库；可继续依靠本地全文检索和现有人物/事件/地点卡片核对。"
    lines = []
    for item in items:
        name = item.get("name") or ""
        item_type = item.get("type") or item.get("entity_type") or "entity"
        summary = item.get("summary") or item.get("description") or "待补充"
        risk = item.get("risk") or item.get("disputes") or ""
        lines.append(f"- {name}（{item_type}）：{summary}{'；风险/争议：' + risk if risk else ''}")
    return "\n".join(lines)


def staff_risk_lines(root: Path, text: str, rows: list[sqlite3.Row]) -> str:
    risks = sorted(match_blacklist(root, text), key=lambda item: staff_severity_rank(item.get("severity")))
    lines = []
    for item in risks:
        lines.append(
            f"- [{severity_label(item.get('severity'))}] 命中“{item.get('pattern')}”："
            f"{item.get('canonical') or '请改为规范表述或删除'}。{item.get('note') or ''}"
        )
    if not rows:
        lines.append("- [待核] 未检索到本地来源，不能直接作为事实性结论或正式文稿素材。")
    lines.append("- 正式发稿前，红头文件、内部口径和人工终审优先级高于本公开语料库。")
    return "\n".join(lines)


def staff_query(mode: str, topic: str) -> str:
    extras = {
        "draft": "上海民盟 微信 写作 报道 讲话 纪念 活动",
        "history": "民盟 盟史 历史 人物 事件 上海",
        "topic": "盟史 选题 课堂 历史",
        "info": "参政议政 社情民意 调研 建议 提案 问题 对策 履职",
        "check": "上海民盟 民盟 盟史 口径 核验",
    }
    topic_extras = []
    if mode == "history":
        if "新型政党制度" in topic:
            topic_extras.extend(["多党合作", "政治协商", "人民政协"])
        if "五一口号" in topic:
            topic_extras.extend(["新政协", "民主党派", "响应"])
        if "救国会" in topic or "七君子" in topic:
            topic_extras.extend(["沈钧儒", "史良", "抗日救亡"])
    return f"{topic} {' '.join(topic_extras)} {extras.get(mode, '')}".strip()


def topic_query_variants(topic: str) -> list[str]:
    core = re.sub(r"^(午间盟史课堂|盟史课堂|课堂)\s*[:：]\s*", "", topic.strip())
    spaced = re.sub(r"[《》“”\"'：:，,、；;（）()\-—|]+", " ", core)
    spaced = re.sub(r"(?<=[\u4e00-\u9fff])(?:与|和|及)(?=[\u4e00-\u9fff])", " ", spaced)
    spaced = re.sub(r"\s+", " ", spaced).strip()
    variants = [spaced, core, topic, staff_query("topic", spaced)]
    out = []
    seen = set()
    for value in variants:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def history_query_variants(topic: str) -> list[str]:
    variants = [topic, staff_query("history", topic)]
    if "新型政党制度" in topic:
        variants.extend(["新型政党制度 多党合作", "新型政党制度 政治协商 人民政协"])
    if "五一口号" in topic:
        variants.extend(["五一口号 新政协 民主党派", "民盟 响应 五一口号"])
    if "救国会" in topic or "七君子" in topic:
        variants.extend(["沈钧儒 救国会 七君子", "救国会 抗日救亡 史良"])
    out = []
    seen = set()
    for value in variants:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def merge_search_rows(row_groups: list[list[sqlite3.Row]], top_k: int) -> list[sqlite3.Row]:
    def ranked(items: list[sqlite3.Row]) -> list[sqlite3.Row]:
        return sorted(
            items,
            key=lambda row: (
                authority_rank(row["authority_level"] if "authority_level" in row.keys() else None),
                -int(row["is_citable"] or 0) if "is_citable" in row.keys() else 0,
            ),
        )[:top_k]

    rows = []
    seen_chunks: set[str] = set()
    seen_articles: set[str] = set()
    for group in row_groups:
        for row in group:
            chunk_id = str(row["chunk_id"])
            article_id = str(row["article_id"])
            if chunk_id in seen_chunks:
                continue
            rows.append(row)
            seen_chunks.add(chunk_id)
            seen_articles.add(article_id)
            if len(rows) >= top_k:
                return ranked(rows)
    if len(rows) >= top_k:
        return ranked(rows)
    for group in row_groups:
        for row in group:
            article_id = str(row["article_id"])
            if article_id in seen_articles:
                continue
            rows.append(row)
            seen_articles.add(article_id)
            if len(rows) >= top_k:
                return ranked(rows)
    return ranked(rows)


def staff_search_rows(root: Path, mode: str, topic: str, top_k: int) -> list[sqlite3.Row]:
    if mode == "topic":
        queries = topic_query_variants(topic)
    elif mode == "history":
        queries = history_query_variants(topic)
    else:
        queries = [staff_query(mode, topic)]
    groups = [search_rows(root, query, top_k) for query in queries]
    return merge_search_rows(groups, top_k)


def staff_card_matches(root: Path, topic: str, limit: int = 8) -> list[sqlite3.Row]:
    if not (root / "schema.sql").exists():
        return []
    init_db(root)
    conn = connect_db(root)
    try:
        rows = conn.execute(
            """
            SELECT title, page_type, path, source_count, review_status, priority_card
            FROM wiki_pages
            WHERE title LIKE ? OR path LIKE ?
            ORDER BY priority_card DESC, source_count DESC, updated_at DESC
            LIMIT ?
            """,
            (f"%{topic}%", f"%{topic}%", limit),
        ).fetchall()
        return rows
    finally:
        conn.close()


def staff_cards_block(root: Path, topic: str) -> str:
    rows = staff_card_matches(root, topic)
    if not rows:
        return "- 未找到同名自动卡片；可用 `kb build-cards` 或 `kb compile` 后补。"
    return "\n".join(
        f"- {row['title']}（{row['page_type']}，来源 {row['source_count']}，{row['review_status']}）：`{row['path']}`"
        for row in rows
    )


def external_reference_matches(root: Path, topic: str, limit: int = 6) -> list[dict]:
    matches = []
    for item in load_external_inventory(root):
        haystack = "\n".join(
            str(item.get(key) or "")
            for key in ["title", "path", "source", "import_decision", "role"]
        )
        if topic and topic in haystack:
            matches.append(item)
        if len(matches) >= limit:
            break
    return matches


def external_reference_block(root: Path, topic: str) -> str:
    matches = external_reference_matches(root, topic)
    if not matches:
        return "- 未命中 Drive 外部参考层；当前输出以本地检索来源为准，并按 L1-L4 权威级别区分使用。"
    rows = [["材料", "来源层", "导入判断", "链接"]]
    for item in matches:
        rows.append(
            [
                str(item.get("title") or ""),
                f"{item.get('source') or ''}/{item.get('layer') or ''}",
                str(item.get("import_decision") or "外部参考"),
                f"[打开]({item.get('url') or ''})",
            ]
        )
    return markdown_table(rows)


def external_sources_report_markdown(root: Path, created_at: str) -> str:
    folders = load_jsonl(staff_index_dir(root) / "external_sources" / "google_drive_folders.jsonl")
    inventory = load_external_inventory(root)
    by_source = Counter(str(item.get("source") or "unknown") for item in inventory)
    by_decision = Counter(str(item.get("import_decision") or "未判定") for item in inventory)
    folder_rows = [["知识库", "用途", "状态"]]
    for item in folders:
        folder_rows.append([
            str(item.get("name") or ""),
            str(item.get("role") or ""),
            str(item.get("status") or ""),
        ])
    inventory_rows = [["材料", "来源层", "导入判断", "链接"]]
    for item in inventory:
        if item.get("item_type") == "folder":
            continue
        inventory_rows.append([
            str(item.get("title") or ""),
            f"{item.get('source') or ''}/{item.get('layer') or ''}",
            str(item.get("import_decision") or ""),
            f"[打开]({item.get('url') or ''})",
        ])
    return f"""# Google Drive外部参考层状态

生成时间：{created_at}

本页用于查看已连接的 Google Drive 工作资料。它们当前是外部参考层，不等同于微信公众号主语料，也不自动作为事实出处。

## 总体结论

- 已登记知识库：{len(folders)} 个。
- 文件级清单记录：{len(inventory)} 条。
- 当前 `raw` 文件夹盘点结果显示暂无新的原始公众号文章，现有可用内容主要在 `wiki` 成果层。
- `/稿` 和 `/史` 会在主题命中时提示相关 Drive 材料，但仍要求回到公开语料、原始文件或权威资料核验。

## 已登记知识库

{markdown_table(folder_rows)}

## 按来源统计

{markdown_table([["来源", "记录数"]] + [[k, str(v)] for k, v in by_source.most_common()])}

## 按导入判断统计

{markdown_table([["导入判断", "记录数"]] + [[k, str(v)] for k, v in by_decision.most_common()])}

## 可人工参考材料

{markdown_table(inventory_rows)}

## 使用边界

- 公开微信公众号文章仍是主语料层。
- Drive 工作材料只作外部参考和人工查阅提示。
- 个人履历、内部草稿、红头文件和未公开工作材料不进入公众号主语料。
- 正式发稿、史实判断和口径判断仍以权威资料、内部口径和人工终审为准。
"""


def command_pro_sources(args: argparse.Namespace) -> int:
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


def command_sources(args: argparse.Namespace) -> int:
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


def command_source_urls(args: argparse.Namespace) -> int:
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


def research_dossier_matches(root: Path, topic: str, limit: int = 6) -> list[Path]:
    dirs = [
        root / "wiki" / "研究助手" / "核心人物研究档案",
        root / "wiki" / "研究助手" / "核心事件研究档案",
    ]
    matches = []
    for directory in dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.stem == "索引":
                continue
            if topic in path.stem or path.stem in topic:
                matches.append(path)
    return matches[:limit]


def research_dossier_block(root: Path, topic: str) -> str:
    matches = research_dossier_matches(root, topic)
    if not matches:
        return "- 未命中核心人物/事件研究档案；可先用 `kb build-research-dossiers --set core-people` 或 `--set core-events` 补建。"
    rows = [["档案", "类型", "路径", "结论状态"]]
    for path in matches:
        dossier_type = "人物" if "核心人物研究档案" in str(path) else "事件"
        status = "待人工核验"
        text = path.read_text(encoding="utf-8")
        status_match = re.search(r"结论状态：([^\n]+)", text)
        if status_match:
            status = status_match.group(1).strip()
        rows.append([path.stem, dossier_type, f"`{path}`", status])
    return markdown_table(rows)


def staff_history_research_route(topic: str) -> str:
    return f"""- 第一步：先打开命中的核心研究档案，确认来源分布、主题线索和待核字段。
- 第二步：按“时间线、人物关系、组织关系、上海地方线索、争议风险”五栏摘录证据。
- 第三步：所有年份、会议名称、职务、组织名称、地点和历史评价逐条回 raw 原文核验。
- 第四步：正式写作时区分全国民盟史主线、上海民盟地方史线索和公众号纪念性表达。
- 第五步：无法由当前语料证明的判断，一律写作 `[待核]`，不要替代权威档案结论。"""


def staff_draft_article_type(topic: str) -> str:
    article_type, _, _ = classify_article(topic, "上海民盟", topic)
    if article_type == "other":
        return "activity_report"
    if article_type == "notice_info":
        return "activity_report"
    return article_type


def staff_curated_writing_samples(root: Path, topic: str, limit: int = 6) -> tuple[str, str]:
    article_type = staff_draft_article_type(topic)
    labels_path = corpus_dir(root) / "article_labels.jsonl"
    if not labels_path.exists():
        return article_type, "- 未找到 `index/corpus/article_labels.jsonl`，请先运行 `kb corpus` 生成精选样本索引。"
    labels = [json.loads(line) for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    candidates = []
    for label in labels:
        if label.get("account") != "上海民盟":
            continue
        if label.get("article_type") != article_type:
            continue
        if not label.get("is_writing_sample"):
            continue
        if not year_at_least(label.get("year"), "2023"):
            continue
        score, reasons = writing_sample_score(label)
        candidates.append({**label, "sample_score": score, "sample_reasons": reasons})
    candidates.sort(key=lambda item: (-int(item["sample_score"]), item.get("published_at") or "", int(item["article_id"])))
    rows = [["分数", "日期", "标题", "入选理由", "raw 原文"]]
    for item in candidates[:limit]:
        rows.append(
            [
                str(item["sample_score"]),
                item.get("published_at") or "日期不详",
                f"《{item.get('title') or ''}》",
                "；".join(item.get("sample_reasons") or []),
                f"`{item.get('raw_path') or ''}`",
            ]
        )
    if len(rows) == 1:
        return article_type, "- 未找到同体裁精选样本；可退回全库同题历史稿和 `上海民盟2023年以来写作样本库.md`。"
    return article_type, markdown_table(rows)


def staff_draft_structure_block(article_type: str) -> str:
    guide = WRITING_STYLE_GUIDES.get(article_type)
    if not guide:
        return """- 标题：先点明对象和动作，避免空泛口号。
- 导语：补齐时间、地点、主体、事项和核心主题。
- 正文：按事实顺序展开，每段绑定材料或来源。
- 结尾：落到下一步工作或现实意义，避免无来源拔高。"""
    return f"""- 标题写法：{guide["title"]}
- 导语写法：{guide["lead"]}
- 正文骨架：{guide["structure"]}
- 适用场景：{guide["use"]}
- 体裁风险：{guide["risk"]}"""


def material_points(text: str, limit: int = 8) -> list[str]:
    parts = []
    for line in text.splitlines():
        line = line.strip(" -\t")
        if not line:
            continue
        parts.extend(part.strip() for part in re.split(r"(?<=[。！？；])", line) if part.strip())
    if not parts and text.strip():
        parts = [text.strip()]
    cleaned = []
    seen = set()
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip()
        if not part or part in seen:
            continue
        seen.add(part)
        cleaned.append(part)
        if len(cleaned) >= limit:
            break
    return cleaned


def material_field_hints(text: str) -> dict[str, str]:
    date_match = re.search(r"(20\d{2}年\d{1,2}月\d{1,2}日|20\d{2}年\d{1,2}月|20\d{2}-\d{1,2}-\d{1,2})", text)
    location_match = re.search(r"在([^，。；\n]{2,24})(?:举行|召开|开展|举办|启动)", text)
    return {
        "date": date_match.group(1) if date_match else "[时间待核]",
        "location": location_match.group(1) if location_match else "[地点待核]",
    }


def title_suggestions(topic: str, article_type: str) -> list[str]:
    if article_type == "meeting_report":
        return [f"{topic}召开", f"围绕{topic}，这场会议作出部署"]
    if article_type == "theme_education":
        return [f"{topic}走深走实", f"以学促干，推动{topic}见行见效"]
    if article_type == "policy_advice":
        return [f"聚焦{topic}，上海民盟这样建言", f"围绕{topic}献良策、聚共识"]
    if article_type == "person_profile":
        return [f"这位盟员的{topic}故事", f"在{topic}中见初心"]
    if article_type == "history_commemoration":
        return [f"回望{topic}，赓续民盟优良传统", f"从{topic}中汲取前行力量"]
    return [topic, f"围绕{topic}，上海民盟开展相关工作"]


def draft_paragraphs_from_material(topic: str, article_type: str, material: str) -> list[str]:
    fields = material_field_hints(material)
    points = material_points(material, 6)
    if article_type == "meeting_report":
        lead = f"{fields['date']}，{topic}在{fields['location']}召开。会议围绕[会议主题待核]开展交流部署，相关负责人和人员参加。[M1]"
    elif article_type == "theme_education":
        lead = f"{fields['date']}，围绕{topic}，相关活动在{fields['location']}举行，推动学习教育与履职实践相结合。[M1]"
    elif article_type == "policy_advice":
        lead = f"围绕{topic}，上海民盟相关组织和盟员结合调研情况，聚焦问题、提出建议，服务中心大局。[M1]"
    elif article_type == "person_profile":
        lead = f"{topic}相关人物材料显示，人物经历、专业贡献和盟务履职之间具有可展开的报道价值。[M1]"
    elif article_type == "history_commemoration":
        lead = f"围绕{topic}，材料可从历史脉络、人物关系和现实传承三个层面展开。[M1]"
    else:
        lead = f"{fields['date']}，围绕{topic}，相关工作在{fields['location']}开展。[M1]"
    body = [lead]
    for idx, point in enumerate(points[:4], 1):
        body.append(f"{point} [M{idx}]")
    if article_type == "policy_advice":
        body.append("下一步，可围绕问题发现、调研依据、对策建议和办理反馈继续补充材料，形成更完整的参政议政报道。[待核]")
    elif article_type == "history_commemoration":
        body.append("正式成稿前，应补充权威史料或 raw 原文出处，对时间、人物职务、历史评价逐条核验。[待核]")
    else:
        body.append("后续将结合相关部署和实际成效，持续推动工作走深走实。[待核]")
    return body


def staff_material_draft_body(root: Path, topic: str, material: str, rows: list[sqlite3.Row]) -> str:
    formulations = match_staff_items(load_formulations(root), f"{topic}\n{material}")
    article_type, curated_samples = staff_curated_writing_samples(root, topic)
    type_name = ARTICLE_TYPE_NAMES.get(article_type, article_type)
    structure_block = staff_draft_structure_block(article_type)
    external_refs = external_reference_block(root, topic)
    material_issues = staff_check_issues(root, material)
    titles = title_suggestions(topic, article_type)
    paragraphs = draft_paragraphs_from_material(topic, article_type, material)
    title_lines = "\n".join(f"- {title}" for title in titles)
    draft_text = "\n\n".join(paragraphs)
    draft_issues = staff_check_issues(root, draft_text)
    material_table = "\n".join(f"| M{idx} | {clean_snippet(point, 140)} |" for idx, point in enumerate(material_points(material, 10), 1))
    return f"""# 盟参 /稿：{topic}

## 结论

- 已根据用户材料生成公众号初稿框架；初步判断适用体裁：{type_name}。
- 初稿只使用用户材料 `[M]` 和本地参考来源 `[S]`，无法确认的事实已保留 `[待核]`。
- 正式发稿前必须继续补齐时间、地点、人物职务、主办承办单位、数据、图片说明和权威口径。

## 初稿

### 标题备选

{title_lines}

### 正文初稿

{draft_text}

## 素材

### 用户材料拆解

| 编号 | 材料要点 |
|---|---|
{material_table or '| M1 | 用户材料为空或无法拆分。 |'}

### 精选写作样本

{curated_samples}

### Drive 外部参考层

{external_refs}

### 体裁写作骨架

{structure_block}

### 本地参考来源

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{citation_table(rows)}

### 参考摘录

{cited_excerpts(rows, 6)}

### 口径要点

{staff_formulation_lines(formulations)}

## 风险提示

### 用户材料核验

| 编号 | 严重度 | 类型 | 命中内容 | 建议处理 |
|---|---|---|---|---|
{issue_table(material_issues)}

### 初稿核验

| 编号 | 严重度 | 类型 | 命中内容 | 建议处理 |
|---|---|---|---|---|
{issue_table(draft_issues)}

### 其他风险

{staff_risk_lines(root, f"{topic}\n{material}", rows)}
"""


def staff_draft_body(root: Path, topic: str, rows: list[sqlite3.Row]) -> str:
    formulations = match_staff_items(load_formulations(root), topic)
    article_type, curated_samples = staff_curated_writing_samples(root, topic)
    type_name = ARTICLE_TYPE_NAMES.get(article_type, article_type)
    structure_block = staff_draft_structure_block(article_type)
    external_refs = external_reference_block(root, topic)
    return f"""# 盟参 /稿：{topic}

## 结论

- 本次为“文稿素材包”，不是最终成稿；已检索到 {len(rows)} 条片段，覆盖 {len({int(row['article_id']) for row in rows}) if rows else 0} 篇来源文章。
- 初步判断适用体裁：{type_name}。写作时优先参考 `wiki/研究助手/上海民盟微信公众号写作风格规则卡.md` 和同体裁精选样本。
- 可先按“同题历史稿 + 口径要点 + 常用结构 + 风险提示”进入起草；事实性句子必须保留 [S] 来源或标注 [待核]。
- 若要生成正式微信公众号文章，请继续补充时间、地点、人物职务、主办承办单位、活动流程、讲话要点和图片说明。

## 素材

### 同题历史稿

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{citation_table(rows)}

### 精选写作样本

{curated_samples}

### Drive 外部参考层

{external_refs}

### 体裁写作骨架

{structure_block}

### 证据摘录

{cited_excerpts(rows, 10)}

### 可复用结构

- 导语：先交代时间、地点、活动全称、主办承办单位和核心主题 [待按材料核]。
- 主体：按“背景意义 -> 现场流程 -> 领导/专家观点 -> 具体成果”组织，每一段至少绑定一个来源或原始材料。
- 结尾：落到民盟履职、传统传承、主题教育、组织建设或下一步工作，避免空泛表态。

### 口径要点

{staff_formulation_lines(formulations)}

## 风险提示

{staff_risk_lines(root, topic, rows)}
"""


def staff_history_body(root: Path, topic: str, rows: list[sqlite3.Row]) -> str:
    formulations = match_staff_items(load_formulations(root), topic)
    entities = match_staff_items(load_staff_entities(root), topic)
    dossiers = research_dossier_block(root, topic)
    external_refs = external_reference_block(root, topic)
    route = staff_history_research_route(topic)
    return f"""# 盟参 /史：{topic}

## 结论

- 本次为“史实卡片/研究入口”，已检索到 {len(rows)} 条片段，覆盖 {len({int(row['article_id']) for row in rows}) if rows else 0} 篇来源文章。
- 若命中核心研究档案，应优先以研究档案作为入口，再回 raw 原文核验。
- 当前输出只能作为研究线索；涉及年份、会议、职务、组织名称和地点，必须打开 raw 原文和权威资料复核。
- 若来源不足或存在争议，相关表述一律按 [待核] 处理。

## 素材

### 核心研究档案

{dossiers}

### 已有卡片

{staff_cards_block(root, topic)}

### 种子实体库

{staff_entity_lines(entities)}

### Drive 外部参考层

{external_refs}

### 来源文章

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{citation_table(rows)}

### 证据摘录

{cited_excerpts(rows, 10)}

### 时间线线索

{timeline_candidates(rows, 12)}

### 口径/争议线索

{staff_formulation_lines(formulations)}

### 研究路线

{route}

## 风险提示

{staff_risk_lines(root, topic, rows)}
"""


def normalized_similarity(left: str, right: str) -> float:
    raw_left = left or ""
    left_terms = [term for term in re.split(r"\s+", raw_left.strip()) if len(term) >= 2]
    left = re.sub(r"[\s《》“”\"'：:，,、；;（）()\-—|]+", "", raw_left)
    right = re.sub(r"[\s《》“”\"'：:，,、；;（）()\-—|]+", "", right or "")
    if not left or not right:
        return 0.0
    coverage_score = 0.0
    if len(left_terms) >= 2:
        covered = sum(1 for term in left_terms if term in right)
        coverage = covered / len(left_terms)
        if coverage == 1:
            coverage_score = 0.86
        elif coverage >= 0.5:
            coverage_score = 0.55
    if left in right or right in left:
        shorter = min(len(left), len(right))
        longer = max(len(left), len(right))
        return max(0.72, shorter / longer, coverage_score)
    return max(difflib.SequenceMatcher(None, left, right).ratio(), coverage_score)


def topic_similarity_rows(topic: str, rows: list[sqlite3.Row], limit: int = 10) -> list[tuple[float, sqlite3.Row]]:
    scored = []
    seen: set[int] = set()
    variants = topic_query_variants(topic)
    for row in rows:
        article_id = int(row["article_id"])
        if article_id in seen:
            continue
        seen.add(article_id)
        comparison = f"{row['title']} {clean_snippet(row['snippet'], 120)}"
        score = max(normalized_similarity(variant, comparison) for variant in variants)
        scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:limit]


def staff_topic_body(root: Path, topic: str, rows: list[sqlite3.Row]) -> str:
    scored = topic_similarity_rows(topic, rows, 10)
    max_score = scored[0][0] if scored else 0.0
    if max_score >= 0.72:
        conclusion = "高度相近，建议不要直接沿用原题；需要换角度、换材料或换问题意识。"
    elif max_score >= 0.45:
        conclusion = "存在相近历史题目，建议做差异化处理。"
    elif rows:
        conclusion = "检索到相关材料，但暂未发现明显撞题；仍需人工确认午间盟史课堂既往目录。"
    else:
        conclusion = "未检索到可靠相近题目，不能据此判断未写过，应补充课堂目录后复核。"
    similar_lines = []
    for idx, (score, row) in enumerate(scored, 1):
        similar_lines.append(
            f"| T{idx} | {score:.0%} | {row['published_at'] or '日期不详'} | "
            f"{row['account'] or ''} | 《{row['title']}》 | `{row['raw_path']}` |"
        )
    return f"""# 盟参 /题：{topic}

## 结论

- 查重判断：{conclusion}
- 当前依据为本地文章库相似检索，命中 {len(rows)} 条片段；如果有完整“午间盟史课堂”目录，应以后续专门目录为最高优先级。
- 相似度是辅助指标，不替代人工判断。

## 素材

### 近似历史篇目

| 编号 | 相似度 | 日期 | 公众号 | 标题 | raw 原文 |
|---|---:|---|---|---|---|
{chr(10).join(similar_lines) if similar_lines else '| - | - | - | - | 未检索到可靠来源 | - |'}

### 可差异化角度

- 换问题：从“人物生平”改为“一个关键抉择/一处历史现场/一段组织关系”。
- 换材料：优先寻找上海地方史材料、档案线索、旧址地点或人物口述线索。
- 换体例：课堂稿可做“史实讲清 + 今日传承 + 一个可记住的细节”，避免复述旧稿。

### 来源摘录

{cited_excerpts(rows, 8)}

## 风险提示

{staff_risk_lines(root, topic, rows)}
"""


def staff_info_body(root: Path, topic: str, rows: list[sqlite3.Row]) -> str:
    formulations = match_staff_items(load_formulations(root), topic)
    external_refs = external_reference_block(root, topic)
    return f"""# 盟参 /信：{topic}

## 结论

- 本次为“信息/参政议政素材包”，已检索到 {len(rows)} 条片段，覆盖 {len({int(row['article_id']) for row in rows}) if rows else 0} 篇来源文章。
- 输出用于起草统战信息、社情民意、调研综述或参政议政报道的前期资料，不是最终建议稿。
- 可以先按“问题发现 -> 调研依据 -> 对策建议 -> 履职价值 -> 风险核验”组织材料。
- 具体数据、政策表述、办理结果和对策可行性必须另行核验，无法证明的内容标 `[待核]`。

## 素材

### 同题历史材料

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{citation_table(rows)}

### Drive 外部参考层

{external_refs}

### 证据摘录

{cited_excerpts(rows, 10)}

### 问题发现素材

- 从来源中提取真实问题、对象范围、场景和影响，不把活动报道直接改写成问题判断。
- 优先寻找“调研、提案、社情民意、建议、办理、成效、反馈”等明确履职线索。
- 如果只有会议或活动报道，可作为背景材料，不能直接推出政策建议。[待核]

### 对策建议骨架

- 建议一：围绕机制、流程、资源配置或协同治理提出可操作建议。[待按材料核]
- 建议二：围绕试点、评估、数据共享、人才支撑或服务保障提出延展建议。[待按材料核]
- 建议三：围绕民主党派履职、专家资源和基层观察提出民盟特色角度。[待按材料核]

### 写作结构

- 标题：点明问题对象或建议方向，避免空泛口号。
- 开头：交代调研背景、问题来源或现实场景。
- 主体：按“问题表现、原因分析、已有做法、对策建议”展开。
- 结尾：落到服务中心大局、提升治理效能或发挥民盟界别优势。

### 口径要点

{staff_formulation_lines(formulations)}

## 风险提示

{staff_risk_lines(root, topic, rows)}
"""


def staff_stats_matches(labels: list[dict], query: str) -> list[dict]:
    terms = [term for term in re.split(r"\s+", query.strip()) if term]
    if not terms:
        return labels
    year_terms = {term for term in terms if re.fullmatch(r"\d{4}", term)}
    text_terms = [term for term in terms if term not in year_terms]
    matches = []
    for label in labels:
        if year_terms and str(label.get("year") or "") not in year_terms:
            continue
        haystack = " ".join(
            [
                str(label.get("title") or ""),
                str(label.get("account") or ""),
                str(label.get("year") or ""),
                str(label.get("article_type_name") or ""),
                " ".join(label.get("topic_tags") or []),
                " ".join(label.get("people") or []),
            ]
        )
        if all(term in haystack for term in text_terms):
            matches.append(label)
    if matches:
        return matches
    return [
        label for label in labels
        if (not year_terms or str(label.get("year") or "") in year_terms)
        if any(term in " ".join([
            str(label.get("title") or ""),
            str(label.get("account") or ""),
            str(label.get("year") or ""),
            str(label.get("article_type_name") or ""),
            " ".join(label.get("topic_tags") or []),
        ]) for term in text_terms)
    ]


def counter_table(counter: Counter, left: str, right: str, limit: int = 20) -> str:
    rows = [[left, right]]
    rows.extend([[str(key), str(value)] for key, value in counter.most_common(limit)])
    return markdown_table(rows)


def staff_stats_body(root: Path, topic: str) -> str:
    labels = load_article_labels(root)
    matches = staff_stats_matches(labels, topic)
    by_account = Counter(label.get("account") or "unknown" for label in matches)
    by_year = Counter(label.get("year") or "unknown" for label in matches)
    by_type = Counter(label.get("article_type_name") or "unknown" for label in matches)
    topics = Counter()
    for label in matches:
        topics.update(label.get("topic_tags") or [])
    recent_rows = [["日期", "账号", "类型", "标题", "raw 原文"]]
    for item in sorted(matches, key=lambda label: label.get("published_at") or "", reverse=True)[:20]:
        recent_rows.append([
            item.get("published_at") or "日期不详",
            item.get("account") or "",
            item.get("article_type_name") or "",
            f"《{item.get('title') or ''}》",
            f"`{item.get('raw_path') or ''}`",
        ])
    return f"""# 盟参 /数：{topic}

## 结论

- 当前按文章标签库统计，命中 {len(matches)} 篇文章；全库标签总量 {len(labels)} 篇。
- 统计结果用于选题研判、素材覆盖分析和人工校订优先级判断，不替代逐篇阅读。
- 如果命中量异常偏大或偏小，应拆分关键词重新统计。

## 素材

### 按账号分布

{counter_table(by_account, "账号", "篇数")}

### 按年份分布

{markdown_table([["年份", "篇数"]] + [[str(k), str(v)] for k, v in sorted(by_year.items(), reverse=True)])}

### 按体裁分布

{counter_table(by_type, "体裁", "篇数")}

### 高频主题

{counter_table(topics, "主题", "篇数")}

### 最近样本

{markdown_table(recent_rows)}

## 风险提示

- 统计基于机器标签，低置信和交叉体裁文章需要结合 `微信公众号分类优先校订清单.md` 人工复核。
- 选题空白不能只凭“未命中”判断，需补充关键词、同义词和人工经验。
"""


def staff_check_body(root: Path, text: str, rows: list[sqlite3.Row]) -> str:
    issues = staff_check_issues(root, text)
    blocker_count = sum(1 for item in issues if item.get("severity") in {"blocker", "high"})
    if blocker_count:
        conclusion = f"发现 {blocker_count} 个必须优先处理的高风险问题，正式使用前不能放行。"
    elif issues:
        conclusion = f"发现 {len(issues)} 个提示/中风险问题，需人工复核后再使用。"
    else:
        conclusion = "未发现当前种子黑名单命中；这不等于已经通过史实、口径和文字终审。"
    return f"""# 盟参 /核

## 结论

- {conclusion}
- 本次为机器预审，只覆盖种子黑名单、来源缺失和部分史实风险；正式发文仍需人工终审。
- 涉及红头文件、内部口径、领导职务、组织名称和历史争议时，以最新权威口径为准。

## 素材

### 问题清单

| 编号 | 严重度 | 类型 | 命中内容 | 建议处理 |
|---|---|---|---|---|
{issue_table(issues)}

### 本地参考来源

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{citation_table(rows)}

### 参考摘录

{cited_excerpts(rows, 6)}

## 风险提示

- 无出处的事实性表述一律按 [待核] 处理。
- 种子黑名单还不是完整审稿规范；没有命中不代表没有问题。
- 建盟日期、组织名称、人物职务、历史会议和地点类内容，应逐条回 raw 原文和权威资料核验。
"""


def command_staff(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    if args.staff_command == "check":
        text = ""
        if args.file:
            path = Path(args.file).expanduser()
            if not path.exists():
                print(f"staff check file not found: {path}", file=sys.stderr)
                return 2
            text = path.read_text(encoding="utf-8")
        elif args.text:
            text = " ".join(args.text)
        if not text.strip():
            print("staff check requires pasted text or --file", file=sys.stderr)
            return 2
        query = text[:160]
        rows = search_rows(root, staff_query("check", query), args.top_k)
        body = staff_check_body(root, text, rows)
        title = f"盟参核稿：{slugify(text[:30], 30)}"
    else:
        topic = args.topic.strip()
        rows = staff_search_rows(root, args.staff_command, topic, args.top_k)
        if args.staff_command == "draft":
            material = ""
            if getattr(args, "file", None):
                path = Path(args.file).expanduser()
                if not path.exists():
                    print(f"staff draft file not found: {path}", file=sys.stderr)
                    return 2
                material = path.read_text(encoding="utf-8")
            elif getattr(args, "material", None):
                material = " ".join(args.material)
            body = staff_material_draft_body(root, topic, material, rows) if material.strip() else staff_draft_body(root, topic, rows)
            title = f"盟参文稿素材：{topic}"
        elif args.staff_command == "history":
            body = staff_history_body(root, topic, rows)
            title = f"盟参史实卡：{topic}"
        elif args.staff_command == "topic":
            body = staff_topic_body(root, topic, rows)
            title = f"盟参选题查重：{topic}"
        elif args.staff_command == "info":
            body = staff_info_body(root, topic, rows)
            title = f"盟参信息素材：{topic}"
        elif args.staff_command == "stats":
            body = staff_stats_body(root, topic)
            title = f"盟参统计看板：{topic}"
            rows = []
        else:
            print(f"unknown staff command: {args.staff_command}", file=sys.stderr)
            return 2
    if args.save:
        path = write_wiki_page(root, title, "assistant", body, rows)
        append_wiki_log(root, f"盟参生成页面：{path.relative_to(root)}")
        print(f"Saved: {path}\n")
    print(body)
    log_operation(
        root,
        "staff",
        "ok",
        f"{args.staff_command}: {len(rows)} sources",
        {"staff_command": args.staff_command, "sources": len(rows)},
    )
    return 0


ARTICLE_TYPE_RULES = [
    {"type": "meeting_report", "name": "会议报道", "keywords": ["会议", "全委", "常委会", "主委会议", "代表大会", "座谈会", "开题会", "推进会", "工作会", "学习交流会"]},
    {"type": "activity_report", "name": "活动报道", "keywords": ["活动", "举行", "举办", "开展", "走进", "启动", "参观", "调研", "培训班", "讲座", "比赛"]},
    {"type": "leadership_speech", "name": "领导讲话/工作部署", "keywords": ["讲话", "工作报告", "工作要点", "部署", "要求", "指出", "强调", "主委会议通过", "全会"]},
    {"type": "member_achievement", "name": "盟员履职/成果荣誉", "keywords": ["祝贺", "荣获", "获得", "获", "获评", "获奖", "获颁", "获表彰", "表彰", "提名奖", "喜获", "当选", "入选", "成果", "团队", "院士", "科学技术奖", "五一劳动奖章", "创新争先", "典型在身边", "履职风采"]},
    {"type": "cultural_showcase", "name": "文化作品/展示传播", "keywords": ["盟员美术", "美术大师", "美术家", "作品集萃", "作品赏析", "书画作品", "笔下", "原创歌曲", "MV", "夜听", "劳动最光荣", "歌声", "共唱", "词曲", "主唱", "线上展", "以笔寄愿", "我和我的祖国", "我和我的民盟", "接力送祝福"]},
    {"type": "person_profile", "name": "人物采访/人物风采", "keywords": ["人物", "采访", "风采", "盟员风采", "专访", "故事", "诞辰", "纪念", "先生", "代表访谈微视频", "科考记", "被央视报道", "这位盟员", "盟员教师", "盟员医生"]},
    {"type": "history_commemoration", "name": "文史纪念", "keywords": ["盟史", "文史", "纪念", "先贤", "旧政协", "新政协", "五一口号", "李闻", "钩沉", "口述史", "传统教育基地"]},
    {"type": "history_research", "name": "盟史研究", "keywords": ["盟史研究", "民盟历史", "档案", "史料", "史实", "考证", "历史资料", "理论和盟史"]},
    {"type": "policy_advice", "name": "参政议政", "keywords": ["参政议政", "提案", "社情民意", "建言", "调研", "建议", "政协", "两会", "履职"]},
    {"type": "theme_education", "name": "主题教育", "keywords": ["主题教育", "主题教育进行时", "参政为公", "实干为民", "凝心铸魂", "学规定", "强作风", "树形象", "政治共识", "学习贯彻习近平", "学习贯彻中共"]},
    {"type": "organization_building", "name": "组织建设", "keywords": ["组织建设", "基层组织", "支部", "区委", "委员会", "换届", "盟员之家", "新盟员", "入盟"]},
    {"type": "social_service", "name": "社会服务", "keywords": ["社会服务", "帮扶", "乡村振兴", "烛光行动", "黄丝带", "公益", "医疗", "教育帮扶", "抗疫", "战“疫”", "疫灾", "驰援", "小黄人"]},
    {"type": "notice_info", "name": "通知公告/信息发布", "keywords": ["通知", "公告", "预告", "名单", "公示", "目录", "招聘", "征集", "报名", "结果出炉", "倒计时", "正式上线", "节日快乐", "节日祝福", "新年快乐", "元宵节快乐", "拜年", "中秋快乐", "中秋佳节", "国庆", "迎春", "祝广大盟员"]},
    {"type": "commentary_theory", "name": "评论综述/理论文章", "keywords": ["综述", "理论", "评论", "学习体会", "心得", "观察", "解读", "述评"]},
]

ARTICLE_TYPE_NAMES = {item["type"]: item["name"] for item in ARTICLE_TYPE_RULES} | {"other": "其他/待判"}

WRITING_STYLE_GUIDES = {
    "meeting_report": {
        "use": "适合全委会、常委会、主委会、专题座谈会、工作推进会等会议新闻。",
        "structure": "标题点明会议名称或核心任务；导语交代时间、地点、会议主体和议题；主体按领导讲话、会议内容、审议事项、工作要求展开；结尾落到贯彻落实或下一步安排。",
        "title": "常见标题以“召开”“举行”“专题学习”“部署推进”等动词承载事实，避免只写口号。",
        "lead": "首段要一次性回答谁、何时、何地、开什么会、围绕什么主题。",
        "risk": "会议名称、职务排序、参会范围和审议事项必须核对原文或正式通知。",
    },
    "activity_report": {
        "use": "适合调研、培训、走访、参观、交流、讲座、比赛等动态报道。",
        "structure": "标题突出活动动作和对象；导语交代活动基本信息；主体写活动环节、现场交流、成果反馈；结尾写活动意义或后续转化。",
        "title": "常见标题使用“赴”“开展”“举办”“走进”“举行”等动作词，重点放在具体活动而不是抽象表态。",
        "lead": "导语宜短，先给出活动事实，再补充主办单位和参加人员。",
        "risk": "不要把一般活动拔高为制度成果；活动效果要有具体事实支撑。",
    },
    "person_profile": {
        "use": "适合人物采访、盟员风采、先贤纪念、先进典型和口述材料。",
        "structure": "标题突出人物身份或精神特质；开头以人物核心贡献或场景切入；主体按经历、贡献、细节故事、民盟关联展开；结尾回到时代价值或履职启示。",
        "title": "常见标题会用人物姓名加身份、贡献或一句代表性表述，增强识别度。",
        "lead": "导语可用一个典型细节引入，但必须尽快交代人物与民盟、上海或主题的关系。",
        "risk": "生卒年、职务、入盟时间、历史评价和引语必须有出处；避免把文学化描写写成史实。",
    },
    "cultural_showcase": {
        "use": "适合作品赏析、书画展、原创歌曲、视频展播、节庆征集和文化传播类稿件。",
        "structure": "标题突出作品形态或传播主题；导语说明作品来源、作者或活动背景；主体介绍作品内容、人物身份、创作背景和传播效果；结尾落到文化凝聚或民盟特色。",
        "title": "常见标题会出现“作品”“笔下”“夜听”“原创歌曲”“我和我的祖国”等提示，风格可比会议报道更活泼。",
        "lead": "导语要先交代作品或展播对象，不宜只写情绪性祝福。",
        "risk": "作品作者、版权、奖项、展览名称和人物身份要核对；节庆问候类不宜误写成人物专访。",
    },
    "policy_advice": {
        "use": "适合参政议政、社情民意、提案建议、调研成果和履职综述。",
        "structure": "标题点出议题和履职动作；导语说明调研或建言背景；主体按问题发现、调研依据、建议内容、办理反馈或社会价值展开。",
        "title": "常见标题包含“建言”“调研”“提案”“社情民意”“助力”等关键词。",
        "lead": "导语应把议题、履职主体和建言场景说清楚，不宜直接堆政策概念。",
        "risk": "政策判断和数据要注明来源；建议表述要可操作，避免空泛口号。",
    },
    "theme_education": {
        "use": "适合主题教育、政治学习、思想共识、作风建设和专题学习类稿件。",
        "structure": "标题通常围绕主题和行动；导语写学习背景和组织方式；主体写学习内容、交流发言、实践转化；结尾强调凝聚共识和履职实效。",
        "title": "常见标题会包含“主题教育”“凝心铸魂”“学规定、强作风、树形象”等规范表述。",
        "lead": "导语应先写活动或会议事实，再写学习主题，避免只有抽象政治表述。",
        "risk": "政治表述必须使用现行规范口径；引用上级部署时要核对原文。",
    },
    "history_commemoration": {
        "use": "适合文史纪念、盟史传播、先贤故事、纪念活动和传统教育基地介绍。",
        "structure": "标题点明人物、事件或纪念节点；导语说明历史对象和现实语境；主体按史实脉络、人物贡献、民盟关联、今日启示展开。",
        "title": "常见标题偏庄重，常用“纪念”“回望”“钩沉”“先贤”等词。",
        "lead": "导语要交代时间坐标和史实对象，不宜直接抒情。",
        "risk": "高风险史实必须回到原始来源或权威档案；争议问题要标注待核。",
    },
    "leadership_speech": {
        "use": "适合领导讲话、工作部署、年度工作报告和重要会议精神整理。",
        "structure": "标题突出会议层级或部署主题；导语写讲话场景；主体按肯定成绩、指出问题、提出要求、部署任务展开。",
        "title": "标题应稳健准确，少用修辞，优先体现讲话场景和任务方向。",
        "lead": "导语必须准确写明讲话人、职务、会议和时间。",
        "risk": "领导职务、讲话原意和提法边界必须核对，不要二次发挥。",
    },
    "member_achievement": {
        "use": "适合盟员获奖、成果发布、入选名单、履职成果和先进典型报道。",
        "structure": "标题点明获奖或成果事实；导语说明人物/团队、奖项和时间；主体补充成果背景、专业贡献、民盟身份和社会影响。",
        "title": "常见标题使用“荣获”“入选”“获评”“成果”等事实词，信息密度高。",
        "lead": "导语先讲清楚谁获得了什么，再补充评选单位或成果领域。",
        "risk": "奖项名称、层级、授予单位和人物身份必须逐字核对。",
    },
    "organization_building": {
        "use": "适合基层组织、支部活动、换届、新盟员、盟员之家等组织建设稿件。",
        "structure": "标题点明组织和动作；导语交代组织层级与事项；主体写流程、交流、组织成效和下一步建设。",
        "title": "常见标题包含“基层组织”“支部”“换届”“盟员之家”“新盟员”等组织词。",
        "lead": "导语要明确组织名称和活动性质，避免只写泛泛学习交流。",
        "risk": "组织名称、届次、职务和程序性表述要准确。",
    },
    "social_service": {
        "use": "适合社会服务、帮扶、教育医疗公益、乡村振兴和品牌项目报道。",
        "structure": "标题突出服务对象和行动；导语写服务时间地点与主体；主体写服务内容、受益对象、项目机制和持续效果。",
        "title": "常见标题使用“助力”“帮扶”“公益”“乡村振兴”“烛光行动”等行动词。",
        "lead": "导语应先写具体服务事实，再写价值意义。",
        "risk": "服务成效不能夸大；人数、物资、项目名称等要有来源。",
    },
}

TOPIC_KEYWORDS = {
    "民盟史": ["盟史", "中国民主政团同盟", "旧政协", "新政协", "五一口号", "李闻", "民盟先贤", "传统教育基地"],
    "上海民盟": ["上海民盟", "民盟市委", "民盟上海市委", "上海"],
    "80周年": ["八十", "80周年", "80华诞", "八秩", "华诞"],
    "参政议政": ["参政议政", "提案", "社情民意", "建言", "政协", "履职"],
    "主题教育": ["主题教育", "参政为公", "实干为民", "凝心铸魂"],
    "组织建设": ["组织建设", "基层组织", "支部", "盟员之家", "入盟", "换届"],
    "社会服务": ["社会服务", "帮扶", "乡村振兴", "烛光行动", "黄丝带"],
    "宣传写作": ["宣传", "微信公众号", "上海盟讯", "报道", "讲述者", "讲解员"],
}


def corpus_dir(root: Path) -> Path:
    return root / "index" / "corpus"


def report_dir(root: Path) -> Path:
    return root / "wiki" / "研究助手"


def article_year(published_at: str | None) -> str:
    if published_at and re.match(r"^\d{4}", published_at):
        return published_at[:4]
    return "unknown"


def year_at_least(year: str | None, minimum: str) -> bool:
    return bool(year and re.match(r"^\d{4}$", year) and year >= minimum)


def corpus_text_for_row(row: sqlite3.Row) -> str:
    return " ".join([str(row["title"] or ""), str(row["account"] or ""), str(row["sample_text"] or "")])


def classify_article(title: str, account: str | None, text: str) -> tuple[str, int, list[str]]:
    haystack = f"{title}\n{account or ''}\n{text[:1600]}"
    if title.startswith(("预告", "【预告】", "通知", "公告", "名单", "公示")):
        return "notice_info", 95, ["预告" if "预告" in title[:6] else title[:2]]
    if "《史良》连载" in title:
        return "history_commemoration", 95, ["《史良》连载"]
    theme_title_terms = ["主题教育", "学规定", "强作风", "树形象", "参政为公", "实干为民", "不忘合作初心", "政治交接主题教育"]
    if any(term in title for term in theme_title_terms):
        return "theme_education", 95, [term for term in theme_title_terms if term in title][:3]
    history_title_terms = ["盟史", "民盟先贤", "先贤", "纪念", "诞辰", "五一口号", "旧政协", "新政协", "传统教育基地"]
    if any(term in title for term in history_title_terms):
        return "history_commemoration", 95, [term for term in history_title_terms if term in title][:3]
    scored = []
    for index, rule in enumerate(ARTICLE_TYPE_RULES):
        matched = [kw for kw in rule["keywords"] if kw in haystack]
        if matched:
            title_hits = sum(3 for kw in matched if kw in title)
            score = len(matched) + title_hits
            if rule["type"] == "member_achievement" and any(kw in title for kw in ["祝贺", "获", "入选", "表彰", "当选"]):
                score += 6
            if rule["type"] == "cultural_showcase" and any(kw in title for kw in ["盟员美术", "美术大师", "美术家", "作品", "笔下", "原创歌曲", "MV", "夜听", "劳动最光荣", "我和我的祖国", "我和我的民盟", "共唱", "词曲", "主唱"]):
                score += 6
            if rule["type"] == "person_profile" and any(kw in title for kw in ["代表访谈微视频", "科考记", "这位盟员", "盟员教师", "盟员医生", "被央视报道"]):
                score += 6
            if rule["type"] == "social_service" and any(kw in title for kw in ["抗疫", "战“疫”", "疫灾", "驰援", "小黄人"]):
                score += 6
            if rule["type"] == "theme_education" and any(kw in title for kw in ["主题教育", "凝心铸魂", "参政为公", "实干为民", "学规定", "强作风"]):
                score += 6
            if rule["type"] == "history_commemoration" and any(kw in title for kw in ["盟史钩沉", "民盟先贤", "五一口号", "诞辰", "旧政协", "新政协"]):
                score += 6
            if rule["type"] == "notice_info" and (
                title.startswith(("预告", "通知", "公告", "名单", "公示"))
                or any(kw in title for kw in ["节日快乐", "节日祝福", "新年快乐", "元宵节快乐", "拜年", "中秋快乐", "中秋佳节", "国庆", "迎春", "倒计时", "正式上线"])
            ):
                score += 6
            scored.append((score, -index, rule["type"], matched))
    if not scored:
        return "other", 0, []
    scored.sort(key=lambda item: item[0], reverse=True)
    score, _, article_type, matched = scored[0]
    return article_type, min(95, 50 + score * 8), matched[:8]


def topic_tags_for_text(text: str) -> list[str]:
    return [tag for tag, keywords in TOPIC_KEYWORDS.items() if any(keyword in text for keyword in keywords)]


def people_hits_for_text(text: str) -> list[str]:
    people = ["张澜", "沈钧儒", "黄炎培", "史良", "李公朴", "闻一多", "陶行知", "费孝通", "钱伟长", "陈望道", "苏步青", "谈家桢"]
    return [name for name in people if name in text]


def article_rows_for_corpus(root: Path) -> list[sqlite3.Row]:
    conn = connect_db(root)
    try:
        return conn.execute(
            """
            SELECT a.id, a.title, a.account, a.author, a.published_at, a.source_url, a.raw_path,
                   a.content_hash, a.file_type, a.status,
                   COUNT(c.id) AS chunk_count,
                   COALESCE(SUM(c.token_estimate), 0) AS token_estimate,
                   substr(group_concat(c.content, '\n'), 1, 2400) AS sample_text
            FROM articles a
            LEFT JOIN article_chunks c ON c.article_id = a.id
            GROUP BY a.id
            ORDER BY a.published_at DESC, a.id
            """
        ).fetchall()
    finally:
        conn.close()


def build_article_label(row: sqlite3.Row) -> dict:
    text = corpus_text_for_row(row)
    article_type, confidence, matched = classify_article(str(row["title"] or ""), row["account"], str(row["sample_text"] or ""))
    year = article_year(row["published_at"])
    topics = topic_tags_for_text(text)
    people = people_hits_for_text(text)
    is_history = article_type in {"history_commemoration", "history_research"} or "民盟史" in topics
    is_writing_sample = row["account"] == "上海民盟" and year_at_least(year, "2023") and article_type in {
        "activity_report", "meeting_report", "person_profile", "history_commemoration",
        "policy_advice", "theme_education", "leadership_speech", "member_achievement",
        "organization_building", "social_service", "cultural_showcase",
    }
    return {
        "article_id": int(row["id"]),
        "title": row["title"],
        "account": row["account"],
        "author": row["author"],
        "published_at": row["published_at"],
        "year": year,
        "source_url": row["source_url"],
        "raw_path": row["raw_path"],
        "content_hash": row["content_hash"],
        "file_type": row["file_type"],
        "chunk_count": int(row["chunk_count"] or 0),
        "token_estimate": int(row["token_estimate"] or 0),
        "article_type": article_type,
        "article_type_name": ARTICLE_TYPE_NAMES[article_type],
        "classification_confidence": confidence,
        "matched_keywords": matched,
        "topic_tags": topics,
        "people": people,
        "is_history": is_history,
        "is_writing_sample": is_writing_sample,
        "can_be_formulation_source": row["account"] in {"上海民盟", "中国民主同盟"} and year_at_least(year, "2023"),
        "needs_metadata_review": bool(not row["published_at"] or not row["account"] or not row["raw_path"]),
    }


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


def corpus_audit_markdown(labels: list[dict], created_at: str) -> str:
    total = len(labels)
    by_account = Counter(label["account"] or "unknown" for label in labels)
    by_year = Counter(label["year"] for label in labels)
    by_type = Counter(label["article_type_name"] for label in labels)
    recent_sh = [label for label in labels if label["account"] == "上海民盟" and year_at_least(label["year"], "2023")]
    history_count = sum(1 for label in labels if label["is_history"])
    writing_count = sum(1 for label in labels if label["is_writing_sample"])
    metadata_review = sum(1 for label in labels if label["needs_metadata_review"])
    missing_url = sum(1 for label in labels if not label.get("source_url"))
    return f"""# 微信公众号语料库体检报告

生成时间：{created_at}

## 总体结论

- 当前微信公众号语料库共有 {total} 篇文章。
- 2023 年以后上海民盟文章共有 {len(recent_sh)} 篇，是后续写作体例学习的第一优先层。
- 初步识别文史/盟史相关文章 {history_count} 篇，写作样本候选 {writing_count} 篇。
- 需要元数据复核的文章 {metadata_review} 篇；缺少原文链接的文章 {missing_url} 篇。

## 按账号分布

{markdown_table([["账号", "文章数"]] + [[k, str(v)] for k, v in by_account.most_common()])}

## 按年份分布

{markdown_table([["年份", "文章数"]] + [[k, str(v)] for k, v in sorted(by_year.items(), reverse=True)])}

## 按文章类型分布

{markdown_table([["类型", "文章数"]] + [[k, str(v)] for k, v in by_type.most_common()])}

## 数据质量

- 日期缺失或异常：{sum(1 for label in labels if label['year'] == 'unknown')} 篇。
- 账号缺失：{sum(1 for label in labels if not label.get('account'))} 篇。
- raw 原文路径缺失：{sum(1 for label in labels if not label.get('raw_path'))} 篇。
- source_url 缺失：{missing_url} 篇。

## 下一步

1. 人工抽检每类文章各 20 篇，修正关键词规则。
2. 把 2023 年以后上海民盟写作样本按类型精选。
3. 将文史/盟史文章升级为人物、事件、地点、组织四类研究入口。
4. 将高可信文章沉淀为口径库来源，供 `/核` 和 `/稿` 调用。
"""


def type_system_markdown(created_at: str) -> str:
    rows = [["类型代码", "类型名称", "主要识别词"]]
    for rule in ARTICLE_TYPE_RULES:
        rows.append([rule["type"], rule["name"], "、".join(rule["keywords"][:12])])
    return f"""# 微信公众号文章分类体系 v0.1

生成时间：{created_at}

本分类体系服务于民盟微信公众号语料库，优先解决“文章是什么、可用于什么任务、是否可作为写作样本或口径来源”的问题。

## 类型表

{markdown_table(rows)}

## 使用原则

- 第一版采用可解释规则分类，所有标签都允许后续人工修订。
- 一篇文章先给一个主类型，同时保留主题词、人物、是否文史类、是否写作样本等辅助标签。
- 上海民盟 2023 年以后文章优先进入写作体例样本库。
- 中国民主同盟、群言杂志的文史类文章优先进入盟史研究和事实核验参考层。
- 正式口径仍以红头文件、内部口径和人工终审为准。
"""


def writing_samples_markdown(labels: list[dict], created_at: str, limit_per_type: int = 30) -> str:
    samples = [label for label in labels if label["account"] == "上海民盟" and year_at_least(label["year"], "2023") and label["is_writing_sample"]]
    by_type: dict[str, list[dict]] = {}
    for label in sorted(samples, key=lambda item: item["published_at"] or "", reverse=True):
        by_type.setdefault(label["article_type_name"], []).append(label)
    sections = []
    for type_name, items in sorted(by_type.items()):
        rows = [["日期", "标题", "主题词", "raw 原文"]]
        for item in items[:limit_per_type]:
            rows.append([item["published_at"] or "日期不详", f"《{item['title']}》", "、".join(item["topic_tags"][:4]) or "待补", f"`{item['raw_path']}`"])
        sections.append(f"## {type_name}\n\n{markdown_table(rows)}")
    return f"""# 上海民盟 2023 年以后写作样本库

生成时间：{created_at}

本页从上海民盟 2023 年以后的微信公众号文章中自动抽取写作样本候选。它用于后续提炼标题、导语、结构、常用表达和风险点。

## 总览

- 样本候选：{len(samples)} 篇。
- 覆盖类型：{len(by_type)} 类。
- 每类最多展示 {limit_per_type} 篇；完整标签见 `index/corpus/article_labels.jsonl`。

{chr(10).join(sections) if sections else '暂无样本。'}

## 后续人工校订

- 每类先精选 20 篇高质量样本。
- 标注标题方式、导语方式、段落结构、结尾落点和可复用表达。
- 将不适合作为风格样本的短讯、通知、转载类文章剔除。
"""


TYPE_SAMPLE_SIGNALS = {
    "meeting_report": ["召开", "会议", "常委", "主委", "座谈会", "推进会", "专题协商"],
    "activity_report": ["开展", "举行", "举办", "赴", "走进", "调研", "培训"],
    "person_profile": ["专访", "这位盟员", "他说", "她", "故事", "面对面", "风采"],
    "history_commemoration": ["盟史", "纪念", "先贤", "诞辰", "追思", "回望", "传统"],
    "policy_advice": ["建言", "提案", "社情民意", "调研", "两会", "金点子", "履职"],
    "theme_education": ["主题教育", "参政为公", "实干为民", "学规定", "强作风", "树形象", "凝心铸魂"],
    "leadership_speech": ["工作要点", "讲话", "部署", "要求", "机关建设", "工作报告"],
    "member_achievement": ["祝贺", "荣获", "获奖", "入选", "表彰", "当选", "捷报"],
    "organization_building": ["基层组织", "换届", "支部", "盟员之家", "新盟员", "组织"],
    "social_service": ["社会服务", "帮扶", "乡村振兴", "名医", "公益", "服务"],
    "cultural_showcase": ["作品", "展", "书画", "画笔", "原创", "艺术", "非遗"],
}


def writing_sample_score(label: dict) -> tuple[int, list[str]]:
    score = int(label.get("classification_confidence") or 0)
    reasons = [f"置信度{score}"]
    title = label.get("title") or ""
    year = label.get("year") or ""
    token_estimate = int(label.get("token_estimate") or 0)
    if year_at_least(year, "2025"):
        score += 12
        reasons.append("近两年样本")
    elif year_at_least(year, "2023"):
        score += 6
        reasons.append("2023年以来")
    if 500 <= token_estimate <= 5000:
        score += 10
        reasons.append("篇幅适中")
    elif token_estimate > 5000:
        score += 4
        reasons.append("长稿可拆解")
    signals = [term for term in TYPE_SAMPLE_SIGNALS.get(label.get("article_type") or "", []) if term in title]
    if signals:
        score += min(18, len(signals) * 6)
        reasons.append("标题体裁信号:" + "、".join(signals[:3]))
    if any(term in title for term in ["预告", "通知", "公告", "名单", "公示", "节日快乐"]):
        score -= 40
        reasons.append("偏信息发布，降权")
    return score, reasons


def curated_writing_samples_markdown(labels: list[dict], created_at: str, limit_per_type: int = 8) -> str:
    samples = [label for label in labels if label["account"] == "上海民盟" and year_at_least(label["year"], "2023") and label["is_writing_sample"]]
    by_type: dict[str, list[dict]] = {}
    scored_samples = []
    for label in samples:
        score, reasons = writing_sample_score(label)
        scored = {**label, "sample_score": score, "sample_reasons": reasons}
        scored_samples.append(scored)
        by_type.setdefault(label["article_type"], []).append(scored)

    sections = []
    total_selected = 0
    for type_code, guide in WRITING_STYLE_GUIDES.items():
        items = sorted(by_type.get(type_code, []), key=lambda item: (-int(item["sample_score"]), item.get("published_at") or "", int(item["article_id"])))[:limit_per_type]
        total_selected += len(items)
        rows = [["分数", "日期", "标题", "入选理由", "raw 原文"]]
        for item in items:
            rows.append(
                [
                    str(item["sample_score"]),
                    item["published_at"] or "日期不详",
                    f"《{item['title']}》",
                    "；".join(item["sample_reasons"][:4]),
                    f"`{item['raw_path']}`",
                ]
            )
        sections.append(
            f"""## {ARTICLE_TYPE_NAMES.get(type_code, type_code)}

- 样本用途：{guide["use"]}
- 选样重点：优先选择标题体裁清楚、篇幅适中、2023 年以后且分类置信度较高的上海民盟文章。

{markdown_table(rows) if items else '暂无精选样本。'}
"""
        )

    return f"""# 上海民盟微信公众号精选写作样本

生成时间：{created_at}

本页从 `上海民盟2023年以来写作样本库.md` 中再筛一层，作为后续写稿时优先模仿的代表样本。它不是最终人工定稿清单，但比全量候选库更适合直接调用。

## 总览

- 候选样本：{len(samples)} 篇。
- 精选样本：{total_selected} 篇。
- 覆盖体裁：{sum(1 for items in by_type.values() if items)} 类。
- 每类最多展示 {limit_per_type} 篇。

## 选样规则

- 优先上海民盟 2023 年以后文章。
- 优先体裁信号明确、标题可借鉴、篇幅适中的文章。
- 降权通知、预告、公示、节庆问候等不适合作为风格模板的文章。
- 正式写稿仍需回 raw 原文核对事实、职务、数据和口径。

{chr(10).join(sections)}

## 使用办法

1. 先判断你给的材料属于哪种体裁。
2. 到本页对应体裁下选 3-5 篇 raw 原文作为风格参照。
3. 再结合 `上海民盟微信公众号分体裁写作模板.md` 输出初稿。
4. 最后用 `/核` 做事实、口径和错别字检查。
"""


def writing_style_templates_markdown(labels: list[dict], created_at: str, limit_per_type: int = 12) -> str:
    samples = [label for label in labels if label["account"] == "上海民盟" and year_at_least(label["year"], "2023") and label["is_writing_sample"]]
    by_type: dict[str, list[dict]] = {}
    for label in sorted(samples, key=lambda item: item["published_at"] or "", reverse=True):
        by_type.setdefault(label["article_type"], []).append(label)
    sections = []
    for type_code, guide in WRITING_STYLE_GUIDES.items():
        items = by_type.get(type_code, [])
        rows = [["日期", "标题", "主题词", "raw 原文"]]
        for item in items[:limit_per_type]:
            rows.append(
                [
                    item["published_at"] or "日期不详",
                    f"《{item['title']}》",
                    "、".join(item["topic_tags"][:4]) or "待补",
                    f"`{item['raw_path']}`",
                ]
            )
        sample_table = markdown_table(rows) if items else "暂无 2023 年以后上海民盟样本。"
        sections.append(
            f"""## {ARTICLE_TYPE_NAMES.get(type_code, type_code)}

- 适用场景：{guide["use"]}
- 常用结构：{guide["structure"]}
- 标题习惯：{guide["title"]}
- 导语写法：{guide["lead"]}
- 风险提示：{guide["risk"]}
- 当前样本数：{len(items)} 篇。

### 代表样本

{sample_table}
"""
        )
    return f"""# 上海民盟微信公众号分体裁写作模板

生成时间：{created_at}

本页把 2023 年以后上海民盟微信公众号文章，按体裁沉淀为可调用的写作模板。后续你给材料时，可以先判断材料属于哪一类，再套用对应结构生成初稿。

## 使用方式

- 事件、调研、培训、参观优先走“活动报道”。
- 全委会、常委会、座谈会、推进会优先走“会议报道”。
- 采访、风采、纪念人物优先走“人物采访/人物风采”。
- 提案、社情民意、调研建议优先走“参政议政”。
- 主题教育、政治学习、作风建设优先走“主题教育”。
- 文史纪念和盟史传播先做史实核验，再进入写作。

## 总览

- 当前纳入写作样本：{len(samples)} 篇。
- 覆盖模板：{len(WRITING_STYLE_GUIDES)} 类。
- 每类展示最近 {limit_per_type} 篇代表样本。

{chr(10).join(sections)}

## 写作红线

- 事实先行：没有材料支撑的成果、评价、数字和历史结论标 `[待核]`。
- 口径优先：红头文件、内部口径和人工终审高于公众号公开表述。
- 风格服从体裁：新闻稿重事实顺序，人物稿重细节和贡献，文史稿重出处和史实边界。
"""


def shanghai_style_rule_card_markdown(labels: list[dict], created_at: str) -> str:
    samples = [label for label in labels if label["account"] == "上海民盟" and year_at_least(label["year"], "2023") and label["is_writing_sample"]]
    by_type = Counter(label["article_type_name"] for label in samples)
    recent = sorted(samples, key=lambda item: item["published_at"] or "", reverse=True)
    representative_rows = [["体裁", "近年样本数", "优先写法"]]
    rule_map = {
        "会议报道": "标题点明会议名称；导语交代时间、地点、会议主体和议题；主体按会议议程、讲话要点、审议事项和工作要求展开。",
        "活动报道": "导语先给完整事实；主体写活动流程、现场交流和实际成果；结尾落到履职、服务、传承或下一步工作。",
        "人物采访/人物风采": "标题突出人物身份或贡献；开头用具体场景进入；主体用经历、细节、观点和贡献支撑人物形象。",
        "参政议政": "围绕问题、调研、建议、办理或成效组织；避免只写活动过程，必须凸显履职价值。",
        "主题教育": "突出政治学习、组织落实、交流研讨和作风建设；表述要稳，不夸张拔高。",
        "文史纪念": "先核史实和出处；按人物、事件、地点或文献组织；历史评价要有来源边界。",
        "盟史研究": "先列来源和争议，再组织时间线、人物关系和历史评价；无法证明的判断标注待核。",
        "组织建设": "写清组织层级、换届或建设动作；主体呈现程序、交流、组织活力和后续工作。",
        "社会服务": "先写服务对象和具体行动；再写专业优势、社会效果和民盟特色。",
        "盟员履职/成果荣誉": "突出成果事实、人物身份和专业贡献；避免把个人荣誉泛化成组织结论。",
        "领导讲话/工作部署": "先交代会议或活动场景，再提炼讲话要点、部署要求和落实方向；避免脱离来源扩写。",
        "文化作品/展示传播": "先呈现作品、展览或传播事实，再写作者身份、主题表达和社会反响。",
    }
    for type_name, count in by_type.most_common():
        representative_rows.append([type_name, str(count), rule_map.get(type_name, "按标题、导语、事实主体、意义落点和风险核验组织。")])
    source_rows = [["日期", "体裁", "标题", "raw 原文"]]
    for item in recent[:20]:
        source_rows.append([
            item["published_at"] or "日期不详",
            item["article_type_name"],
            f"《{item['title']}》",
            f"`{item['raw_path']}`",
        ])
    return f"""# 上海民盟微信公众号写作风格规则卡

生成时间：{created_at}

本页把 2023 年以来上海民盟微信公众号写作样本压缩成“可直接执行”的规则卡。它服务于 `/稿`，用于收到活动材料、人物材料、讲话材料后快速判断体裁和组织结构。

## 总体判断

- 当前近年上海民盟写作样本：{len(samples)} 篇。
- 覆盖体裁：{len(by_type)} 类。
- 正式写稿时，优先同时查看本页、`上海民盟微信公众号精选写作样本.md` 和 `上海民盟微信公众号分体裁写作模板.md`。

## 通用风格

- 标题：直接点明主体、事件、成果或人物，不使用过度文学化标题替代事实。
- 导语：第一段交代时间、地点、主体、事项和主题，尽量一次说清新闻事实。
- 主体：按事实顺序组织，常见顺序是背景、现场、讲话/观点、成果、下一步。
- 表述：多用稳健、规范、组织化表达；少用无来源的宏大评价。
- 结尾：落到民盟履职、优良传统、组织建设、主题教育、社会服务或下一步工作。
- 风险：职务、会议名称、机构名称、历史年份、数字和评价必须回材料或 raw 原文核验。

## 分体裁规则

{markdown_table(representative_rows)}

## 最近可参照样本

{markdown_table(source_rows)}

## 使用方法

1. 先判断材料属于会议、活动、人物、参政议政、主题教育、文史纪念还是组织建设。
2. 按本页分体裁规则确定标题、导语和主体顺序。
3. 到精选样本库选 3 篇同体裁 raw 原文对照语气和段落长度。
4. 初稿完成后，用 `/核` 检查口径、错字、史实和引用。
"""


def policy_advice_material_index_markdown(labels: list[dict], created_at: str, limit: int = 80) -> str:
    title_terms = ["参政议政", "调研", "提案", "社情民意", "建言", "建议", "民主监督", "专项监督", "履职风采", "课题"]
    items = [
        label for label in labels
        if label["article_type"] == "policy_advice" or any(term in str(label.get("title") or "") for term in title_terms)
    ]
    by_account = Counter(label["account"] or "unknown" for label in items)
    by_year = Counter(label["year"] for label in items)
    by_topic = Counter()
    for item in items:
        by_topic.update(item.get("topic_tags") or [])
    recent_rows = [["日期", "账号", "类型", "主题词", "标题", "raw 原文"]]
    for item in sorted(items, key=lambda label: label["published_at"] or "", reverse=True)[:limit]:
        recent_rows.append([
            item["published_at"] or "日期不详",
            item["account"] or "",
            item["article_type_name"],
            "、".join(item.get("topic_tags") or []) or "待补",
            f"《{item['title']}》",
            f"`{item['raw_path']}`",
        ])
    topic_rows = [["主题", "文章数"]]
    topic_rows.extend([[topic, str(count)] for topic, count in by_topic.most_common(20)])
    return f"""# 微信公众号参政议政素材主题库

生成时间：{created_at}

本页把微信公众号语料中的参政议政、调研、提案、社情民意和履职线索集中起来，服务 `/信`、参政议政报道和统战信息起草。

## 总览

- 参政议政候选文章：{len(items)} 篇。
- 这些材料只能作为公开报道层线索；正式形成信息、提案或建议时，必须另行补充调研事实、数据、政策依据和办理反馈。

## 按账号分布

{markdown_table([["账号", "篇数"]] + [[k, str(v)] for k, v in by_account.most_common()])}

## 按年份分布

{markdown_table([["年份", "篇数"]] + [[k, str(v)] for k, v in sorted(by_year.items(), reverse=True)])}

## 高频主题

{markdown_table(topic_rows)}

## 最近素材

{markdown_table(recent_rows)}

## 使用方法

1. 先用本页判断同类主题是否已有报道基础。
2. 再用 `/信 主题` 生成问题、依据、对策的素材包。
3. 如果来源只是活动报道，只能当背景，不能直接推导政策建议。
4. 涉及数据、政策条文、部门职责、办理结果和建议可行性时，统一标 `[待核]`，并回到正式材料核验。
"""


def history_research_entry_markdown(labels: list[dict], created_at: str, limit_per_group: int = 40) -> str:
    items = [label for label in labels if label["is_history"]]
    by_account = Counter(label["account"] or "unknown" for label in items)
    by_type = Counter(label["article_type_name"] for label in items)
    people = Counter()
    topics = Counter()
    for item in items:
        people.update(item.get("people") or [])
        topics.update(item.get("topic_tags") or [])

    recent_rows = [["日期", "账号", "类型", "标题", "人物", "raw 原文"]]
    for item in sorted(items, key=lambda label: label["published_at"] or "", reverse=True)[:limit_per_group]:
        recent_rows.append(
            [
                item["published_at"] or "日期不详",
                item["account"] or "",
                item["article_type_name"],
                f"《{item['title']}》",
                "、".join(item["people"][:4]) or "待抽取",
                f"`{item['raw_path']}`",
            ]
        )

    people_rows = [["人物", "命中文章数"]]
    people_rows.extend([[name, str(count)] for name, count in people.most_common(30)])
    topic_rows = [["主题", "命中文章数"]]
    topic_rows.extend([[name, str(count)] for name, count in topics.most_common(20)])

    return f"""# 微信公众号文史盟史研究入口清单

生成时间：{created_at}

本页是文史/盟史研究的入口页，用于把上海民盟、中国民主同盟、群言杂志中的文史候选文章先集中起来，再逐步升级成人物卡、事件卡、机构卡和地点卡。

## 总览

- 文史/盟史候选文章：{len(items)} 篇。
- 按账号分布：

{markdown_table([["账号", "篇数"]] + [[k, str(v)] for k, v in by_account.most_common()])}

- 按类型分布：

{markdown_table([["类型", "篇数"]] + [[k, str(v)] for k, v in by_type.most_common()])}

## 高频人物线索

{markdown_table(people_rows)}

## 高频主题线索

{markdown_table(topic_rows)}

## 最近候选文章

{markdown_table(recent_rows)}

## 下一步研究法

1. 先按人物建立研究卡，尤其是沈钧儒、史良、张澜、黄炎培、费孝通等核心人物。
2. 再按事件建立专题线索，如建盟、旧政协、五一口号、李闻事件、新政协等。
3. 上海地方史材料单列，避免与全国民盟史混写。
4. 对生卒年、任职、会议日期、机构名称等事实字段逐条回 raw 原文核验。
5. 有争议或口径风险的条目进入 `index/blacklist.csv` 或 `index/formulations.jsonl`。
"""


def history_corpus_markdown(labels: list[dict], created_at: str, limit: int = 120) -> str:
    items = [label for label in labels if label["is_history"]]
    rows = [["日期", "账号", "类型", "标题", "人物", "raw 原文"]]
    for item in sorted(items, key=lambda label: label["published_at"] or "", reverse=True)[:limit]:
        rows.append([item["published_at"] or "日期不详", item["account"] or "", item["article_type_name"], f"《{item['title']}》", "、".join(item["people"][:4]) or "待抽取", f"`{item['raw_path']}`"])
    return f"""# 微信公众号文史盟史文章专题库

生成时间：{created_at}

本页汇总由规则初筛出的文史/盟史相关文章。当前为候选库，适合后续升级为人物、事件、地点、组织研究卡。

## 总览

- 候选文章：{len(items)} 篇。
- 本页展示最近 {min(limit, len(items))} 篇。

{markdown_table(rows)}

## 后续处理

- 对核心人物、事件、地点做二次实体标注。
- 区分全国民盟史主线、上海地方史线索和公众号纪念性表述。
- 有争议或高风险史实进入口径库和黑名单。
"""


def corpus_dashboard_markdown(labels: list[dict], created_at: str) -> str:
    total = len(labels)
    shanghai_recent = [label for label in labels if label["account"] == "上海民盟" and year_at_least(label["year"], "2023")]
    writing_samples = [label for label in labels if label["is_writing_sample"]]
    history_items = [label for label in labels if label["is_history"]]
    formulation_sources = [label for label in labels if label["can_be_formulation_source"]]
    priority_rows = corpus_priority_review_rows(labels, limit=20)

    by_account = Counter(label["account"] or "unknown" for label in labels)
    by_type = Counter(label["article_type_name"] for label in labels)
    shanghai_by_type = Counter(label["article_type_name"] for label in shanghai_recent)
    history_by_account = Counter(label["account"] or "unknown" for label in history_items)
    writing_by_type = Counter(label["article_type_name"] for label in writing_samples)
    by_year = Counter(label["year"] for label in labels)

    account_rows = [["账号", "总文章", "文史/盟史", "可作近期口径来源"]]
    for account, count in by_account.most_common():
        account_rows.append(
            [
                account,
                str(count),
                str(sum(1 for label in history_items if (label["account"] or "unknown") == account)),
                str(sum(1 for label in formulation_sources if (label["account"] or "unknown") == account)),
            ]
        )

    recent_rows = [["年份", "总文章", "上海民盟", "写作样本", "文史/盟史"]]
    for year, _ in sorted(by_year.items(), reverse=True):
        if year == "unknown":
            continue
        recent_rows.append(
            [
                year,
                str(sum(1 for label in labels if label["year"] == year)),
                str(sum(1 for label in labels if label["year"] == year and label["account"] == "上海民盟")),
                str(sum(1 for label in writing_samples if label["year"] == year)),
                str(sum(1 for label in history_items if label["year"] == year)),
            ]
        )

    type_rows = [["类型", "全库", "上海民盟2023以后", "写作样本"]]
    for type_name, count in by_type.most_common():
        type_rows.append([type_name, str(count), str(shanghai_by_type.get(type_name, 0)), str(writing_by_type.get(type_name, 0))])

    priority_table = [["分数", "日期", "账号", "当前类型", "建议类型", "标题", "原因", "raw 原文"]]
    for row in priority_rows:
        priority_table.append(
            [
                str(row["priority_score"]),
                row.get("published_at") or "日期不详",
                row.get("account") or "",
                row.get("article_type_name") or "",
                row.get("suggested_type_name") or "待人工判断",
                f"《{row.get('title') or ''}》",
                "；".join(row.get("priority_reasons") or []),
                f"`{row.get('raw_path') or ''}`",
            ]
        )

    return f"""# 微信公众号语料库工作台

生成时间：{created_at}

本页是“盟参”微信公众号语料库的总控入口，用来判断当前语料能支持什么任务、哪里需要先校订、哪些材料可以进入写作和盟史研究。

## 一页结论

- 全库文章：{total} 篇。
- 上海民盟 2023 年以后文章：{len(shanghai_recent)} 篇，是写作风格学习的核心层。
- 写作样本候选：{len(writing_samples)} 篇，覆盖 {len(writing_by_type)} 类体裁。
- 文史/盟史候选：{len(history_items)} 篇，主要来自 {len(history_by_account)} 个账号。
- 近期公开口径候选来源：{len(formulation_sources)} 篇。
- 当前最需要人工校订的是分类边界：通知预告、人物风采、文史纪念、主题教育、成果荣誉之间仍有交叉。

## 可用度判断

| 模块 | 当前可用度 | 依据 | 下一步 |
| --- | --- | --- | --- |
| 微信写稿 | 可用，但需按体裁选样本 | 上海民盟近年样本 {len(shanghai_recent)} 篇，写作样本 {len(writing_samples)} 篇 | 先校订高频体裁样本，再沉淀标题/导语/结构 |
| 盟史研究 | 可用作入口，不可直接定稿 | 文史/盟史候选 {len(history_items)} 篇，已有人物与事件研究档案 | 核心事实回 raw 原文和权威档案 |
| 口径核验 | 种子版可用 | 近期公开口径候选 {len(formulation_sources)} 篇，已有黑名单与口径库 | 扩充高风险术语和争议史实 |
| 新增公众号 | 框架可接入 | `kb refresh` 与 `kb corpus` 可重建标签 | 新增后必须重新跑体检和抽检 |

## 账号层

{markdown_table(account_rows)}

## 年份层

{markdown_table(recent_rows)}

## 体裁层

{markdown_table(type_rows)}

## 优先校订清单 Top 20

{markdown_table(priority_table)}

## 使用路线

1. 写上海民盟公众号文章：先看 `上海民盟微信公众号精选写作样本.md` 和 `上海民盟微信公众号分体裁写作模板.md`，再从本页确认该体裁样本是否充足。
2. 做盟史研究：先看 `微信公众号文史盟史研究入口清单.md`、核心人物档案和核心事件档案，再回 raw 原文核验。
3. 做口径核验：先查 `index/blacklist.csv` 和 `index/formulations.jsonl`，再用 `/核` 输出问题清单。
4. 做分类校订：优先处理本页 Top 20，再看 `微信公众号分类优先校订清单.md` 和 CSV。

## 当前短板

- 人物、事件、地点实体抽取仍是种子级，不能覆盖全部 9000 多篇。
- 部分标题类文章容易误判，如“预告丨盟员医生”可能进入人物风采样本。
- 文史候选文章中混有纪念活动和转载信息，不能全部视为深度研究文章。
- source_url 缺失文章和 unknown 年份文章需要单独补元数据。
"""


def load_article_labels(root: Path) -> list[dict]:
    path = corpus_dir(root) / "article_labels.jsonl"
    if not path.exists():
        return [build_article_label(row) for row in article_rows_for_corpus(root)]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def stable_sample(items: list[dict], limit: int) -> list[dict]:
    if len(items) <= limit:
        return items
    if limit <= 0:
        return []
    step = max(1, len(items) // limit)
    sampled = [items[i] for i in range(0, len(items), step)]
    return sampled[:limit]


def corpus_review_rows(labels: list[dict], per_type: int, low_confidence_limit: int, other_limit: int) -> list[dict]:
    rows = []
    by_type: dict[str, list[dict]] = {}
    for label in sorted(labels, key=lambda item: (item["article_type_name"], item["published_at"] or "", item["article_id"])):
        by_type.setdefault(label["article_type_name"], []).append(label)
    for type_name, items in sorted(by_type.items()):
        for item in stable_sample(items, per_type):
            rows.append({**item, "review_bucket": f"按类型抽检:{type_name}"})
    low_confidence = sorted(labels, key=lambda item: (item["classification_confidence"], item["published_at"] or "", item["article_id"]))
    for item in low_confidence[:low_confidence_limit]:
        rows.append({**item, "review_bucket": "低置信抽检"})
    others = [item for item in low_confidence if item["article_type"] == "other"]
    for item in others[:other_limit]:
        rows.append({**item, "review_bucket": "其他/待判抽检"})
    deduped = []
    seen: set[tuple[int, str]] = set()
    for row in rows:
        key = (int(row["article_id"]), row["review_bucket"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def write_corpus_review_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {
        (row.get("article_id") or "").strip(): row
        for row in read_review_csv(path)
        if (row.get("article_id") or "").strip()
    }
    fieldnames = [
        "review_bucket",
        "article_id",
        "account",
        "published_at",
        "title",
        "article_type_name",
        "classification_confidence",
        "matched_keywords",
        "topic_tags",
        "suggested_type",
        "review_result",
        "review_note",
        "raw_path",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            saved = existing.get(str(row["article_id"]), {})
            writer.writerow(
                {
                    "review_bucket": row["review_bucket"],
                    "article_id": row["article_id"],
                    "account": row["account"] or "",
                    "published_at": row["published_at"] or "",
                    "title": row["title"] or "",
                    "article_type_name": row["article_type_name"],
                    "classification_confidence": row["classification_confidence"],
                    "matched_keywords": "、".join(row.get("matched_keywords") or []),
                    "topic_tags": "、".join(row.get("topic_tags") or []),
                    "suggested_type": saved.get("suggested_type") or "",
                    "review_result": saved.get("review_result") or "",
                    "review_note": saved.get("review_note") or "",
                    "raw_path": row["raw_path"] or "",
                }
            )


def suggested_review_type(label: dict) -> tuple[str, list[str]]:
    title = label.get("title") or ""
    current = label.get("article_type") or ""
    suggestions: list[tuple[str, str]] = []
    if title.startswith(("预告", "通知", "公告", "名单", "公示")):
        suggestions.append(("notice_info", "标题为通知预告类"))
    if any(term in title for term in ["节日快乐", "节日祝福", "新年快乐", "元宵节快乐", "拜年", "中秋快乐", "中秋佳节", "国庆", "迎春", "倒计时", "正式上线"]):
        suggestions.append(("notice_info", "标题为节庆问候/信息发布类"))
    if any(term in title for term in ["祝贺", "荣获", "获得", "获评", "获奖", "获颁", "入选", "表彰", "当选", "提名奖"]):
        suggestions.append(("member_achievement", "标题含获奖/入选/表彰信号"))
    if any(term in title for term in ["盟员美术", "美术大师", "美术家", "作品集萃", "作品赏析", "书画作品", "笔下", "原创歌曲", "MV", "夜听", "劳动最光荣", "我和我的祖国", "我和我的民盟", "以笔寄愿", "共唱", "词曲", "主唱"]):
        suggestions.append(("cultural_showcase", "标题含文化作品/展示传播信号"))
    if any(term in title for term in ["抗疫", "战“疫”", "疫灾", "驰援", "小黄人"]):
        suggestions.append(("social_service", "标题含抗疫/服务行动信号"))
    if any(term in title for term in ["主题教育", "凝心铸魂", "参政为公", "实干为民", "学规定", "强作风"]):
        suggestions.append(("theme_education", "标题含主题教育信号"))
    if any(term in title for term in ["盟史钩沉", "民盟先贤", "五一口号", "旧政协", "新政协", "诞辰"]):
        suggestions.append(("history_commemoration", "标题含文史纪念信号"))
    if current == "other" and not suggestions and any(term in title for term in ["盟员", "先生", "人物", "风采", "访谈", "专访", "代表访谈微视频", "科考记"]):
        suggestions.append(("person_profile", "其他/待判中疑似人物文章"))
    if not suggestions:
        return "", []
    suggested = suggestions[0][0]
    reasons = [reason for _, reason in suggestions]
    return suggested, reasons


def corpus_priority_review_rows(labels: list[dict], limit: int = 100) -> list[dict]:
    rows = []
    for label in labels:
        suggested, reasons = suggested_review_type(label)
        score = 0
        title = label.get("title") or ""
        confidence = int(label.get("classification_confidence") or 0)
        if label.get("article_type") == "other":
            score += 40
            reasons.append("当前为其他/待判")
        if confidence < 70:
            score += 30
            reasons.append(f"置信度较低:{confidence}")
        if suggested and suggested != label.get("article_type"):
            score += 35
            reasons.append(f"建议复核为:{ARTICLE_TYPE_NAMES.get(suggested, suggested)}")
        if label.get("account") == "上海民盟" and year_at_least(label.get("year"), "2023"):
            score += 15
            reasons.append("上海民盟近年样本优先")
        if label.get("is_history"):
            score += 10
            reasons.append("文史/盟史候选")
        if label.get("is_writing_sample"):
            score += 10
            reasons.append("写作样本候选")
        if label.get("article_type") == "activity_report" and any(term in title for term in ["主题教育", "纪念", "盟史", "获", "表彰"]):
            score += 15
            reasons.append("活动报道中含交叉体裁信号")
        if score <= 0:
            continue
        rows.append(
            {
                **label,
                "priority_score": score,
                "suggested_type": suggested,
                "suggested_type_name": ARTICLE_TYPE_NAMES.get(suggested, "") if suggested else "",
                "priority_reasons": sorted(set(reasons)),
            }
        )
    rows.sort(key=lambda item: (-int(item["priority_score"]), item.get("published_at") or "", int(item["article_id"])))
    return rows[:limit]


def write_corpus_priority_review_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {
        (row.get("article_id") or "").strip(): row
        for row in read_review_csv(path)
        if (row.get("article_id") or "").strip()
    }
    fieldnames = [
        "priority_score",
        "article_id",
        "account",
        "published_at",
        "title",
        "current_type",
        "classification_confidence",
        "suggested_type",
        "suggested_type_name",
        "priority_reasons",
        "matched_keywords",
        "topic_tags",
        "review_result",
        "review_note",
        "raw_path",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            saved = existing.get(str(row["article_id"]), {})
            writer.writerow(
                {
                    "priority_score": row["priority_score"],
                    "article_id": row["article_id"],
                    "account": row.get("account") or "",
                    "published_at": row.get("published_at") or "",
                    "title": row.get("title") or "",
                    "current_type": row.get("article_type_name") or "",
                    "classification_confidence": row.get("classification_confidence") or "",
                    "suggested_type": row.get("suggested_type") or "",
                    "suggested_type_name": row.get("suggested_type_name") or "",
                    "priority_reasons": "；".join(row.get("priority_reasons") or []),
                    "matched_keywords": "、".join(row.get("matched_keywords") or []),
                    "topic_tags": "、".join(row.get("topic_tags") or []),
                    "review_result": saved.get("review_result") or "",
                    "review_note": saved.get("review_note") or "",
                    "raw_path": row.get("raw_path") or "",
                }
            )


def read_review_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def normalized_review_result(value: str | None) -> str:
    value = (value or "").strip()
    mapping = {
        "正确": "正确",
        "yes": "正确",
        "right": "正确",
        "ok": "正确",
        "错误": "错误",
        "错": "错误",
        "wrong": "错误",
        "no": "错误",
        "不确定": "不确定",
        "存疑": "不确定",
        "待核": "不确定",
    }
    return mapping.get(value.lower(), value)


def collect_review_decisions(root: Path) -> tuple[dict[int, dict], list[str]]:
    review_paths = [
        corpus_dir(root) / "classification_review.csv",
        corpus_dir(root) / "classification_priority_review.csv",
    ]
    decisions: dict[int, dict] = {}
    warnings = []
    valid_types = set(ARTICLE_TYPE_NAMES)
    for path in review_paths:
        for row in read_review_csv(path):
            result = normalized_review_result(row.get("review_result"))
            suggested = (row.get("suggested_type") or "").strip()
            note = (row.get("review_note") or "").strip()
            if not result and not note:
                continue
            article_raw = (row.get("article_id") or "").strip()
            if not article_raw.isdigit():
                warnings.append(f"{path.name}: article_id 无效：{article_raw}")
                continue
            article_id = int(article_raw)
            if suggested and suggested not in valid_types:
                warnings.append(f"{path.name}: article_id={article_id} suggested_type 无效：{suggested}")
                continue
            existing = decisions.get(article_id)
            decision = {
                "article_id": article_id,
                "review_result": result or "不确定",
                "suggested_type": suggested,
                "review_note": note,
                "review_source": path.name,
            }
            if existing and existing != decision:
                warnings.append(f"article_id={article_id} 存在多条人工校订记录，已采用后出现的 {path.name}")
            decisions[article_id] = decision
    return decisions, warnings


def apply_review_decisions_to_labels(labels: list[dict], decisions: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    updated = []
    applied = []
    for label in labels:
        item = dict(label)
        decision = decisions.get(int(item["article_id"]))
        if decision:
            before_type = item.get("article_type") or ""
            result = decision["review_result"]
            suggested = decision.get("suggested_type") or ""
            if result == "错误" and suggested:
                item["article_type"] = suggested
                item["article_type_name"] = ARTICLE_TYPE_NAMES.get(suggested, suggested)
                item["classification_confidence"] = 100
                item["classification_review_status"] = "人工已改"
            elif result == "正确":
                item["classification_review_status"] = "人工确认"
            else:
                item["classification_review_status"] = "人工存疑"
            item["classification_review_result"] = result
            item["classification_review_note"] = decision.get("review_note") or ""
            item["classification_review_source"] = decision.get("review_source") or ""
            item["classification_reviewed_at"] = now_iso()
            applied.append(
                {
                    "article_id": item["article_id"],
                    "title": item.get("title") or "",
                    "before_type": before_type,
                    "after_type": item.get("article_type") or "",
                    "review_status": item["classification_review_status"],
                    "review_note": item["classification_review_note"],
                }
            )
        updated.append(item)
    return updated, applied


def corpus_review_apply_markdown(applied: list[dict], warnings: list[str], created_at: str) -> str:
    rows = [["文章ID", "标题", "原类型", "现类型", "状态", "备注"]]
    for item in applied[:200]:
        rows.append(
            [
                str(item["article_id"]),
                f"《{item['title']}》",
                ARTICLE_TYPE_NAMES.get(item["before_type"], item["before_type"]),
                ARTICLE_TYPE_NAMES.get(item["after_type"], item["after_type"]),
                item["review_status"],
                item["review_note"],
            ]
        )
    if len(rows) == 1:
        rows.append(["无", "暂无已填写的人工校订", "-", "-", "-", "-"])
    warning_rows = [["提示"]]
    for warning in warnings[:80]:
        warning_rows.append([warning])
    if len(warning_rows) == 1:
        warning_rows.append(["无"])
    changed = sum(1 for item in applied if item["before_type"] != item["after_type"])
    confirmed = sum(1 for item in applied if item["review_status"] == "人工确认")
    uncertain = sum(1 for item in applied if item["review_status"] == "人工存疑")
    return f"""# 微信公众号分类人工校订应用报告

生成时间：{created_at}

## 总体结果

- 已读取并应用人工校订：{len(applied)} 条。
- 其中改分类：{changed} 条。
- 人工确认正确：{confirmed} 条。
- 人工标记存疑：{uncertain} 条。
- 警告：{len(warnings)} 条。

## 应用明细

{markdown_table(rows)}

## 警告

{markdown_table(warning_rows)}

## 使用说明

1. 在 `classification_review.csv` 或 `classification_priority_review.csv` 中填写人工校订。
2. 运行 `kb corpus-apply-reviews --save`。
3. 再运行 `kb corpus-audit` 重建抽检表，查看校订后的优先问题是否减少。
"""


def corpus_priority_review_markdown(rows: list[dict], created_at: str) -> str:
    by_current = Counter(row.get("article_type_name") or "unknown" for row in rows)
    by_suggested = Counter(row.get("suggested_type_name") or "待人工判断" for row in rows)
    table = [["分数", "日期", "账号", "当前类型", "建议类型", "标题", "原因", "raw 原文"]]
    for row in rows:
        table.append(
            [
                str(row["priority_score"]),
                row.get("published_at") or "日期不详",
                row.get("account") or "",
                row.get("article_type_name") or "",
                row.get("suggested_type_name") or "待人工判断",
                f"《{row.get('title') or ''}》",
                "；".join(row.get("priority_reasons") or []),
                f"`{row.get('raw_path') or ''}`",
            ]
        )
    return f"""# 微信公众号分类优先校订清单

生成时间：{created_at}

本页从全库标签中自动挑出最值得优先人工复核的文章。它服务于分类校订闭环，不代表最终分类结论。

## 总览

- 优先校订样本：{len(rows)} 篇。
- CSV 文件：`index/corpus/classification_priority_review.csv`。

## 按当前类型分布

{markdown_table([["当前类型", "篇数"]] + [[k, str(v)] for k, v in by_current.most_common()])}

## 按建议类型分布

{markdown_table([["建议类型", "篇数"]] + [[k, str(v)] for k, v in by_suggested.most_common()])}

## 优先校订清单

{markdown_table(table)}

## 校订办法

1. 优先打开分数最高的文章 raw 原文。
2. 在 CSV 中填写 `review_result` 和 `review_note`。
3. 如果建议类型正确，后续把相同规则固化进分类规则。
4. 如果属于体裁交叉，保留当前类型并在备注中说明原因。
"""


def corpus_review_markdown(rows: list[dict], created_at: str) -> str:
    by_bucket = Counter(row["review_bucket"] for row in rows)
    overview = markdown_table([["抽检桶", "篇数"]] + [[k, str(v)] for k, v in by_bucket.most_common()])
    sections = []
    for bucket in sorted(by_bucket):
        bucket_rows = [row for row in rows if row["review_bucket"] == bucket]
        table = [["日期", "账号", "当前类型", "置信度", "标题", "命中词", "raw 原文"]]
        for row in bucket_rows[:80]:
            table.append(
                [
                    row["published_at"] or "日期不详",
                    row["account"] or "",
                    row["article_type_name"],
                    str(row["classification_confidence"]),
                    f"《{row['title']}》",
                    "、".join(row.get("matched_keywords") or []) or "无",
                    f"`{row['raw_path']}`",
                ]
            )
        sections.append(f"## {bucket}\n\n{markdown_table(table)}")
    return f"""# 微信公众号文章分类抽检表

生成时间：{created_at}

本页用于人工校订第一版文章分类规则。抽检对象包括每个文章类型的代表样本、低置信样本和“其他/待判”样本。

## 抽检规模

{overview}

## 校订方法

1. 打开 raw 原文确认文章真实体裁。
2. 在 `index/corpus/classification_review.csv` 中填写 `suggested_type`、`review_result`、`review_note`。
3. 如果同类错误反复出现，优先修改 `ARTICLE_TYPE_RULES`，再运行 `kb corpus` 和 `kb corpus-audit`。
4. 不确定的文章保留“其他/待判”，不要强行归类。

{chr(10).join(sections)}
"""


def corpus_review_guide_markdown(created_at: str) -> str:
    return f"""# 微信公众号语料库人工校订说明

生成时间：{created_at}

## 当前校订目标

- 先提高文章主类型准确率，不急于做细颗粒实体抽取。
- 优先校订上海民盟 2023 年以后写作样本。
- 文史/盟史文章只做候选库，不把机器分类当作史实结论。

## 字段说明

- `suggested_type`：人工建议类型代码，如 `activity_report`、`meeting_report`。
- `review_result`：填写 `正确`、`错误`、`不确定`。
- `review_note`：说明误判原因或建议增加/删除的关键词。

## 类型代码

{markdown_table([["类型代码", "类型名称"]] + [[rule["type"], rule["name"]] for rule in ARTICLE_TYPE_RULES] + [["other", "其他/待判"]])}

## 推荐流程

1. 先看 `wiki/研究助手/微信公众号文章分类抽检表.md`，挑误判明显的类型。
2. 再编辑 `classification_review.csv`，保留人工判断。
3. 汇总高频误判词，修改分类规则。
4. 重新运行 `.venv/bin/kb corpus && .venv/bin/kb corpus-audit`。
"""


def corpus_quality_diagnostic_markdown(labels: list[dict], created_at: str, limit: int = 30) -> str:
    checks = [
        ("活动报道中疑似履职成果", lambda item: item["article_type"] == "activity_report" and any(term in (item["title"] or "") for term in ["祝贺", "获", "入选", "表彰", "当选"])),
        ("活动报道中疑似主题教育", lambda item: item["article_type"] == "activity_report" and "主题教育" in (item["title"] or "")),
        ("活动报道中疑似文史纪念", lambda item: item["article_type"] == "activity_report" and any(term in (item["title"] or "") for term in ["盟史", "民盟先贤", "纪念", "诞辰", "五一口号"])),
        ("其他/待判中疑似通知预告", lambda item: item["article_type"] == "other" and (item["title"] or "").startswith(("预告", "通知", "公告", "名单", "公示"))),
        ("其他/待判中疑似人物文章", lambda item: item["article_type"] == "other" and any(term in (item["title"] or "") for term in ["盟员", "先生", "人物", "风采", "访谈"])),
    ]
    overview = []
    sections = []
    for name, predicate in checks:
        matched = [item for item in labels if predicate(item)]
        overview.append([name, str(len(matched))])
        rows = [["日期", "账号", "当前类型", "标题", "命中词", "raw 原文"]]
        for item in sorted(matched, key=lambda row: row["published_at"] or "", reverse=True)[:limit]:
            rows.append(
                [
                    item["published_at"] or "日期不详",
                    item["account"] or "",
                    item["article_type_name"],
                    f"《{item['title']}》",
                    "、".join(item.get("matched_keywords") or []) or "无",
                    f"`{item['raw_path']}`",
                ]
            )
        sections.append(f"## {name}\n\n{markdown_table(rows) if matched else '暂未发现。'}")
    by_type = Counter(item["article_type_name"] for item in labels)
    return f"""# 微信公众号分类质量诊断报告

生成时间：{created_at}

本页用于发现机器分类的高频边界问题。它不是人工校订结论，只提示下一轮需要重点抽看的文章。

## 当前类型分布

{markdown_table([["类型", "篇数"]] + [[k, str(v)] for k, v in by_type.most_common()])}

## 边界问题概览

{markdown_table([["诊断项", "疑似篇数"]] + overview)}

{chr(10).join(sections)}

## 使用建议

1. 疑似篇数高的项目，先抽看标题和 raw 原文。
2. 如果误判集中来自某个词，修改 `ARTICLE_TYPE_RULES`。
3. 如果属于体裁交叉，不强行修规则，保留人工校订备注。
4. 每次运行 `kb corpus` 后都重新查看本页。
"""


def command_corpus_audit(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    labels = load_article_labels(root)
    rows = corpus_review_rows(labels, args.per_type, args.low_confidence, args.other)
    priority_rows = corpus_priority_review_rows(labels, args.priority)
    created_at = now_iso()
    out_dir = corpus_dir(root)
    reports = report_dir(root)
    write_corpus_review_csv(out_dir / "classification_review.csv", rows)
    write_corpus_priority_review_csv(out_dir / "classification_priority_review.csv", priority_rows)
    (reports / "微信公众号文章分类抽检表.md").write_text(corpus_review_markdown(rows, created_at), encoding="utf-8")
    (reports / "微信公众号分类优先校订清单.md").write_text(corpus_priority_review_markdown(priority_rows, created_at), encoding="utf-8")
    (reports / "微信公众号语料库人工校订说明.md").write_text(corpus_review_guide_markdown(created_at), encoding="utf-8")
    log_operation(root, "corpus-audit", "ok", f"review samples {len(rows)}", {"output": str(out_dir / "classification_review.csv")})
    print(f"Review samples: {len(rows)}")
    print(f"Priority samples: {len(priority_rows)}")
    print(f"CSV: {out_dir / 'classification_review.csv'}")
    print(f"Priority CSV: {out_dir / 'classification_priority_review.csv'}")
    print(f"Report: {reports / '微信公众号文章分类抽检表.md'}")
    print(f"Priority report: {reports / '微信公众号分类优先校订清单.md'}")
    print(f"Guide: {reports / '微信公众号语料库人工校订说明.md'}")
    return 0


def command_corpus_apply_reviews(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    labels = load_article_labels(root)
    decisions, warnings = collect_review_decisions(root)
    updated, applied = apply_review_decisions_to_labels(labels, decisions)
    if args.dry_run:
        print(corpus_review_apply_markdown(applied, warnings, now_iso()))
        log_operation(root, "corpus-apply-reviews", "dry-run", f"decisions={len(decisions)} applied={len(applied)}")
        return 0

    out_dir = corpus_dir(root)
    if decisions:
        write_jsonl(out_dir / "article_labels.jsonl", updated)
    created_at = now_iso()
    body = corpus_review_apply_markdown(applied, warnings, created_at)
    if args.save:
        path = report_dir(root) / "微信公众号分类人工校订应用报告.md"
        path.write_text(body, encoding="utf-8")
        append_wiki_log(root, f"生成分类人工校订应用报告：{path.relative_to(root)}")
        print(path)
    else:
        print(body)
    status = "ok" if not warnings else "warning"
    log_operation(root, "corpus-apply-reviews", status, f"decisions={len(decisions)} applied={len(applied)} warnings={len(warnings)}")
    return 0


def command_corpus(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    labels = [build_article_label(row) for row in article_rows_for_corpus(root)]
    created_at = now_iso()
    out_dir = corpus_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "article_labels.jsonl", labels)
    (out_dir / "article_types.json").write_text(json.dumps(ARTICLE_TYPE_RULES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reports = report_dir(root)
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "微信公众号语料库体检报告.md").write_text(corpus_audit_markdown(labels, created_at), encoding="utf-8")
    (reports / "微信公众号文章分类体系.md").write_text(type_system_markdown(created_at), encoding="utf-8")
    (reports / "微信公众号分类质量诊断报告.md").write_text(corpus_quality_diagnostic_markdown(labels, created_at), encoding="utf-8")
    (reports / "微信公众号语料库工作台.md").write_text(corpus_dashboard_markdown(labels, created_at), encoding="utf-8")
    (reports / "上海民盟2023年以来写作样本库.md").write_text(writing_samples_markdown(labels, created_at), encoding="utf-8")
    (reports / "上海民盟微信公众号精选写作样本.md").write_text(curated_writing_samples_markdown(labels, created_at), encoding="utf-8")
    (reports / "上海民盟微信公众号分体裁写作模板.md").write_text(writing_style_templates_markdown(labels, created_at), encoding="utf-8")
    (reports / "上海民盟微信公众号写作风格规则卡.md").write_text(shanghai_style_rule_card_markdown(labels, created_at), encoding="utf-8")
    (reports / "微信公众号文史盟史文章专题库.md").write_text(history_corpus_markdown(labels, created_at), encoding="utf-8")
    (reports / "微信公众号文史盟史研究入口清单.md").write_text(history_research_entry_markdown(labels, created_at), encoding="utf-8")
    (reports / "微信公众号参政议政素材主题库.md").write_text(policy_advice_material_index_markdown(labels, created_at), encoding="utf-8")
    log_operation(root, "corpus", "ok", f"labeled {len(labels)} articles", {"output": str(out_dir)})
    print(f"Articles labeled: {len(labels)}")
    print(f"Labels: {out_dir / 'article_labels.jsonl'}")
    print(f"Types: {out_dir / 'article_types.json'}")
    print(f"Reports: {reports}")
    return 0


def command_corpus_style(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    labels = load_article_labels(root)
    created_at = now_iso()
    reports = report_dir(root)
    style_path = reports / "上海民盟微信公众号分体裁写作模板.md"
    curated_path = reports / "上海民盟微信公众号精选写作样本.md"
    rule_card_path = reports / "上海民盟微信公众号写作风格规则卡.md"
    history_path = reports / "微信公众号文史盟史研究入口清单.md"
    policy_path = reports / "微信公众号参政议政素材主题库.md"
    style_path.write_text(writing_style_templates_markdown(labels, created_at), encoding="utf-8")
    curated_path.write_text(curated_writing_samples_markdown(labels, created_at), encoding="utf-8")
    rule_card_path.write_text(shanghai_style_rule_card_markdown(labels, created_at), encoding="utf-8")
    history_path.write_text(history_research_entry_markdown(labels, created_at), encoding="utf-8")
    policy_path.write_text(policy_advice_material_index_markdown(labels, created_at), encoding="utf-8")
    log_operation(root, "corpus-style", "ok", "writing style and history research entries updated", {"style": str(style_path), "curated": str(curated_path), "rule_card": str(rule_card_path), "history": str(history_path), "policy": str(policy_path)})
    print(f"Style templates: {style_path}")
    print(f"Curated samples: {curated_path}")
    print(f"Style rule card: {rule_card_path}")
    print(f"History entries: {history_path}")
    print(f"Policy advice materials: {policy_path}")
    return 0


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


def wiki_dir_for_page_type(page_type: str, topic: str = "") -> str:
    mapping = {
        "assistant": "研究助手",
        "person": "人物",
        "organization": "组织",
        "event": "事件",
        "meeting": "会议",
        "topic": "盟史",
        "issue": "参政议政",
        "oral_history": "口述史",
        "place": "传统教育基地",
        "writing": "文稿素材",
    }
    if page_type == "topic":
        if any(k in topic for k in ["参政", "提案", "建言", "社情民意"]):
            return "参政议政"
        if any(k in topic for k in ["主题教育", "参政为公", "实干为民"]):
            return "主题教育"
        if any(k in topic for k in ["写法", "文稿", "采访"]):
            return "文稿素材"
    return mapping.get(page_type, "盟史")


def make_frontmatter(title: str, page_type: str, source_count: int, confidence: str = "medium") -> str:
    return (
        "---\n"
        f'title: "{title}"\n'
        f"page_type: {page_type}\n"
        "aliases: []\n"
        "tags: [kb-generated]\n"
        f"source_count: {source_count}\n"
        f'last_compiled_at: "{now_iso()}"\n'
        f"confidence: {confidence}\n"
        "review_status: 待校订\n"
        "priority_card: false\n"
        "card_batch: auto\n"
        "needs_review: true\n"
        "---\n\n"
    )


def write_wiki_page(root: Path, title: str, page_type: str, body: str, sources: list[sqlite3.Row]) -> Path:
    directory = wiki_dir_for_page_type(page_type, title)
    path = root / "wiki" / directory / f"{slugify(title, 80)}.md"
    source_count = len({row["article_id"] for row in sources})
    content = (
        make_frontmatter(title, page_type, source_count)
        + "<!-- KB-GENERATED:START -->\n\n"
        + body.strip()
        + "\n\n<!-- KB-GENERATED:END -->\n"
        + "<!-- HUMAN-NOTES:START -->\n\n人工补充区\n\n<!-- HUMAN-NOTES:END -->\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(path, backup)
        content = merge_generated_with_fresh_metadata(path.read_text(encoding="utf-8"), content)
    path.write_text(content, encoding="utf-8")

    conn = connect_db(root)
    try:
        now = now_iso()
        rel = str(path.relative_to(root))
        h = sha256_text(content)
        cur = conn.execute(
            """
            INSERT INTO wiki_pages(title,page_type,path,source_count,needs_review,review_status,priority_card,card_batch,obsidian_path,content_hash,last_compiled_at,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET
                title=excluded.title,
                page_type=excluded.page_type,
                source_count=excluded.source_count,
                needs_review=excluded.needs_review,
                review_status=COALESCE(wiki_pages.review_status, excluded.review_status),
                priority_card=COALESCE(wiki_pages.priority_card, excluded.priority_card),
                card_batch=COALESCE(wiki_pages.card_batch, excluded.card_batch),
                content_hash=excluded.content_hash,
                last_compiled_at=excluded.last_compiled_at,
                updated_at=excluded.updated_at
            RETURNING id
            """,
            (title, page_type, rel, source_count, 1, "待校订", 0, "auto", None, h, now, now, now),
        )
        page_id = cur.fetchone()[0]
        conn.execute("DELETE FROM wiki_sources WHERE wiki_page_id = ?", (page_id,))
        seen: set[int] = set()
        for row in sources:
            article_id = int(row["article_id"])
            if article_id in seen:
                continue
            seen.add(article_id)
            conn.execute(
                "INSERT OR IGNORE INTO wiki_sources(wiki_page_id, article_id, raw_path, citation_note, created_at) VALUES (?, ?, ?, ?, ?)",
                (page_id, article_id, row["raw_path"], row["title"], now),
            )
        conn.commit()
    finally:
        conn.close()
    return path


def compile_body(topic: str, page_type: str, rows: list[sqlite3.Row]) -> str:
    if not rows:
        return f"# {topic}\n\n未检索到可靠来源。\n\n## 待核实点\n\n- 需要补充来源。"
    source_lines = "\n".join(f"- {row_source_line(row, idx)}" for idx, row in enumerate(rows, 1))
    excerpts = "\n".join(f"{idx}. {clean_snippet(row['snippet'], 280)} [S{idx}]" for idx, row in enumerate(rows, 1))
    titles = []
    seen_titles = set()
    for row in rows:
        if row["title"] not in seen_titles:
            seen_titles.add(row["title"])
            titles.append(f"- {row['published_at'] or '日期不详'}｜{row['account']}｜《{row['title']}》")
    title_list = "\n".join(titles[:12])
    return f"""# {topic}

## 概述

本页由本地微信公众号知识库自动编译，依据检索词“{topic}”命中的 raw 原文生成。当前为研究型初稿，适合继续人工校订。

## 核心判断

- 已检索到 {len(rows)} 条相关片段，来源覆盖 {len({row['article_id'] for row in rows})} 篇文章。
- 目前材料可以支持初步梳理，但正式研究或发稿前，应逐条打开 raw 原文核对事实细节。
- 如果该主题涉及上海民盟，应同时区分“全国民盟史主线”和“上海地方实践”。

## 主要来源文章

{title_list}

## 证据摘录

{excerpts}

## 可用于写稿的事实材料

- 可从上述来源中提取时间、地点、人物、会议、活动和表述。
- 对于历史类主题，应优先建立时间线和人物关系。
- 对于写作类主题，应优先提取标题、导语、结构和常用表达。

## 风险与待核实点

- 本页为自动编译初稿，不等同于最终史实结论。
- 片段可能来自同一文章不同 chunk，引用时需打开 raw 原文查看完整上下文。
- 若来源数量不足，应扩大检索词后重新编译。

## 来源

{source_lines}
"""


def topic_pack_body(topic: str, mode: str, rows: list[sqlite3.Row]) -> str:
    source_table = "\n".join(row_source_md(row, idx) for idx, row in enumerate(rows, 1))
    excerpts = "\n".join(f"{idx}. {clean_snippet(row['snippet'], 260)} [S{idx}]" for idx, row in enumerate(rows, 1))
    return f"""# {topic}

## 专题定位

本页是专题研究包，用于把分散文章组织成可继续研究、写作和汇报的材料入口。当前命中 {len(rows)} 条片段，覆盖 {len({row['article_id'] for row in rows}) if rows else 0} 篇来源文章。

## 时间线

{timeline_candidates(rows, 15)}

## 人物表

{entity_candidates(rows, topic, 20)}

## 事件表

- 待从证据摘录和 raw 原文中拆分具体事件。
- 优先标注时间、地点、人物、组织和事件结果。

## 地点表

- 待从证据摘录和 raw 原文中拆分具体地点。
- 若涉及上海，应区分旧址、纪念馆、传统教育基地和活动场所。

## 核心来源

{source_title_list(rows, 15)}

## 证据摘录

{excerpts or '未检索到可靠来源。'}

## 可写作角度

- 研究报告：按历史阶段、人物关系、事件脉络和现实意义组织。
- 微信文史稿：用当下活动或地点切入，再回到历史事实。
- 主题教育材料：以具体史实支撑精神阐释，避免空泛表态。
- 参政议政素材：若为履职主题，提取问题意识、建议方向和工作机制。

## 待核实点

- 是否存在同一文章多次转载或重复来源。
- 关键年份、会议名称、人物身份和地点是否需要外部权威资料互证。
- 正式使用前，应逐条打开 raw 原文核对上下文。

## 来源表

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{source_table or '| - | - | - | 未检索到可靠来源 | - |'}
"""


def writing_workflow_body(name: str, spec: dict[str, list[str] | str], rows: list[sqlite3.Row]) -> str:
    source_table = "\n".join(row_source_md(row, idx) for idx, row in enumerate(rows, 1))
    excerpts = "\n".join(f"- {clean_snippet(row['snippet'], 240)} [S{idx}]" for idx, row in enumerate(rows, 1))
    sections = spec["sections"]
    section_text = "\n\n".join(
        f"## {section}\n\n{writing_workflow_section_text(name, section)}"
        for section in sections  # type: ignore[union-attr]
    )
    return f"""# {name}

## 工作流定位

本页用于上海民盟微信公众号写作。它不是最终成稿，而是把材料检查、结构选择和风险控制固定下来。当前参考 {len(rows)} 条来源片段。

{section_text}

## 可参考来源片段

{excerpts or '未检索到可靠来源。'}

## 来源表

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{source_table or '| - | - | - | 未检索到可靠来源 | - |'}
"""


def writing_workflow_section_text(workflow: str, section: str) -> str:
    common = {
        "材料清单": "- 时间、地点、活动全称、主办承办单位。\n- 出席人员及准确职务。\n- 主要议程、讲话要点、数字和成果。\n- 图片说明和供稿信息。",
        "标题模板": "- 动词 + 主题 + 活动类型。\n- 历史/人物类可用“人物 + 精神/事件 + 当代回响”。\n- 标题可有新媒体入口，但正文保持机关新闻规范。",
        "导语模板": "近日/某月某日，某活动在某地举行。活动围绕……，邀请……参加，旨在……。民盟市委……出席并讲话。",
        "正文结构": "导语交代事实，第二段交代背景，中段写流程和重点观点，结尾落到履职、传承、组织建设或下一步工作。",
        "常见风险": "- 人名、职务、组织名称、会议名称写错。\n- 把纪念性表述当成史实。\n- 只有口号，没有事实支撑。\n- 领导讲话层级和语气处理不准。",
    }
    specialized = {
        "适用场景": f"适用于“{workflow}”相关稿件。先判断是否需要史实核验，再进入成稿。",
        "采访材料清单": "- 被采访人基本信息、入盟时间、岗位职责。\n- 关键经历、具体故事、代表成果。\n- 与民盟关系、履职情况、人物原话。",
        "人物稿结构": "人物入口、关键场景、成长经历、岗位贡献、盟员身份、精神落点。避免简历堆砌。",
        "细节写法": "优先使用动作、现场、原话和具体数字，让人物可信可感。",
        "史实核验清单": "- 年份、地点、会议、人物身份。\n- 来源是否为原始史料、转述还是活动报道。\n- 全国主线与上海地方史是否区分清楚。",
        "上海切入方式": "从上海地点、上海组织活动、上海纪念资源或上海盟员传承实践切入。",
        "成果表达": "突出问题意识、调研过程、建议方向、采纳转化或履职成效。",
        "落实机制写法": "写具体学习安排、基层组织方式、履职结合点和可见成效。",
    }
    return specialized.get(section) or common.get(section) or "- 待根据具体材料补充。"


def command_compile(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    topic = args.topic or args.query
    if not topic:
        print("compile requires --topic or --query", file=sys.stderr)
        return 2
    rows = search_rows(root, args.query or topic, args.top_k)
    body = compile_body(topic, args.page_type, rows)
    path = write_wiki_page(root, topic, args.page_type, body, rows)
    append_wiki_log(root, f"编译 wiki 页面：{path.relative_to(root)}")
    log_operation(root, "compile", "ok", str(path), {"topic": topic, "page_type": args.page_type, "sources": len(rows)})
    print(f"Compiled: {path}")
    print(f"Sources: {len(rows)}")
    return 0


CARD_SETS = {
    "people": {
        "page_type": "person",
        "items": ["张澜", "沈钧儒", "黄炎培", "梁漱溟", "史良", "李公朴", "闻一多", "陶行知", "费孝通", "钱伟长", "陈望道", "谷超豪"],
    },
    "shanghai-history-people": {
        "page_type": "person",
        "items": ["徐中玉", "杨村彬", "谈家桢", "李国豪", "戚雅仙", "童芷苓", "周谷城", "苏步青", "全增嘏", "胡和生", "朱东润", "王中", "蒋学模", "刘王立明", "王元美"],
    },
    "events": {
        "page_type": "event",
        "items": ["中国民主政团同盟成立", "旧政协", "李闻事件", "民盟被迫解散", "民盟一届三中全会", "民盟一届二中全会", "五一口号", "新政协", "上海李闻追悼大会", "六二三事件", "上海民盟组织建立"],
    },
    "places": {
        "page_type": "place",
        "items": ["特园", "香港光明报", "国府路300号", "太平胡同", "陶行知纪念馆", "钱伟长纪念馆", "上海市档案馆", "上海民盟传统教育基地", "虹桥疗养院", "周公馆", "上海清华同学会", "龙华烈士陵园", "福寿园", "复旦大学", "上海大学", "上海市行知中学", "上海民主党派大厦"],
    },
}

CARD_QUERY_OVERRIDES = {
    "六二三事件": "六二三 6•23 反内战 上海 民盟",
    "香港光明报": "香港 光明报 民盟",
}

PRIORITY_CARDS = {
    "person": ["张澜", "沈钧儒", "黄炎培", "史良", "李公朴", "闻一多", "陶行知", "费孝通", "钱伟长"],
    "event": ["五一口号", "旧政协", "新政协", "民盟一届二中全会", "民盟一届三中全会", "李闻事件"],
    "place": ["周公馆", "虹桥疗养院", "上海清华同学会", "陶行知纪念馆", "钱伟长纪念馆", "上海市档案馆"],
}

TOPIC_PACKS = [
    ("民盟与五一口号", "五一口号 民盟 响应 新政协 多党合作 上海", "history"),
    ("民盟与人民政协", "人民政协 新政协 旧政协 民盟 沈钧儒 张澜", "history"),
    ("上海民盟组织建立与早期发展", "上海民盟 组织建立 民盟一届二中全会 上海 支部", "history"),
    ("上海民盟传统教育基地", "上海民盟 传统教育基地 周公馆 虹桥疗养院 陶行知纪念馆", "history"),
    ("民盟先贤与上海", "上海 民盟先贤 张澜 沈钧儒 黄炎培 陶行知 钱伟长", "history"),
    ("参政议政写作素材库", "参政议政 社情民意 提案 调研 建言 写作 素材", "policy"),
]

RESEARCH_DOSSIER_SETS = {
    "core-people": ["沈钧儒", "史良", "张澜", "黄炎培", "费孝通", "李公朴", "闻一多", "陶行知", "钱伟长"],
    "founding-people": ["沈钧儒", "张澜", "黄炎培", "史良", "李公朴", "闻一多"],
    "core-events": ["五一口号", "旧政协", "新政协", "李闻事件", "民盟一届二中全会", "民盟一届三中全会", "民盟被迫解散", "中国民主政团同盟成立", "上海民盟组织建立"],
}

AUTHORITY_COVERAGE_TOPICS = [
    ("制度主题", "新型政党制度", "新型政党制度 多党合作 政治协商 参政党"),
    ("制度主题", "多党合作制度", "中国共产党领导的多党合作和政治协商制度"),
    ("制度主题", "高素质参政党", "高素质参政党 民盟 参政党建设"),
    ("制度主题", "全过程人民民主", "全过程人民民主 新型政党制度 多党合作"),
]

PERSON_RESEARCH_THEMES = {
    "民盟史主线": ["民盟", "民盟史", "中国民主同盟", "民主政团同盟"],
    "多党合作与政协": ["政协", "新政协", "旧政协", "五一口号", "多党合作"],
    "上海地方线索": ["上海", "上海民盟", "沪", "周公馆", "福寿园", "虹桥"],
    "文史纪念传播": ["纪念", "先贤", "诞辰", "传统教育", "盟史钩沉"],
    "参政议政线索": ["参政议政", "提案", "社情民意", "建言", "履职"],
}

EVENT_RESEARCH_THEMES = {
    "事件事实链": ["时间", "地点", "召开", "发表", "宣布", "成立", "响应", "解散"],
    "相关人物": ["张澜", "沈钧儒", "黄炎培", "史良", "李公朴", "闻一多", "章伯钧"],
    "民盟组织线索": ["民盟", "中国民主同盟", "民主政团同盟", "总部", "中央", "上海民盟"],
    "多党合作线索": ["中国共产党", "多党合作", "统一战线", "政协", "新政协", "旧政协"],
    "上海地方线索": ["上海", "沪", "周公馆", "虹桥", "福寿园", "上海民盟"],
    "争议与口径风险": ["争议", "考证", "档案", "史料", "被迫", "非法", "惨案"],
}

WRITING_WORKFLOWS = {
    "活动会议报道": {
        "query": "上海民盟 活动 会议 报道 举行 出席 讲话",
        "sections": ["适用场景", "材料清单", "标题模板", "导语模板", "正文结构", "常见风险"],
    },
    "人物采访人物风采": {
        "query": "上海民盟 人物 采访 风采 盟员 事迹",
        "sections": ["适用场景", "采访材料清单", "标题模板", "人物稿结构", "细节写法", "常见风险"],
    },
    "文史纪念文章": {
        "query": "上海民盟 文史 纪念 先贤 盟史 传统教育基地",
        "sections": ["适用场景", "史实核验清单", "标题模板", "文章结构", "上海切入方式", "常见风险"],
    },
    "参政议政报道": {
        "query": "上海民盟 参政议政 社情民意 提案 调研 建言",
        "sections": ["适用场景", "材料清单", "标题模板", "导语模板", "成果表达", "常见风险"],
    },
    "主题教育报道": {
        "query": "上海民盟 主题教育 参政为公 实干为民 基层 落实",
        "sections": ["适用场景", "材料清单", "标题模板", "正文结构", "落实机制写法", "常见风险"],
    },
}


def update_frontmatter_fields(path: Path, updates: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return
    end = text.find("\n---", 4)
    if end == -1:
        return
    fm = text[4:end].splitlines()
    body = text[end:]
    seen = set()
    new_fm = []
    for line in fm:
        key = line.split(":", 1)[0].strip() if ":" in line else ""
        if key in updates:
            new_fm.append(f"{key}: {updates[key]}")
            seen.add(key)
        else:
            new_fm.append(line)
    for key, value in updates.items():
        if key not in seen:
            new_fm.append(f"{key}: {value}")
    path.write_text("---\n" + "\n".join(new_fm) + body, encoding="utf-8")


def apply_priority_card_status(root: Path) -> int:
    init_db(root)
    conn = connect_db(root)
    updated = 0
    try:
        for page_type, titles in PRIORITY_CARDS.items():
            for title in titles:
                row = conn.execute(
                    "SELECT id, path FROM wiki_pages WHERE page_type = ? AND title = ?",
                    (page_type, title),
                ).fetchone()
                if not row:
                    continue
                conn.execute(
                    "UPDATE wiki_pages SET review_status = ?, priority_card = 1, card_batch = ?, needs_review = 1, updated_at = ? WHERE id = ?",
                    ("重点待校订", "priority", now_iso(), row["id"]),
                )
                path = root / row["path"]
                if path.exists():
                    update_frontmatter_fields(
                        path,
                        {
                            "review_status": "重点待校订",
                            "priority_card": "true",
                            "card_batch": "priority",
                            "needs_review": "true",
                        },
                    )
                updated += 1
        conn.commit()
    finally:
        conn.close()
    append_wiki_log(root, f"更新重点研究卡状态：{updated} 张")
    log_operation(root, "curate-cards", "ok", f"{updated} priority cards")
    return updated


def card_body(name: str, page_type: str, rows: list[sqlite3.Row]) -> str:
    source_lines = "\n".join(f"- {row_source_line(row, idx)}" for idx, row in enumerate(rows, 1))
    excerpts = "\n".join(f"- {clean_snippet(row['snippet'], 260)} [S{idx}]" for idx, row in enumerate(rows, 1))
    if page_type == "person":
        source_table = "\n".join(row_source_md(row, idx) for idx, row in enumerate(rows, 1))
        shanghai_rows = [
            row for row in rows
            if "上海" in (row["account"] or "") or "上海" in (row["title"] or "") or "上海" in (row["snippet"] or "")
        ]
        shanghai_excerpts = "\n".join(
            f"- {clean_snippet(row['snippet'], 240)} [S{rows.index(row) + 1}]"
            for row in shanghai_rows[:5]
        )
        return f"""# {name}

## 人物定位

“{name}”是民盟历史人物研究对象。本卡依据本地微信公众号知识库自动生成，当前命中 {len(rows)} 条片段，覆盖 {len({row['article_id'] for row in rows}) if rows else 0} 篇来源文章。

本卡的作用是为后续盟史研究、上海民盟地方史写作、文史纪念文章和主题教育材料提供事实入口。它不是最终人物传记，正式使用前必须回到 raw 原文核对。

## 基本信息

- 姓名：{name}
- 生卒年：待人工核对。
- 主要身份：待依据权威来源补充。
- 与民盟关系：待依据下列来源原文整理。
- 相关领域：民盟史、多党合作史、知识分子与近现代中国、上海民盟文史写作。

## 与民盟关系

从当前命中材料看，“{name}”与民盟历史的关系需要优先放在具体历史阶段中理解，而不是只作纪念性概括。后续整理时应重点核对：

- 是否参与民盟早期组织、会议或重要政治活动。
- 是否与“五一口号”、新政协、人民政协、民主运动、文化教育等主题有关。
- 是否在上海民盟地方史中留下可写作的活动地点、事件或纪念资源。

## 关键历史节点线索

{timeline_candidates(rows, 12)}

## 主要来源文章

{source_title_list(rows, 12)}

## 证据摘录

{excerpts or '未检索到可靠来源。'}

## 上海关联线索

{shanghai_excerpts or '- 当前命中材料中上海关联不够集中，需要用“上海 + 人名”继续检索。'}

## 可写作角度

- 人物研究卡：梳理“{name}”与民盟发展、历史转折、政治选择之间的关系。
- 文史纪念文章：围绕一个关键事件或一个历史现场展开，避免泛泛写生平。
- 主题教育材料：提炼其精神品格，但必须由具体史实支撑。
- 上海民盟公众号文章：优先寻找上海地点、上海组织活动、上海纪念资源或上海盟员传承实践。

## 后续核验问题

- 生卒年、籍贯、职务、党派身份和历史阶段是否准确。
- 相关会议、通电、声明、组织名称、地点和日期是否能在 raw 原文中互证。
- 材料属于民盟中央盟史叙述、上海地方史叙述，还是纪念活动报道。
- 是否需要补充外部权威资料，如民盟史著作、政协文史资料、档案馆资料。

## 来源表

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{source_table or '| - | - | - | 未检索到可靠来源 | - |'}
"""
    elif page_type == "event":
        source_table = "\n".join(row_source_md(row, idx) for idx, row in enumerate(rows, 1))
        return f"""# {name}

## 事件定位

“{name}”是民盟历史事件研究卡。本卡用于把全国民盟史主线、上海地方史回响和微信公众号文史写作素材放在同一入口下管理。当前命中 {len(rows)} 条片段，覆盖 {len({row['article_id'] for row in rows}) if rows else 0} 篇来源文章。

## 基本信息

- 事件名称：{name}
- 时间：待根据 raw 原文核对。
- 地点：待根据 raw 原文核对。
- 相关人物/组织：待根据来源整理。
- 事件类型：民盟史、统一战线史、多党合作史或上海地方史。

## 历史脉络

本事件应放入具体历史阶段理解。整理时优先回答：

- 它发生在民盟哪一段历史进程中。
- 它与中国共产党领导的多党合作、政治协商或民主运动有什么关系。
- 它在上海是否有地方现场、纪念活动、组织传承或传播实践。

## 关键时间线线索

{timeline_candidates(rows, 12)}

## 主要来源文章

{source_title_list(rows, 12)}

## 证据摘录

{excerpts or '未检索到可靠来源。'}

## 可写作角度

- 全国主线：讲清事件在民盟史和多党合作史中的位置。
- 上海切面：寻找上海现场、上海组织、上海纪念和上海传播实践。
- 文史文章：用“当下纪念/活动入口 + 历史事实 + 人物关系 + 当代意义”组织。

## 待核实问题

- 事件名称、日期、地点和参加人物是否准确。
- 来源之间是否存在同一材料多次转载或不同口径。
- 是否需要补充权威盟史著作、档案馆资料或政协文史资料。

## 来源表

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{source_table or '| - | - | - | 未检索到可靠来源 | - |'}
"""
    else:
        source_table = "\n".join(row_source_md(row, idx) for idx, row in enumerate(rows, 1))
        return f"""# {name}

## 地点定位

“{name}”是民盟历史地点/传统教育资源研究卡。本卡用于整理地点的历史事实、相关人物、相关事件和当代教育传播价值。当前命中 {len(rows)} 条片段，覆盖 {len({row['article_id'] for row in rows}) if rows else 0} 篇来源文章。

## 基本信息

- 地点名称：{name}
- 所在城市/区域：待根据 raw 原文核对。
- 相关人物：待根据来源整理。
- 相关事件：待根据来源整理。
- 当前功能：旧址、纪念馆、传统教育基地、档案资源或活动场所，待核对。

## 历史关系

整理本地点时应优先回答：

- 这里与民盟组织、民盟先贤或重要事件有什么关系。
- 它是全国民盟史地点，还是上海民盟地方史地点。
- 它今天如何被用于主题教育、盟史传播、组织建设或文史写作。

## 关键时间线线索

{timeline_candidates(rows, 12)}

## 主要来源文章

{source_title_list(rows, 12)}

## 证据摘录

{excerpts or '未检索到可靠来源。'}

## 可写作角度

- 打卡式文史稿：从今天的地点进入历史。
- 传统教育基地稿：写“地点 + 人物 + 事件 + 当代传承”。
- 研究说明：梳理地点在全国主线与上海地方史中的双重位置。

## 待核实问题

- 地点名称、地址、历史时期和当前功能是否准确。
- 是否有挂牌、展陈、纪念活动或档案合作的明确来源。
- 是否需要补充地图、图片、档案馆说明或官方介绍。

## 来源表

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{source_table or '| - | - | - | 未检索到可靠来源 | - |'}
"""
    return f"""# {name}

{sections}本页依据本地检索结果生成初稿。请根据来源原文补充完整事实。

## 核心判断

- “{name}”在知识库中已有可追溯来源，可继续扩展为研究卡片。
- 当前页仅自动摘录来源，不替代人工史实核定。

## 证据摘录

{excerpts or '未检索到可靠来源。'}

## 时间线

- 待根据来源原文整理。

## 待核实点

- 生卒年、职务、会议名称、地点和具体日期需回 raw 原文核对。

## 来源

{source_lines or '- 未检索到可靠来源。'}
"""


def dossier_dir(root: Path) -> Path:
    return root / "wiki" / "研究助手" / "核心人物研究档案"


def event_dossier_dir(root: Path) -> Path:
    return root / "wiki" / "研究助手" / "核心事件研究档案"


def row_text(row: sqlite3.Row) -> str:
    return " ".join(str(row[key] or "") for key in ("title", "account", "published_at", "snippet", "raw_path"))


def rows_matching_keywords(rows: list[sqlite3.Row], keywords: list[str]) -> list[sqlite3.Row]:
    return [row for row in rows if any(keyword in row_text(row) for keyword in keywords)]


def dossier_theme_sections(sources: list[sqlite3.Row], themes: dict[str, list[str]]) -> tuple[list[list[str]], list[str]]:
    theme_rows = []
    theme_sections = []
    for theme, keywords in themes.items():
        matched = rows_matching_keywords(sources, keywords)
        theme_rows.append([theme, str(len(matched)), "、".join(keywords[:8])])
        if matched:
            table = [["编号", "日期", "账号", "标题", "raw 原文"]]
            for row in matched[:6]:
                idx = sources.index(row) + 1 if row in sources else 0
                table.append([f"S{idx}", row["published_at"] or "日期不详", row["account"] or "", f"《{row['title']}》", f"`{row['raw_path']}`"])
            theme_sections.append(f"### {theme}\n\n{markdown_table(table)}")
    return theme_rows, theme_sections


def person_research_dossier_body(name: str, rows: list[sqlite3.Row], created_at: str) -> str:
    sources = unique_source_rows(rows, 18)
    by_account = Counter(row["account"] or "unknown" for row in sources)
    by_year = Counter((row["published_at"] or "日期不详")[:4] for row in sources)
    theme_rows, theme_sections = dossier_theme_sections(sources, PERSON_RESEARCH_THEMES)

    evidence_rows = [["编号", "日期", "账号", "标题", "证据摘录"]]
    for idx, row in enumerate(sources[:12], 1):
        evidence_rows.append([f"S{idx}", row["published_at"] or "日期不详", row["account"] or "", f"《{row['title']}》", clean_snippet(row["snippet"], 180)])

    source_table = [["编号", "账号", "日期", "标题", "raw 原文"]]
    for idx, row in enumerate(sources, 1):
        source_table.append([f"S{idx}", row["account"] or "", row["published_at"] or "日期不详", f"《{row['title']}》", f"`{row['raw_path']}`"])

    return f"""# {name}研究档案

生成时间：{created_at}

本档案是“盟参”研究型知识库的人物入口页，依据本地微信公众号语料自动生成。它只整理可追溯线索，不把机器摘录写成最终史实结论。

## 研究定位

- 人物：{name}
- 档案性质：核心人物研究入口
- 命中片段：{len(rows)} 条
- 去重来源文章：{len(sources)} 篇
- 结论状态：待人工核验

## 来源分布

{markdown_table([["账号", "来源篇数"]] + [[k, str(v)] for k, v in by_account.most_common()])}

{markdown_table([["年份", "来源篇数"]] + [[k, str(v)] for k, v in sorted(by_year.items(), reverse=True)])}

## 主题线索

{markdown_table([["主题", "命中来源数", "识别词"]] + theme_rows)}

{chr(10).join(theme_sections) if theme_sections else '暂无明显主题线索。'}

## 时间线线索

{timeline_candidates(sources, 12)}

## 证据摘录

{markdown_table(evidence_rows)}

## 待核字段

- 生卒年、籍贯、主要身份、入盟或参与民盟活动时间。
- 民盟内职务、政府/政协/社会职务及其任期。
- 与重要事件的关系，如建盟、旧政协、五一口号、新政协、李闻事件等。
- 上海地方史关联：地点、组织、活动、纪念资源和传承实践。
- 引语、评价、历史判断是否来自原文、转载、纪念性表述或权威档案。

## 写作与研究用法

- 写文史纪念文章：先从“时间线线索”选一个具体事件，不直接铺陈完整生平。
- 写人物风采文章：优先寻找可验证细节、具体场景和人物贡献。
- 做盟史研究：区分全国民盟史主线与上海地方史线索。
- 做口径核验：涉及职务、日期、组织名称、历史评价的表述必须回到 raw 原文。

## 来源表

{markdown_table(source_table)}
"""


def event_research_dossier_body(name: str, rows: list[sqlite3.Row], created_at: str) -> str:
    sources = unique_source_rows(rows, 20)
    by_account = Counter(row["account"] or "unknown" for row in sources)
    by_year = Counter((row["published_at"] or "日期不详")[:4] for row in sources)
    theme_rows, theme_sections = dossier_theme_sections(sources, EVENT_RESEARCH_THEMES)
    related_people = Counter()
    for row in sources:
        related_people.update(people_hits_for_text(row_text(row)))

    evidence_rows = [["编号", "日期", "账号", "标题", "证据摘录"]]
    for idx, row in enumerate(sources[:12], 1):
        evidence_rows.append([f"S{idx}", row["published_at"] or "日期不详", row["account"] or "", f"《{row['title']}》", clean_snippet(row["snippet"], 180)])

    source_table = [["编号", "账号", "日期", "标题", "raw 原文"]]
    for idx, row in enumerate(sources, 1):
        source_table.append([f"S{idx}", row["account"] or "", row["published_at"] or "日期不详", f"《{row['title']}》", f"`{row['raw_path']}`"])

    people_table = [["人物", "命中来源数"]]
    people_table.extend([[person, str(count)] for person, count in related_people.most_common(12)])

    return f"""# {name}研究档案

生成时间：{created_at}

本档案是“盟参”研究型知识库的事件入口页，依据本地微信公众号语料自动生成。它只整理可追溯线索，不把机器摘录写成最终史实结论。

## 事件定位

- 事件：{name}
- 档案性质：核心事件研究入口
- 命中片段：{len(rows)} 条
- 去重来源文章：{len(sources)} 篇
- 结论状态：待人工核验

## 来源分布

{markdown_table([["账号", "来源篇数"]] + [[k, str(v)] for k, v in by_account.most_common()])}

{markdown_table([["年份", "来源篇数"]] + [[k, str(v)] for k, v in sorted(by_year.items(), reverse=True)])}

## 主题线索

{markdown_table([["主题", "命中来源数", "识别词"]] + theme_rows)}

{chr(10).join(theme_sections) if theme_sections else '暂无明显主题线索。'}

## 相关人物线索

{markdown_table(people_table) if len(people_table) > 1 else '待从原文中继续抽取。'}

## 时间线线索

{timeline_candidates(sources, 14)}

## 证据摘录

{markdown_table(evidence_rows)}

## 待核字段

- 事件准确名称、发生日期、地点、相关组织和参与人物。
- 不同文章对事件阶段、因果关系、历史评价的表述是否一致。
- 是否存在纪念性表达、宣传性概括、转载材料和权威史料之间的层级差异。
- 上海地方线索与全国民盟史主线是否需要分开叙述。
- 涉及争议、称谓、日期、会议届次、组织名称时，必须回 raw 原文和权威档案核验。

## 写作与研究用法

- 写盟史课堂：先从“时间线线索”确定事件顺序，再选取一条可讲述的主线。
- 写文史纪念文章：围绕具体人物、地点或关键文献展开，避免泛化口号。
- 写领导讲话素材：只提炼可由来源支撑的历史意义和现实启示。
- 做口径核验：事件名称、日期、参与人物和组织关系必须逐条溯源。

## 来源表

{markdown_table(source_table)}
"""


def write_person_research_dossier(root: Path, name: str, rows: list[sqlite3.Row], created_at: str) -> Path:
    path = dossier_dir(root) / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = person_research_dossier_body(name, rows, created_at)
    path.write_text(body, encoding="utf-8")
    return path


def write_event_research_dossier(root: Path, name: str, rows: list[sqlite3.Row], created_at: str) -> Path:
    path = event_dossier_dir(root) / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = event_research_dossier_body(name, rows, created_at)
    path.write_text(body, encoding="utf-8")
    return path


def person_dossier_rows(root: Path, name: str, top_k: int) -> list[sqlite3.Row]:
    rows = search_rows(root, name, top_k * 3)
    focused = [row for row in rows if name in row_text(row)]
    if len(focused) < top_k:
        broad = search_rows(root, f"{name} 民盟", top_k * 2)
        seen = {int(row["chunk_id"]) for row in focused if row["chunk_id"] is not None}
        for row in broad:
            if name not in row_text(row):
                continue
            chunk_id = int(row["chunk_id"])
            if chunk_id in seen:
                continue
            focused.append(row)
            seen.add(chunk_id)
            if len(focused) >= top_k:
                break
    return focused[:top_k]


def event_dossier_rows(root: Path, name: str, top_k: int) -> list[sqlite3.Row]:
    query = CARD_QUERY_OVERRIDES.get(name, name)
    rows = search_rows(root, query, top_k * 3)
    focused = [row for row in rows if any(term in row_text(row) for term in query_terms(query) + [name])]
    if len(focused) < top_k:
        broad = search_rows(root, f"{query} 民盟 盟史 上海", top_k * 2)
        seen = {int(row["chunk_id"]) for row in focused if row["chunk_id"] is not None}
        for row in broad:
            if not any(term in row_text(row) for term in query_terms(query) + [name]):
                continue
            chunk_id = int(row["chunk_id"])
            if chunk_id in seen:
                continue
            focused.append(row)
            seen.add(chunk_id)
            if len(focused) >= top_k:
                break
    return focused[:top_k]


def authority_level_counts(rows: list[sqlite3.Row]) -> Counter:
    counts = Counter()
    for row in unique_source_rows(rows, len(rows)):
        level = row["authority_level"] if "authority_level" in row.keys() and row["authority_level"] else "L4"
        counts[str(level)] += 1
    return counts


def authority_coverage_status(counts: Counter) -> str:
    if counts.get("L1", 0) > 0:
        return "可优先用于事实核验"
    if counts.get("L2", 0) > 0 or counts.get("L3", 0) > 0:
        return "可作权威佐证，仍需补 L1"
    if counts.get("L4", 0) > 0:
        return "仅有样本线索，不能作定论"
    return "暂无有效命中"


def authority_coverage_action(counts: Counter) -> str:
    if counts.get("L1", 0) > 0:
        return "优先人工校订事实卡；补充正式出版物或档案互证"
    if counts.get("L2", 0) > 0 or counts.get("L3", 0) > 0:
        return "补民盟中央、统战部、政协官网或白皮书 L1 来源"
    if counts.get("L4", 0) > 0:
        return "先找 L1-L3 权威来源，再进入正式研究卡"
    return "补关键词、补来源候选或人工登记线索"


def authority_coverage_records(root: Path, top_k: int) -> list[dict]:
    records = []
    for name in RESEARCH_DOSSIER_SETS["core-people"]:
        rows = person_dossier_rows(root, name, top_k)
        records.append({"kind": "核心人物", "name": name, "rows": rows})
    for name in RESEARCH_DOSSIER_SETS["core-events"]:
        rows = event_dossier_rows(root, name, top_k)
        records.append({"kind": "核心事件", "name": name, "rows": rows})
    for kind, name, query in AUTHORITY_COVERAGE_TOPICS:
        rows = search_rows(root, query, top_k)
        records.append({"kind": kind, "name": name, "rows": rows})

    out = []
    for record in records:
        sources = unique_source_rows(record["rows"], top_k)
        counts = authority_level_counts(sources)
        out.append(
            {
                "kind": record["kind"],
                "name": record["name"],
                "sources": sources,
                "counts": counts,
                "status": authority_coverage_status(counts),
                "action": authority_coverage_action(counts),
            }
        )
    return out


def authority_coverage_markdown(records: list[dict], created_at: str) -> str:
    summary = Counter()
    for record in records:
        summary[record["status"]] += 1
    summary_rows = [["状态", "对象数"]] + [[key, str(value)] for key, value in summary.most_common()]

    rows = [["类型", "对象", "L1", "L2", "L3", "L4", "状态", "下一步"]]
    detail_sections = []
    for record in records:
        counts = record["counts"]
        rows.append(
            [
                record["kind"],
                record["name"],
                str(counts.get("L1", 0)),
                str(counts.get("L2", 0)),
                str(counts.get("L3", 0)),
                str(counts.get("L4", 0)),
                record["status"],
                record["action"],
            ]
        )
        source_rows = [["编号", "级别", "日期", "来源", "标题"]]
        for idx, row in enumerate(record["sources"][:5], 1):
            source_rows.append([f"S{idx}", row_authority_label(row), row["published_at"] or "日期不详", row["account"] or "", f"《{row['title']}》"])
        if len(source_rows) == 1:
            source_rows.append(["-", "-", "-", "-", "未检索到来源"])
        detail_sections.append(f"### {record['kind']}：{record['name']}\n\n{markdown_table(source_rows)}")

    return f"""# 权威事实覆盖仪表盘

生成时间：{created_at}

本页用于判断核心人物、核心事件和制度主题是否已有 L1-L3 权威公开来源支撑。它服务于史实核验和研究卡人工校订，不替代正式史实结论。

## 总体判断

{markdown_table(summary_rows)}

## 覆盖总表

{markdown_table(rows)}

## 逐项来源

{chr(10).join(detail_sections)}

## 使用规则

1. `可优先用于事实核验` 表示已有 L1 来源，但正式使用仍需打开 raw 原文核对。
2. `可作权威佐证，仍需补 L1` 表示已有 L2/L3，适合写研究线索，不宜直接作最终定论。
3. `仅有样本线索` 表示主要来自公众号 L4，只能提示方向，不能作为史实定本。
4. 每次补入新权威来源后，重新运行 `kb authority-coverage`，再更新人物卡和事件卡。
"""


def research_dossier_index_body(created: list[Path], created_at: str) -> str:
    rows = [["人物", "档案路径"]]
    for path in created:
        rows.append([path.stem, f"`{path}`"])
    return f"""# 核心人物研究档案索引

生成时间：{created_at}

本页汇总“盟参”当前已生成的核心人物研究档案。人物档案用于盟史研究、文史纪念写作、口径核验和后续人工校订。

{markdown_table(rows)}

## 使用原则

- 档案中的事实线索必须回到 raw 原文核验后使用。
- 没有明确来源支撑的生平、职务、时间和评价不得直接写入正式稿。
- 上海地方史线索与全国民盟史主线应分别整理，再决定写作角度。
"""


def event_research_dossier_index_body(created: list[Path], created_at: str) -> str:
    rows = [["事件", "档案路径"]]
    for path in created:
        rows.append([path.stem, f"`{path}`"])
    return f"""# 核心事件研究档案索引

生成时间：{created_at}

本页汇总“盟参”当前已生成的核心事件研究档案。事件档案用于盟史研究、文史纪念写作、讲话素材、口径核验和后续人工校订。

{markdown_table(rows)}

## 使用原则

- 档案中的事件线索必须回到 raw 原文核验后使用。
- 事件日期、会议名称、组织关系和人物职务不得只凭机器摘录定稿。
- 全国民盟史主线、上海地方史线索和纪念性报道应分别整理。
"""


def command_build_research_dossiers(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    names = RESEARCH_DOSSIER_SETS[args.set]
    created_at = now_iso()
    created: list[Path] = []
    is_event_set = args.set == "core-events"
    for name in names[: args.limit or None]:
        if is_event_set:
            rows = event_dossier_rows(root, name, args.top_k)
            created.append(write_event_research_dossier(root, name, rows, created_at))
        else:
            rows = person_dossier_rows(root, name, args.top_k)
            created.append(write_person_research_dossier(root, name, rows, created_at))
    index_path = (event_dossier_dir(root) if is_event_set else dossier_dir(root)) / "索引.md"
    index_path.write_text(
        event_research_dossier_index_body(created, created_at) if is_event_set else research_dossier_index_body(created, created_at),
        encoding="utf-8",
    )
    append_wiki_log(root, f"生成{'核心事件' if is_event_set else '核心人物'}研究档案：{len(created)} 个")
    log_operation(root, "build-research-dossiers", "ok", f"{len(created)} dossiers", {"set": args.set})
    print(f"Created/updated research dossiers: {len(created)}")
    print(index_path)
    for path in created:
        print(path)
    return 0


def command_authority_coverage(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    created_at = now_iso()
    records = authority_coverage_records(root, args.top_k)
    path = root / "wiki" / "研究助手" / "权威事实覆盖仪表盘.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(authority_coverage_markdown(records, created_at), encoding="utf-8")
    append_wiki_log(root, f"生成权威事实覆盖仪表盘：{path.relative_to(root)}")
    log_operation(root, "authority-coverage", "ok", f"{len(records)} records", {"top_k": args.top_k, "output": str(path)})
    print(path)
    return 0


def command_build_cards(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    sets = [args.set] if args.set != "all" else ["people", "shanghai-history-people", "events", "places"]
    created: list[Path] = []
    for set_name in sets:
        spec = CARD_SETS[set_name]
        for item in spec["items"][: args.limit or None]:
            rows = search_rows(root, CARD_QUERY_OVERRIDES.get(item, item), args.top_k)
            body = card_body(item, spec["page_type"], rows)
            created.append(write_wiki_page(root, item, spec["page_type"], body, rows))
    append_wiki_log(root, f"生成研究卡片：{len(created)} 个")
    log_operation(root, "build-cards", "ok", f"{len(created)} cards", {"set": args.set})
    print(f"Created/updated cards: {len(created)}")
    for path in created:
        print(path)
    return 0


def command_curate_cards(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    updated = apply_priority_card_status(root)
    print(f"Priority cards marked: {updated}")
    return 0


def command_build_packs(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    created = []
    for topic, query, mode in TOPIC_PACKS:
        rows = search_rows(root, query, args.top_k)
        body = topic_pack_body(topic, mode, rows)
        created.append(write_wiki_page(root, topic, "topic", body, rows))
    append_wiki_log(root, f"生成专题研究包：{len(created)} 个")
    log_operation(root, "build-packs", "ok", f"{len(created)} packs")
    print(f"Created/updated topic packs: {len(created)}")
    for path in created:
        print(path)
    return 0


def command_build_writing_workflows(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    created = []
    for name, spec in WRITING_WORKFLOWS.items():
        rows = search_rows(root, str(spec["query"]), args.top_k)
        body = writing_workflow_body(name, spec, rows)
        created.append(write_wiki_page(root, name, "writing", body, rows))
    append_wiki_log(root, f"生成微信公众号写作工作流：{len(created)} 个")
    log_operation(root, "build-writing-workflows", "ok", f"{len(created)} workflows")
    print(f"Created/updated writing workflows: {len(created)}")
    for path in created:
        print(path)
    return 0


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
