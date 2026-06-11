from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import html
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import textwrap
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


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

OBSIDIAN_MAPPINGS = {
    "wiki/index.md": "00-总索引/首页.md",
    "wiki/log.md": "00-总索引/操作日志.md",
    "wiki/研究助手": "01-研究助手",
    "wiki/人物": "10-人物",
    "wiki/组织": "20-组织",
    "wiki/事件": "30-事件会议",
    "wiki/会议": "30-事件会议",
    "wiki/盟史": "40-盟史",
    "wiki/参政议政": "50-参政议政",
    "wiki/思想宣传": "60-思想宣传",
    "wiki/社会服务": "70-社会服务",
    "wiki/主题教育": "80-主题教育",
    "wiki/传统教育基地": "90-传统教育基地",
    "wiki/文稿素材": "91-文稿素材",
    "wiki/口述史": "92-口述史",
}

SUPPORTED_EXTS = {".md", ".txt", ".html", ".htm", ".json"}
NOISE_PATTERNS = [
    r"点击上方.*?关注我们",
    r"长按二维码.*",
    r"喜欢就给我们点个赞吧.*",
    r"往期回顾.*",
    r"阅读原文.*",
]


@dataclass
class ArticleDoc:
    title: str
    account: str | None
    author: str | None
    published_at: str | None
    source_url: str | None
    text: str
    file_type: str


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def project_root_from_args(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    env_value = os.environ.get("KB_PROJECT_ROOT")
    if env_value:
        return Path(env_value).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def db_path(root: Path) -> Path:
    return root / "index" / "kb.sqlite"


def connect_db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(root))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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


