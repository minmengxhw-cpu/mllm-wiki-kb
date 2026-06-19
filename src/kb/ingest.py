from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


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
