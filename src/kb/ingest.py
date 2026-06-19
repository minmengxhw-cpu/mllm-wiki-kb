from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
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


def html_title(raw: str) -> str | None:
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    if not title_match:
        return None
    title = normalize_text(strip_html(title_match.group(1)))
    title = re.sub(r"\s*[-_]\s*[^-_]{2,40}$", "", title).strip()
    return title[:120] if title else None


def html_balanced_block(raw: str, tag: str, attr_pattern: str) -> str | None:
    start_match = re.search(rf"(?is)<{tag}\b(?=[^>]*{attr_pattern})[^>]*>", raw)
    if not start_match:
        return None
    depth = 1
    pos = start_match.end()
    token_re = re.compile(rf"(?is)<(/?){tag}\b[^>]*>")
    for match in token_re.finditer(raw, pos):
        if match.group(1):
            depth -= 1
            if depth == 0:
                return raw[start_match.start() : match.end()]
        else:
            depth += 1
    return raw[start_match.start() :]


def html_main_text(raw: str) -> str:
    candidates = []
    for tag, attr_pattern in [
        ("div", r"id=[\"'](?:ozoom|zoom|fontzoom)[\"']"),
        ("div", r"class=[\"'][^\"']*text_box[^\"']*[\"']"),
        ("div", r"class=[\"'][^\"']*Content[^\"']*[\"']"),
        ("div", r"(?:id|class)=[\"'][^\"']*(?:article|main|正文)[^\"']*[\"']"),
        ("founder-content", r""),
        ("article", r""),
        ("main", r""),
    ]:
        block = html_balanced_block(raw, tag, attr_pattern)
        if block:
            candidates.append(block)
    for pattern in [
        r"(?is)<article[^>]*>(.*?)</article>",
        r"(?is)<main[^>]*>(.*?)</main>",
        r"(?is)<founder-content[^>]*>(.*?)</founder-content>",
        r"(?is)<div[^>]+(?:class|id)=[\"'][^\"']*(?:content|article|main|正文)[^\"']*[\"'][^>]*>(.*?)</div>",
    ]:
        candidates.extend(match.group(1) for match in re.finditer(pattern, raw))
    if candidates:
        return max((normalize_text(strip_html(item)) for item in candidates), key=len)
    return normalize_text(strip_html(raw))


def fetch_url_text(url: str, timeout: int = 20, insecure: bool = False) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "mllm-wiki-kb/0.1 (+personal research; public source intake)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
        },
    )
    context = ssl._create_unverified_context() if insecure else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        payload = response.read()
    return payload.decode(charset, errors="ignore"), charset


def extract_doc_from_url(
    url: str,
    account: str | None = None,
    title: str | None = None,
    published_at: str | None = None,
    timeout: int = 20,
    insecure: bool = False,
) -> ArticleDoc:
    raw, _charset = fetch_url_text(url, timeout=timeout, insecure=insecure)
    parsed = urlparse(url)
    inferred_account = account or parsed.netloc
    text = html_main_text(raw) if "<" in raw and ">" in raw else normalize_text(raw)
    inferred_title = title or html_title(raw)
    if not inferred_title:
        for line in text.splitlines()[:30]:
            line = line.strip().lstrip("#").strip()
            if line:
                inferred_title = line[:120]
                break
    if not inferred_title:
        inferred_title = parsed.path.rstrip("/").split("/")[-1] or parsed.netloc
    if not published_at:
        match = re.search(r"(20\d{2})[-年.](\d{1,2})[-月.](\d{1,2})", raw[:5000])
        if match:
            published_at = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return ArticleDoc(
        title=inferred_title,
        account=inferred_account,
        author=None,
        published_at=published_at,
        source_url=url,
        text=text,
        file_type="url",
    )


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
        text = html_main_text(raw)
        source_url_match = re.search(r"(?is)<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']", raw)
        if source_url_match:
            source_url = html.unescape(source_url_match.group(1)).strip()
    elif suffix == ".json":
        data = json.loads(raw)
        text = data.get("content") or data.get("text") or data.get("html") or json.dumps(data, ensure_ascii=False)
        if "<" in text and ">" in text:
            text = html_main_text(text)
        source_url = data.get("source_url") or data.get("url")
    else:
        text = raw

    title = None
    if suffix in {".html", ".htm"}:
        title = html_title(raw)
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