def ensure_schema_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(wiki_pages)").fetchall()}
    additions = {
        "review_status": "ALTER TABLE wiki_pages ADD COLUMN review_status TEXT NOT NULL DEFAULT '待校订'",
        "priority_card": "ALTER TABLE wiki_pages ADD COLUMN priority_card INTEGER NOT NULL DEFAULT 0",
        "card_batch": "ALTER TABLE wiki_pages ADD COLUMN card_batch TEXT",
    }
    for name, sql in additions.items():
        if name not in columns:
            conn.execute(sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_pages_review_status ON wiki_pages(review_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_pages_priority_card ON wiki_pages(priority_card)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_vectors (
            chunk_id INTEGER PRIMARY KEY,
            article_id INTEGER NOT NULL,
            vector_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(chunk_id) REFERENCES article_chunks(id) ON DELETE CASCADE,
            FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunk_vectors_article_id ON chunk_vectors(article_id)")


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


def iter_input_files(input_dir: Path) -> Iterable[Path]:
    for path in sorted(input_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTS:
            yield path


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", "\n", text)
    text = re.sub(r"(?is)<style.*?</style>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</h[1-6]\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    return html.unescape(text)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern in NOISE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.S)
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if s.startswith("![]("):
            continue
        if s.startswith("> 原文地址"):
            continue
        if "mp.weixin.qq.com/s?" in s:
            continue
        if "margin:" in s and "font-family" in s:
            continue
        lines.append(s)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_doc(path: Path, input_root: Path) -> ArticleDoc:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    suffix = path.suffix.lower()
    source_url = None
    if suffix in {".html", ".htm"}:
        text = strip_html(raw)
    elif suffix == ".json":
        data = json.loads(raw)
        text = data.get("content") or data.get("text") or data.get("html") or json.dumps(data, ensure_ascii=False)
        if "<" in text and ">" in text:
            text = strip_html(text)
        source_url = data.get("source_url") or data.get("url")
    else:
        text = raw

    title = None
    lines = text.splitlines()
    for i, line in enumerate(lines[:40]):
        if i + 1 < len(lines) and set(lines[i + 1].strip()) == {"="}:
            title = line.strip()
            break
    if not title:
        for line in lines[:60]:
            s = line.strip().lstrip("#").strip()
            if s and not s.startswith("* {") and "font-family" not in s:
                title = s[:120]
                break
    if not title:
        title = path.stem.replace("_", " ")

    published_at = None
    author = None
    account = None
    date_re = re.compile(r"(20\d{2})-(\d{2})-(\d{2})(?:\s+(\d{2}:\d{2}))?")
    for line in lines[:80]:
        m = date_re.search(line)
        if m:
            published_at = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            before = line[: m.start()].strip()
            parts = [p for p in re.split(r"\s+", before.replace("原创", "").strip()) if p]
            if parts:
                if len(parts) >= 2:
                    author = parts[-2]
                    account = parts[-1]
                else:
                    account = parts[-1]
            break
    if not account:
        try:
            account = path.relative_to(input_root).parts[0]
        except Exception:
            account = None
    if not source_url:
        m = re.search(r"https://mp\.weixin\.qq\.com/[^\)\]\s]+", raw)
        source_url = m.group(0) if m else None

    clean = normalize_text(text)
    return ArticleDoc(
        title=title,
        account=account,
        author=author,
        published_at=published_at,
        source_url=source_url,
        text=clean,
        file_type=suffix.lstrip("."),
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def slugify(value: str, max_len: int = 60) -> str:
    value = re.sub(r"[\\/:*?\"<>|#\[\]\n\r\t]", "_", value).strip(" ._")
    value = re.sub(r"_+", "_", value)
    return value[:max_len] or "untitled"


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 120) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paras:
        if len(current) + len(para) + 2 <= max_chars:
            current = f"{current}\n\n{para}".strip()
        else:
            if current:
                chunks.append(current)
            if len(para) <= max_chars:
                current = para
            else:
                for i in range(0, len(para), max_chars - overlap):
                    chunks.append(para[i : i + max_chars])
                current = ""
    if current:
        chunks.append(current)
    return chunks or [text[:max_chars]]


def write_raw(root: Path, doc: ArticleDoc, content_hash: str) -> Path:
    account = slugify(doc.account or "unknown")
    date = doc.published_at or "undated"
    name = f"{date}_{slugify(doc.title)}_{content_hash[:12]}.md"
    raw_dir = root / "data" / "raw" / account
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / name
    body = [
        "---",
        f'title: "{doc.title.replace(chr(34), chr(39))}"',
        f'account: "{(doc.account or "").replace(chr(34), chr(39))}"',
        f'author: "{(doc.author or "").replace(chr(34), chr(39))}"',
        f'published_at: "{doc.published_at or ""}"',
        f'source_url: "{doc.source_url or ""}"',
        f"content_hash: {content_hash}",
        "---",
        "",
        doc.text,
        "",
    ]
    raw_path.write_text("\n".join(body), encoding="utf-8")
    return raw_path


def quarantine_file(root: Path, source: Path, reason: str) -> Path:
    dest = root / "data" / "quarantine" / f"{now_iso().replace(':', '-')}_{source.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    (dest.with_suffix(dest.suffix + ".reason.txt")).write_text(reason, encoding="utf-8")
    return dest


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
                    raw_path = write_raw(root, doc, content_hash)
                    cur = conn.execute(
                        """
                        INSERT INTO articles(title, account, author, published_at, source_path, raw_path, source_url,
                                             content_hash, imported_at, file_type, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            doc.title,
                            doc.account,
                            doc.author,
                            doc.published_at,
                            str(path),
                            str(raw_path),
                            doc.source_url,
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
            {"input": str(input_dir), "limit": limit, "dry_run": args.dry_run},
        )
        if not args.dry_run:
            append_wiki_log(root, f"导入测试样本：imported={imported} skipped={skipped} failed={failed}")
    finally:
        conn.close()
    print(f"Input: {input_dir}")
    print(f"Limit: {limit}")
    print(f"Dry run: {args.dry_run}")
    print(f"Imported/planned: {imported}")
    print(f"Skipped duplicate: {skipped}")
    print(f"Failed: {failed}")
    print("Preview:")
    for row in preview_rows[:10]:
        print(f"  [{row[3]}] {row[0]} | {row[1]} | {row[2]}")
    return 0 if failed == 0 else 1


def command_check(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
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


def generated_region(content: str) -> str:
    start = "<!-- KB-GENERATED:START -->"
    end = "<!-- KB-GENERATED:END -->"
    if start in content and end in content:
        return content[content.index(start) : content.index(end) + len(end)]
    return content


def merge_generated(existing: str, new_content: str) -> str:
    start = "<!-- KB-GENERATED:START -->"
    end = "<!-- KB-GENERATED:END -->"
    if start not in existing or end not in existing:
        return new_content
    new_region = generated_region(new_content)
    before = existing[: existing.index(start)]
    after = existing[existing.index(end) + len(end) :]
    return before + new_region + after


def merge_generated_with_fresh_metadata(existing: str, new_content: str) -> str:
    end = "<!-- KB-GENERATED:END -->"
    if end not in existing or end not in new_content:
        return new_content
    return new_content[: new_content.index(end) + len(end)] + existing[existing.index(end) + len(end) :]


def sync_file(src: Path, dest: Path, dry_run: bool) -> str:
    action = "create"
    if dest.exists():
        action = "update"
    if dry_run:
        return action
    dest.parent.mkdir(parents=True, exist_ok=True)
    new_content = src.read_text(encoding="utf-8")
    if dest.exists():
        backup = dest.with_suffix(dest.suffix + f".bak-{datetime.now().strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(dest, backup)
        existing = dest.read_text(encoding="utf-8")
        dest.write_text(merge_generated(existing, new_content), encoding="utf-8")
    else:
        dest.write_text(new_content, encoding="utf-8")
    return action


def command_obsidian_sync(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    vault = Path(args.vault).expanduser().resolve()
    actions = []
    for src_rel, dest_rel in OBSIDIAN_MAPPINGS.items():
        src = root / src_rel
        dest_base = vault / dest_rel
        if src.is_file():
            actions.append((src, dest_base, sync_file(src, dest_base, args.dry_run)))
        elif src.is_dir():
            for md in sorted(src.rglob("*.md")):
                rel = md.relative_to(src)
                dest = dest_base / rel
                actions.append((md, dest, sync_file(md, dest, args.dry_run)))
    if not args.dry_run:
        sync_log = root / "obsidian" / "sync_log.md"
        with sync_log.open("a", encoding="utf-8") as f:
            f.write(f"\n## {now_iso()}\n\n")
            for src, dest, action in actions:
                f.write(f"- {action}: `{src}` -> `{dest}`\n")
    status = "dry-run" if args.dry_run else "ok"
    log_operation(root, "obsidian-sync", status, f"{len(actions)} files", {"vault": str(vault), "dry_run": args.dry_run})
    print(f"Vault: {vault}")
    print(f"Dry run: {args.dry_run}")
    print(f"Files: {len(actions)}")
    for src, dest, action in actions[:30]:
        print(f"  {action}: {src.relative_to(root)} -> {dest}")
    return 0


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


def rebuild_fts(root: Path) -> tuple[int, int]:
    conn = connect_db(root)
    try:
        conn.executescript((root / "schema.sql").read_text(encoding="utf-8"))
        conn.execute("DELETE FROM article_chunks_fts")
        rows = conn.execute(
            """
            SELECT c.id AS chunk_id, c.article_id, c.content, a.title, a.account, a.published_at, a.raw_path
            FROM article_chunks c
            JOIN articles a ON a.id = c.article_id
            ORDER BY c.article_id, c.chunk_index
            """
        ).fetchall()
        with conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO article_chunks_fts(content, title, account, published_at, raw_path, article_id, chunk_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["content"],
                        row["title"],
                        row["account"],
                        row["published_at"],
                        row["raw_path"],
                        row["article_id"],
                        row["chunk_id"],
                    ),
                )
        count = conn.execute("SELECT COUNT(*) FROM article_chunks_fts").fetchone()[0]
        return len(rows), count
    finally:
        conn.close()


def vector_tokens(text: str) -> list[str]:
    text = re.sub(r"\s+", "", text)
    tokens = []
    for size in (2, 3, 4):
        for i in range(0, max(0, len(text) - size + 1)):
            token = text[i : i + size]
            if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", token):
                tokens.append(token)
    tokens.extend(query_terms(text))
    return tokens


def text_vector(text: str, dims: int = 256) -> list[float]:
    vec = [0.0] * dims
    for token in vector_tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:2], "big") % dims
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm:
        vec = [round(v / norm, 6) for v in vec]
    return vec


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def rebuild_vectors(root: Path) -> tuple[int, int]:
    conn = connect_db(root)
    try:
        ensure_schema_columns(conn)
        conn.execute("DELETE FROM chunk_vectors")
        rows = conn.execute(
            """
            SELECT c.id AS chunk_id, c.article_id, c.content, a.title
            FROM article_chunks c
            JOIN articles a ON a.id = c.article_id
            ORDER BY c.id
            """
        ).fetchall()
        with conn:
            for row in rows:
                vector = text_vector(f"{row['title']}\n{row['content']}")
                conn.execute(
                    "INSERT INTO chunk_vectors(chunk_id, article_id, vector_json, updated_at) VALUES (?, ?, ?, ?)",
                    (row["chunk_id"], row["article_id"], json.dumps(vector, separators=(",", ":")), now_iso()),
                )
        count = conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
        return len(rows), count
    finally:
        conn.close()


def semantic_rows(root: Path, query: str, top_k: int) -> list[sqlite3.Row]:
    conn = connect_db(root)
    try:
        ensure_schema_columns(conn)
        if conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0] == 0:
            conn.close()
            rebuild_vectors(root)
            conn = connect_db(root)
        qvec = text_vector(query)
        rows = conn.execute(
            """
            SELECT v.chunk_id, v.article_id, v.vector_json, c.content, a.title, a.account, a.published_at, a.raw_path
            FROM chunk_vectors v
            JOIN article_chunks c ON c.id = v.chunk_id
            JOIN articles a ON a.id = v.article_id
            """
        ).fetchall()
        scored = []
        for row in rows:
            score = cosine_similarity(qvec, json.loads(row["vector_json"]))
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        out = []
        for score, row in scored[:top_k]:
            out.append(
                {
                    "article_id": row["article_id"],
                    "chunk_id": row["chunk_id"],
                    "title": row["title"],
                    "account": row["account"],
                    "published_at": row["published_at"],
                    "raw_path": row["raw_path"],
                    "snippet": row["content"][:220],
                    "score": score,
                }
            )
        return [dict_to_row(row) for row in out]
    finally:
        conn.close()


def dict_to_row(data: dict) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    keys = list(data.keys())
    conn.execute("CREATE TABLE t (" + ",".join(f"{key} TEXT" for key in keys) + ")")
    conn.execute("INSERT INTO t VALUES (" + ",".join("?" for _ in keys) + ")", [data[key] for key in keys])
    row = conn.execute("SELECT * FROM t").fetchone()
    conn.close()
    return row


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


def fts_query(value: str) -> str:
    terms = [t for t in re.split(r"\s+", value.strip()) if t]
    if not terms:
        return value
    return " AND ".join(f'"{t}"' for t in terms)


def query_terms(value: str) -> list[str]:
    split_terms = [t for t in re.split(r"\s+", value.strip()) if t]
    if len(split_terms) > 1:
        return split_terms
    known = [
        "上海民盟",
        "中国民主同盟",
        "民盟中央",
        "盟史",
        "资源",
        "活化",
        "传统教育基地",
        "主题教育",
        "基层",
        "落实",
        "机制",
        "参政为公",
        "实干为民",
        "参政议政",
        "建言",
        "提案",
        "社情民意",
        "写作",
        "素材",
        "人物",
        "采访",
        "专访",
        "五一口号",
        "旧政协",
        "人民政协",
        "新政协",
        "李闻",
        "多党合作",
        "统一战线",
        "政治交接",
        "自身建设",
        "社会服务",
        "黄丝带",
        "烛光行动",
        "毕节",
        "传统",
        "先贤",
        "张澜",
        "沈钧儒",
        "黄炎培",
        "史良",
        "特园",
    ]
    terms = [term for term in known if term in value]
    if terms:
        return terms
    return split_terms or [value]


def search_rows(root: Path, query: str, top_k: int = 20) -> list[sqlite3.Row]:
    init_db(root)
    conn = connect_db(root)
    try:
        fts_count = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='article_chunks_fts'"
        ).fetchone()
        if not fts_count or conn.execute("SELECT COUNT(*) FROM article_chunks_fts").fetchone()[0] == 0:
            conn.close()
            rebuild_fts(root)
            conn = connect_db(root)
        q = fts_query(query)
        rows = conn.execute(
            """
            SELECT article_id, chunk_id, title, account, published_at, raw_path,
                   snippet(article_chunks_fts, 0, '[', ']', '...', 18) AS snippet,
                   bm25(article_chunks_fts) AS score
            FROM article_chunks_fts
            WHERE article_chunks_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (q, top_k),
        ).fetchall()
        if not rows:
            terms = query_terms(query)
            if terms:
                where = " AND ".join(["c.content LIKE ?" for _ in terms])
                params = [f"%{term}%" for term in terms]
            else:
                where = "c.content LIKE ?"
                params = [f"%{query}%"]
            rows = conn.execute(
                f"""
                SELECT c.article_id, c.id AS chunk_id, a.title, a.account, a.published_at, a.raw_path,
                       substr(c.content, 1, 220) AS snippet,
                       0 AS score
                FROM article_chunks c
                JOIN articles a ON a.id = c.article_id
                WHERE {where}
                ORDER BY a.published_at DESC, c.article_id, c.chunk_index
                LIMIT ?
                """,
                (*params, top_k),
            ).fetchall()
        if not rows:
            terms = query_terms(query)
            if terms:
                where = " OR ".join(["c.content LIKE ?" for _ in terms])
                score_expr = " + ".join(["CASE WHEN c.content LIKE ? THEN 1 ELSE 0 END" for _ in terms])
                params = [f"%{term}%" for term in terms]
                score_params = [f"%{term}%" for term in terms]
                rows = conn.execute(
                    f"""
                    SELECT c.article_id, c.id AS chunk_id, a.title, a.account, a.published_at, a.raw_path,
                           substr(c.content, 1, 220) AS snippet,
                           ({score_expr}) AS score
                    FROM article_chunks c
                    JOIN articles a ON a.id = c.article_id
                    WHERE {where}
                    ORDER BY score DESC, a.published_at DESC, c.article_id, c.chunk_index
                    LIMIT ?
                    """,
                    (*score_params, *params, top_k),
                ).fetchall()
        elif len(rows) < top_k:
            terms = query_terms(query)
            if terms:
                existing_chunk_ids = {int(row["chunk_id"]) for row in rows if row["chunk_id"] is not None}
                where = " OR ".join(["c.content LIKE ?" for _ in terms])
                score_expr = " + ".join(["CASE WHEN c.content LIKE ? THEN 1 ELSE 0 END" for _ in terms])
                params = [f"%{term}%" for term in terms]
                score_params = [f"%{term}%" for term in terms]
                exclude = ""
                exclude_params: list[int] = []
                if existing_chunk_ids:
                    placeholders = ",".join(["?"] * len(existing_chunk_ids))
                    exclude = f" AND c.id NOT IN ({placeholders})"
                    exclude_params = sorted(existing_chunk_ids)
                supplement = conn.execute(
                    f"""
                    SELECT c.article_id, c.id AS chunk_id, a.title, a.account, a.published_at, a.raw_path,
                           substr(c.content, 1, 220) AS snippet,
                           ({score_expr}) AS score
                    FROM article_chunks c
                    JOIN articles a ON a.id = c.article_id
                    WHERE ({where}){exclude}
                    ORDER BY score DESC, a.published_at DESC, c.article_id, c.chunk_index
                    LIMIT ?
                    """,
                    (*score_params, *params, *exclude_params, top_k - len(rows)),
                ).fetchall()
                rows = list(rows) + list(supplement)
        if len(rows) < top_k:
            existing_chunk_ids = {str(row["chunk_id"]) for row in rows if row["chunk_id"] is not None}
            semantic = semantic_rows(root, query, top_k)
            for row in semantic:
                if str(row["chunk_id"]) in existing_chunk_ids:
                    continue
                rows = list(rows) + [row]
                existing_chunk_ids.add(str(row["chunk_id"]))
                if len(rows) >= top_k:
                    break
    finally:
        conn.close()
    return rows


def clean_snippet(value: str, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = value.replace("[", "").replace("]", "")
    return value[:limit]


def row_source_line(row: sqlite3.Row, idx: int) -> str:
    return (
        f"[S{idx}] {row['account']}，{row['published_at'] or '日期不详'}，"
        f"《{row['title']}》，raw: `{row['raw_path']}`"
    )


def row_source_md(row: sqlite3.Row, idx: int) -> str:
    raw_path = row["raw_path"] or ""
    return (
        f"| S{idx} | {row['account'] or ''} | {row['published_at'] or '日期不详'} | "
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


def load_formulations(root: Path) -> list[dict]:
    return load_jsonl(staff_index_dir(root) / "formulations.jsonl")


def load_blacklist(root: Path) -> list[dict]:
    path = staff_index_dir(root) / "blacklist.csv"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def value_variants(item: dict) -> list[str]:
    values = []
    for key in ["name", "term", "canonical", "pattern"]:
        value = str(item.get(key) or "").strip()
        if value:
            values.append(value)
    for key in ["aliases", "variants"]:
        raw = item.get(key)
        if isinstance(raw, list):
            values.extend(str(v).strip() for v in raw if str(v).strip())
        elif raw:
            values.extend(v.strip() for v in str(raw).split("|") if v.strip())
    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def match_staff_items(items: list[dict], text: str, limit: int = 12) -> list[dict]:
    out = []
    for item in items:
        if any(value and value in text for value in value_variants(item)):
            out.append(item)
        if len(out) >= limit:
            break
    return out


def match_blacklist(root: Path, text: str) -> list[dict]:
    matches = []
    for item in load_blacklist(root):
        pattern = (item.get("pattern") or "").strip()
        if pattern and pattern in text:
            matches.append(item)
    return matches


def staff_severity_rank(value: str | None) -> int:
    return {"blocker": 0, "high": 1, "medium": 2, "low": 3}.get((value or "").strip(), 4)


def severity_label(value: str | None) -> str:
    return {
        "blocker": "必须修改",
        "high": "高风险",
        "medium": "中风险",
        "low": "提示",
    }.get((value or "").strip(), "提示")


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
        "check": "上海民盟 民盟 盟史 口径 核验",
    }
    return f"{topic} {extras.get(mode, '')}".strip()


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


def merge_search_rows(row_groups: list[list[sqlite3.Row]], top_k: int) -> list[sqlite3.Row]:
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
                return rows
    if len(rows) >= top_k:
        return rows
    for group in row_groups:
        for row in group:
            article_id = str(row["article_id"])
            if article_id in seen_articles:
                continue
            rows.append(row)
            seen_articles.add(article_id)
            if len(rows) >= top_k:
                return rows
    return rows


def staff_search_rows(root: Path, mode: str, topic: str, top_k: int) -> list[sqlite3.Row]:
    if mode == "topic":
        queries = topic_query_variants(topic)
    else:
        queries = [staff_query(mode, topic)]
    groups = [search_rows(root, query, top_k) for query in queries]
    return merge_search_rows(groups, top_k)


def staff_card_matches(root: Path, topic: str, limit: int = 8) -> list[sqlite3.Row]:
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


def staff_draft_body(root: Path, topic: str, rows: list[sqlite3.Row]) -> str:
    formulations = match_staff_items(load_formulations(root), topic)
    return f"""# 盟参 /稿：{topic}

## 结论

- 本次为“文稿素材包”，不是最终成稿；已检索到 {len(rows)} 条片段，覆盖 {len({int(row['article_id']) for row in rows}) if rows else 0} 篇来源文章。
- 可先按“同题历史稿 + 口径要点 + 常用结构 + 风险提示”进入起草；事实性句子必须保留 [S] 来源或标注 [待核]。
- 若要生成正式微信公众号文章，请继续补充时间、地点、人物职务、主办承办单位、活动流程、讲话要点和图片说明。

## 素材

### 同题历史稿

| 编号 | 公众号 | 日期 | 标题 | raw 原文 |
|---|---|---|---|---|
{citation_table(rows)}

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
    return f"""# 盟参 /史：{topic}

## 结论

- 本次为“史实卡片/研究入口”，已检索到 {len(rows)} 条片段，覆盖 {len({int(row['article_id']) for row in rows}) if rows else 0} 篇来源文章。
- 当前输出只能作为研究线索；涉及年份、会议、职务、组织名称和地点，必须打开 raw 原文和权威资料复核。
- 若来源不足或存在争议，相关表述一律按 [待核] 处理。

## 素材

### 已有卡片

{staff_cards_block(root, topic)}

### 种子实体库

{staff_entity_lines(entities)}

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


def draft_has_citation(text: str) -> bool:
    return bool(re.search(r"\[S\d+\]|raw:|raw 原文|来源|出处", text))


def staff_check_issues(root: Path, text: str) -> list[dict]:
    issues = []
    for item in match_blacklist(root, text):
        issues.append(
            {
                "severity": item.get("severity") or "medium",
                "category": item.get("category") or "口径",
                "pattern": item.get("pattern") or "",
                "suggestion": item.get("canonical") or "请回到规范表述核定",
                "note": item.get("note") or "",
            }
        )
    if len(text.strip()) >= 20 and not draft_has_citation(text):
        issues.append(
            {
                "severity": "high",
                "category": "引用",
                "pattern": "整篇材料未见来源编号或出处",
                "suggestion": "事实性句子补 [S] 来源；无法溯源的内容标 [待核]",
                "note": "盟参输出要求事实性表述可溯源。",
            }
        )
    if re.search(r"19\d{2}年|20\d{2}年|\d{4}[-.]\d{1,2}[-.]\d{1,2}", text) and not draft_has_citation(text):
        issues.append(
            {
                "severity": "medium",
                "category": "史实",
                "pattern": "出现具体年份/日期但未见出处",
                "suggestion": "逐条补 raw 原文、档案或权威资料出处",
                "note": "历史类和公文类日期必须核验。",
            }
        )
    issues.sort(key=lambda item: staff_severity_rank(item.get("severity")))
    return issues


def issue_table(issues: list[dict]) -> str:
    if not issues:
        return "| - | - | - | 未发现种子黑名单命中 | 仍需人工终审 |"
    lines = []
    for idx, item in enumerate(issues, 1):
        lines.append(
            f"| {idx} | {severity_label(item.get('severity'))} | {item.get('category') or ''} | "
            f"{item.get('pattern') or ''} | {item.get('suggestion') or ''} |"
        )
    return "\n".join(lines)


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
            body = staff_draft_body(root, topic, rows)
            title = f"盟参文稿素材：{topic}"
        elif args.staff_command == "history":
            body = staff_history_body(root, topic, rows)
            title = f"盟参史实卡：{topic}"
        elif args.staff_command == "topic":
            body = staff_topic_body(root, topic, rows)
            title = f"盟参选题查重：{topic}"
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
    {"type": "member_achievement", "name": "盟员履职/成果荣誉", "keywords": ["荣获", "获评", "获奖", "喜获", "当选", "入选", "成果", "团队", "院士", "科学技术奖", "五一劳动奖章", "创新争先", "典型在身边", "履职风采"]},
    {"type": "person_profile", "name": "人物采访/人物风采", "keywords": ["人物", "采访", "风采", "盟员风采", "专访", "故事", "诞辰", "纪念", "先生"]},
    {"type": "history_commemoration", "name": "文史纪念", "keywords": ["盟史", "文史", "纪念", "先贤", "旧政协", "新政协", "五一口号", "李闻", "钩沉", "口述史", "传统教育基地"]},
    {"type": "history_research", "name": "盟史研究", "keywords": ["盟史研究", "民盟历史", "档案", "史料", "史实", "考证", "历史资料", "理论和盟史"]},
    {"type": "policy_advice", "name": "参政议政", "keywords": ["参政议政", "提案", "社情民意", "建言", "调研", "建议", "政协", "两会", "履职"]},
    {"type": "theme_education", "name": "主题教育", "keywords": ["主题教育", "参政为公", "实干为民", "凝心铸魂", "学规定", "强作风", "树形象", "政治共识", "学习贯彻习近平", "学习贯彻中共"]},
    {"type": "organization_building", "name": "组织建设", "keywords": ["组织建设", "基层组织", "支部", "区委", "委员会", "换届", "盟员之家", "新盟员", "入盟"]},
    {"type": "social_service", "name": "社会服务", "keywords": ["社会服务", "帮扶", "乡村振兴", "烛光行动", "黄丝带", "公益", "医疗", "教育帮扶"]},
    {"type": "notice_info", "name": "通知公告/信息发布", "keywords": ["通知", "公告", "名单", "公示", "目录", "招聘", "征集", "报名", "结果出炉"]},
    {"type": "commentary_theory", "name": "评论综述/理论文章", "keywords": ["综述", "理论", "评论", "学习体会", "心得", "观察", "解读", "述评"]},
]

ARTICLE_TYPE_NAMES = {item["type"]: item["name"] for item in ARTICLE_TYPE_RULES} | {"other": "其他/待判"}

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


def corpus_text_for_row(row: sqlite3.Row) -> str:
    return " ".join([str(row["title"] or ""), str(row["account"] or ""), str(row["sample_text"] or "")])


def classify_article(title: str, account: str | None, text: str) -> tuple[str, int, list[str]]:
    haystack = f"{title}\n{account or ''}\n{text[:1600]}"
    scored = []
    for rule in ARTICLE_TYPE_RULES:
        matched = [kw for kw in rule["keywords"] if kw in haystack]
        if matched:
            title_hits = sum(2 for kw in matched if kw in title)
            scored.append((len(matched) + title_hits, rule["type"], matched))
    if not scored:
        return "other", 0, []
    scored.sort(key=lambda item: item[0], reverse=True)
    score, article_type, matched = scored[0]
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
    is_writing_sample = row["account"] == "上海民盟" and year >= "2023" and article_type in {
        "activity_report", "meeting_report", "person_profile", "history_commemoration",
        "policy_advice", "theme_education", "leadership_speech",
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
        "can_be_formulation_source": row["account"] in {"上海民盟", "中国民主同盟"} and year >= "2023",
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
    recent_sh = [label for label in labels if label["account"] == "上海民盟" and label["year"] >= "2023"]
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
    samples = [label for label in labels if label["account"] == "上海民盟" and label["year"] >= "2023" and label["is_writing_sample"]]
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
                    "suggested_type": "",
                    "review_result": "",
                    "review_note": "",
                    "raw_path": row["raw_path"] or "",
                }
            )


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


def command_corpus_audit(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    labels = load_article_labels(root)
    rows = corpus_review_rows(labels, args.per_type, args.low_confidence, args.other)
    created_at = now_iso()
    out_dir = corpus_dir(root)
    reports = report_dir(root)
    write_corpus_review_csv(out_dir / "classification_review.csv", rows)
    (reports / "微信公众号文章分类抽检表.md").write_text(corpus_review_markdown(rows, created_at), encoding="utf-8")
    (reports / "微信公众号语料库人工校订说明.md").write_text(corpus_review_guide_markdown(created_at), encoding="utf-8")
    log_operation(root, "corpus-audit", "ok", f"review samples {len(rows)}", {"output": str(out_dir / "classification_review.csv")})
    print(f"Review samples: {len(rows)}")
    print(f"CSV: {out_dir / 'classification_review.csv'}")
    print(f"Report: {reports / '微信公众号文章分类抽检表.md'}")
    print(f"Guide: {reports / '微信公众号语料库人工校订说明.md'}")
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
    (reports / "上海民盟2023年以来写作样本库.md").write_text(writing_samples_markdown(labels, created_at), encoding="utf-8")
    (reports / "微信公众号文史盟史文章专题库.md").write_text(history_corpus_markdown(labels, created_at), encoding="utf-8")
    log_operation(root, "corpus", "ok", f"labeled {len(labels)} articles", {"output": str(out_dir)})
    print(f"Articles labeled: {len(labels)}")
    print(f"Labels: {out_dir / 'article_labels.jsonl'}")
    print(f"Types: {out_dir / 'article_types.json'}")
    print(f"Reports: {reports}")
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
kb assistant "五一口号在民盟史上的意义" --mode history --save
kb assistant "上海民盟盟史资源有哪些" --mode history --save
kb assistant "参政议政写作素材" --mode policy --save
kb assistant "主题教育基层落实机制" --mode theme --save
kb assistant "根据这份材料写上海民盟公众号文章" --mode writing
```

## 工作规则

1. 先检索本地来源，不凭记忆作史实判断。
2. 先输出证据和待核实点，再进入写作。
3. 历史研究区分全国民盟史主线与上海地方史实践。
4. 微信写作区分活动新闻、会议报道、人物采访、参政议政、主题教育、文史纪念等类型。
5. 正式发稿前必须核对人名、职务、组织、日期、地点和数字。

## 已有核心页面

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
    root = project_root_from_args(args.project_root)
    rows = search_rows(root, args.query, args.top_k)
    log_operation(root, "search", "ok", f"{len(rows)} results", {"query": args.query, "top_k": args.top_k})
    print(f"Query: {args.query}")
    print(f"Results: {len(rows)}")
    for idx, row in enumerate(rows, 1):
        print(f"\n{idx}. {row['title']}")
        print(f"   {row['account']} | {row['published_at']} | article_id={row['article_id']} chunk_id={row['chunk_id']}")
        print(f"   raw: {row['raw_path']}")
        print(f"   {row['snippet']}")
    return 0


def command_ask(args: argparse.Namespace) -> int:
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
        print("Would rebuild index, cards, topic packs, writing workflows, priority status, and Obsidian sync.")
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
    priority_count = apply_priority_card_status(root)
    sync_count = 0
    if args.vault:
        sync_args = argparse.Namespace(project_root=args.project_root, vault=args.vault, dry_run=False)
        sync_code = command_obsidian_sync(sync_args)
        sync_count = 0 if sync_code else len(list((Path(args.vault).expanduser()).rglob("*.md")))
    conn = connect_db(root)
    after_articles = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    message = f"articles {before_articles}->{after_articles}; indexed={indexed}; vectors={vector_indexed}; priority={priority_count}"
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
    if path_value:
        path = Path(path_value).expanduser()
        if not path.is_absolute():
            path = root / path
        return path
    if not title:
        raise ValueError("export requires --title or --path")
    conn = connect_db(root)
    try:
        row = conn.execute("SELECT path FROM wiki_pages WHERE title = ? ORDER BY updated_at DESC LIMIT 1", (title,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise ValueError(f"wiki page not found: {title}")
    return root / row["path"]


def markdown_to_plain_text(markdown: str) -> str:
    text = re.sub(r"(?s)^---\n.*?\n---\n", "", markdown)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text.strip()


def write_docx(path: Path, title: str, markdown: str) -> None:
    text = markdown_to_plain_text(markdown)
    paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    body = "".join(
        f"<w:p><w:r><w:t>{html.escape(p)}</w:t></w:r></w:p>"
        for p in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        zf.writestr("word/document.xml", document)


def write_pdf(path: Path, title: str, markdown: str) -> None:
    text = markdown_to_plain_text(markdown)
    lines = []
    for paragraph in text.splitlines():
        lines.extend(textwrap.wrap(paragraph, width=42) or [""])
    lines = lines[:42]
    stream_lines = ["BT", "/F1 12 Tf", "50 790 Td"]
    for i, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i:
            stream_lines.append("0 -17 Td")
        stream_lines.append(f"({escaped}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("utf-8")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    offsets = []
    content = bytearray(b"%PDF-1.4\n")
    for idx, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{idx} 0 obj\n".encode("ascii"))
        content.extend(obj)
        content.extend(b"\nendobj\n")
    xref = len(content)
    content.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    path.write_bytes(bytes(content))


def command_export(args: argparse.Namespace) -> int:
    root = project_root_from_args(args.project_root)
    try:
        src = page_path_for_export(root, args.title, args.path)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not src.exists():
        print(f"wiki page not found: {src}", file=sys.stderr)
        return 2
    markdown = src.read_text(encoding="utf-8")
    stem = slugify(args.output_name or src.stem, 80)
    requested_formats = args.format or ["all"]
    formats = ["markdown", "docx", "pdf"] if "all" in requested_formats else requested_formats
    outputs = []
    if "markdown" in formats:
        dest = root / "exports" / "markdown" / f"{stem}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(markdown, encoding="utf-8")
        outputs.append(dest)
    if "docx" in formats:
        dest = root / "exports" / "docx" / f"{stem}.docx"
        write_docx(dest, src.stem, markdown)
        outputs.append(dest)
    if "pdf" in formats:
        dest = root / "exports" / "pdf" / f"{stem}.pdf"
        write_pdf(dest, src.stem, markdown)
        outputs.append(dest)
    log_operation(root, "export", "ok", f"{len(outputs)} files", {"source": str(src), "outputs": [str(p) for p in outputs]})
    print(f"Exported: {src}")
    for path in outputs:
        print(path)
    return 0


def command_placeholder(name: str):
    def _inner(args: argparse.Namespace) -> int:
        root = project_root_from_args(args.project_root)
        log_operation(root, name, "placeholder", "not implemented in phase 1")
        print(f"`kb {name}` is a phase-2 placeholder. No changes made.")
        return 0

    return _inner


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
    p.set_defaults(func=command_import)

    p = sub.add_parser("check")
    p.set_defaults(func=command_check)

    p = sub.add_parser("obsidian-sync")
    p.add_argument("--vault", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=command_obsidian_sync)

    p = sub.add_parser("log")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=command_log)

    p = sub.add_parser("index")
    p.set_defaults(func=command_index)

    p = sub.add_parser("refresh")
    p.add_argument("--input", default="~/Downloads/微信公众号")
    p.add_argument("--vault", default="~/Library/Mobile Documents/iCloud~md~obsidian/Documents/MllmWiki")
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

    p = sub.add_parser("staff", help="盟参首席参谋入口：/稿 /史 /题 /核")
    staff_sub = p.add_subparsers(dest="staff_command", required=True)

    p_staff = staff_sub.add_parser("draft", help="/稿：文稿素材包")
    p_staff.add_argument("topic")
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
    p.set_defaults(func=command_corpus_audit)

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

    for name in ["brief"]:
        p = sub.add_parser(name)
        p.add_argument("args", nargs="*")
        p.set_defaults(func=command_placeholder(name))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
