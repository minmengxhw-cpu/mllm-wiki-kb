# -*- coding: utf-8 -*-
"""
民盟知识库 · 集群 Web 只读浏览器
读取仓库 wiki/ 结构化知识页 + index/ 元数据，渲染为手机/电脑可访问的网站。
设计：只读、本地零依赖外网、民盟会徽蓝主色。
"""
import os
import re
import json
import html
from datetime import datetime
from functools import lru_cache

import yaml
import markdown
from flask import Flask, render_template, request, abort, url_for

# 仓库根：webapp/ 的上一级
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI_DIR = os.path.join(ROOT, "wiki")
INDEX_DIR = os.path.join(ROOT, "index")

app = Flask(__name__)

# 分类排序与中文说明（首页导航顺序）
CATEGORY_ORDER = [
    "盟史", "人物", "事件", "参政议政", "主题教育",
    "思想宣传", "传统教育基地", "文稿素材", "研究助手",
]
CATEGORY_DESC = {
    "盟史": "民盟与上海民盟组织发展沿革",
    "人物": "盟史核心人物与盟员风采",
    "事件": "重要会议、活动与历史节点",
    "参政议政": "履职素材与政策建议",
    "主题教育": "主题教育与思想建设",
    "思想宣传": "宣传文稿与思想引领",
    "传统教育基地": "传统教育与盟史教育基地",
    "文稿素材": "公众号写作范式与素材",
    "研究助手": "研究档案与系统说明",
}

MD_EXTS = ["tables", "fenced_code", "toc", "sane_lists", "nl2br"]


def _split_frontmatter(text):
    """解析 YAML frontmatter，返回 (meta dict, body markdown)。"""
    if text.startswith("---"):
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
        if m:
            try:
                meta = yaml.safe_load(m.group(1)) or {}
            except Exception:
                meta = {}
            return (meta if isinstance(meta, dict) else {}), m.group(2)
    return {}, text


def _clean_body(body):
    """去掉 KB-GENERATED 标记。"""
    body = body.replace("<!-- KB-GENERATED:START -->", "")
    body = body.replace("<!-- KB-GENERATED:END -->", "")
    return body.strip()


@lru_cache(maxsize=1)
def load_pages():
    """扫描 wiki/ 下所有 .md（排除 index.md/log.md），载入内存。"""
    pages = {}
    for dirpath, _dirs, files in os.walk(WIKI_DIR):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), WIKI_DIR)
            top = rel.split(os.sep)[0]
            if rel in ("index.md", "log.md"):
                continue
            full = os.path.join(dirpath, fn)
            try:
                with open(full, encoding="utf-8") as f:
                    raw = f.read()
            except Exception:
                continue
            meta, body = _split_frontmatter(raw)
            body = _clean_body(body)
            slug = rel[:-3].replace(os.sep, "/")  # 去 .md
            title = (meta.get("title") or os.path.splitext(fn)[0]).strip()
            category = top if top in CATEGORY_DESC else "研究助手"
            pages[slug] = {
                "slug": slug,
                "category": category,
                "subdir": "/".join(rel.split(os.sep)[1:-1]),
                "title": title,
                "meta": meta,
                "body_md": body,
                "text": title + "\n" + body,
                "tags": meta.get("tags") or [],
                "confidence": meta.get("confidence") or "",
                "needs_review": bool(meta.get("needs_review")),
                "source_count": meta.get("source_count") or 0,
                "last_compiled": (meta.get("last_compiled_at") or "")[:10],
            }
    return pages


def render_md(text):
    return markdown.markdown(text, extensions=MD_EXTS, output_format="html5")


@lru_cache(maxsize=1)
def load_jsonl_entities():
    """载入 index/entities/*.jsonl。"""
    out = {}
    edir = os.path.join(INDEX_DIR, "entities")
    for kind in ("persons", "orgs", "events", "places"):
        path = os.path.join(edir, kind + ".jsonl")
        rows = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        out[kind] = rows
    return out


@lru_cache(maxsize=1)
def load_formulations():
    path = os.path.join(INDEX_DIR, "formulations.jsonl")
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def category_counts():
    pages = load_pages()
    counts = {}
    for p in pages.values():
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    return counts


@app.context_processor
def inject_globals():
    pages = load_pages()
    ents = load_jsonl_entities()
    return {
        "CATEGORY_ORDER": CATEGORY_ORDER,
        "CATEGORY_DESC": CATEGORY_DESC,
        "cat_counts": category_counts(),
        "total_pages": len(pages),
        "total_persons": len(ents["persons"]),
        "total_events": len(ents["events"]),
        "total_formulations": len(load_formulations()),
    }


@app.route("/")
def home():
    pages = load_pages()
    recent = sorted(pages.values(), key=lambda p: p["last_compiled"], reverse=True)[:8]
    return render_template("index.html", recent=recent)


@app.route("/c/<category>")
def category(category):
    pages = load_pages()
    items = [p for p in pages.values() if p["category"] == category]
    if not items:
        abort(404)
    # 按子目录分组
    groups = {}
    for p in sorted(items, key=lambda x: (x["subdir"], x["title"])):
        groups.setdefault(p["subdir"], []).append(p)
    return render_template("category.html", category=category, groups=groups, count=len(items))


@app.route("/p/<path:slug>")
def page(slug):
    pages = load_pages()
    p = pages.get(slug)
    if not p:
        abort(404)
    body_html = render_md(p["body_md"])
    return render_template("page.html", p=p, body_html=body_html)


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    results = []
    if q:
        pages = load_pages()
        ql = q.lower()
        for p in pages.values():
            hay = p["text"].lower()
            idx = hay.find(ql)
            if idx >= 0:
                score = (3 if ql in p["title"].lower() else 0) + p["text"].lower().count(ql)
                # 摘要片段
                start = max(0, idx - 30)
                snippet = p["text"][start:start + 160].replace("\n", " ")
                results.append((score, p, snippet))
        results.sort(key=lambda x: -x[0])
        results = [(p, s) for _sc, p, s in results[:50]]
    return render_template("search.html", q=q, results=results)


@app.route("/entities")
def entities():
    ents = load_jsonl_entities()
    return render_template("entities.html", ents=ents)


@app.route("/formulations")
def formulations():
    rows = load_formulations()
    return render_template("formulations.html", rows=rows)


@app.template_filter("hl")
def highlight(text, q):
    """搜索结果高亮。"""
    if not q:
        return html.escape(text)
    esc = html.escape(text)
    return re.sub(re.escape(html.escape(q)), lambda m: f"<mark>{m.group(0)}</mark>", esc, flags=re.I)


@app.route("/healthz")
def healthz():
    return {"status": "ok", "pages": len(load_pages())}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8866"))
    app.run(host="0.0.0.0", port=port, debug=False)
