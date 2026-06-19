from __future__ import annotations

import argparse
import html
import re
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Callable

from kb.ingest import slugify
from kb.store import connect_db


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


def command_export(
    args: argparse.Namespace,
    project_root_from_args: Callable[[str | None], Path],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> int:
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
