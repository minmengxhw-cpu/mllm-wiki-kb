from __future__ import annotations

import argparse
import difflib
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

from kb.commands import sources as sources_commands
from kb.indexing import authority_rank, search_rows
from kb.ingest import slugify
from kb.staff_check import (
    issue_table,
    load_formulations,
    match_blacklist,
    match_staff_items,
    staff_check_issues,
    staff_severity_rank,
    severity_label,
)
from kb.store import connect_db

_project_root_from_args: Callable[[str | None], Path] | None = None
_append_wiki_log: Callable[[Path, str], None] | None = None
_log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None] | None = None
_init_db: Callable[[Path], None] | None = None
_markdown_table: Callable[[list[list[str]]], str] | None = None
_report_dir: Callable[[Path], Path] | None = None
_write_jsonl: Callable[[Path, list[dict]], None] | None = None
_classify_article: Callable[[str, str | None, str], tuple[str, int, list[str]]] | None = None
_corpus_dir: Callable[[Path], Path] | None = None
_year_at_least: Callable[[str | None, str], bool] | None = None
_writing_sample_score: Callable[[dict], tuple[int, list[str]]] | None = None
_load_article_labels: Callable[[Path], list[dict]] | None = None
_write_wiki_page: Callable[[Path, str, str, str, list[sqlite3.Row]], Path] | None = None
_timeline_candidates: Callable[[list[sqlite3.Row], int], str] | None = None
_article_type_names: dict[str, str] = {}
_writing_style_guides: dict[str, dict[str, str]] = {}


def configure(
    project_root_from_args: Callable[[str | None], Path],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
    init_db: Callable[[Path], None],
    markdown_table: Callable[[list[list[str]]], str],
    report_dir: Callable[[Path], Path],
    write_jsonl: Callable[[Path, list[dict]], None],
    classify_article: Callable[[str, str | None, str], tuple[str, int, list[str]]],
    corpus_dir: Callable[[Path], Path],
    year_at_least: Callable[[str | None, str], bool],
    writing_sample_score: Callable[[dict], tuple[int, list[str]]],
    load_article_labels: Callable[[Path], list[dict]],
    write_wiki_page: Callable[[Path, str, str, str, list[sqlite3.Row]], Path],
    timeline_candidates: Callable[[list[sqlite3.Row], int], str],
    article_type_names: dict[str, str],
    writing_style_guides: dict[str, dict[str, str]],
) -> None:
    global _project_root_from_args, _append_wiki_log, _log_operation, _init_db, _markdown_table
    global _report_dir, _write_jsonl, _classify_article, _corpus_dir, _year_at_least
    global _writing_sample_score, _load_article_labels, _write_wiki_page, _timeline_candidates
    global _article_type_names, _writing_style_guides
    _project_root_from_args = project_root_from_args
    _append_wiki_log = append_wiki_log
    _log_operation = log_operation
    _init_db = init_db
    _markdown_table = markdown_table
    _report_dir = report_dir
    _write_jsonl = write_jsonl
    _classify_article = classify_article
    _corpus_dir = corpus_dir
    _year_at_least = year_at_least
    _writing_sample_score = writing_sample_score
    _load_article_labels = load_article_labels
    _write_wiki_page = write_wiki_page
    _timeline_candidates = timeline_candidates
    _article_type_names = article_type_names
    _writing_style_guides = writing_style_guides


def project_root_from_args(value: str | None) -> Path:
    if _project_root_from_args is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _project_root_from_args(value)


def append_wiki_log(root: Path, message: str) -> None:
    if _append_wiki_log is None:
        raise RuntimeError("staff command callbacks are not configured")
    _append_wiki_log(root, message)


def log_operation(root: Path, operation: str, status: str, message: str, details: dict | None = None) -> None:
    if _log_operation is None:
        raise RuntimeError("staff command callbacks are not configured")
    _log_operation(root, operation, status, message, details)


def init_db(root: Path) -> None:
    if _init_db is None:
        raise RuntimeError("staff command callbacks are not configured")
    _init_db(root)


def markdown_table(rows: list[list[str]]) -> str:
    if _markdown_table is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _markdown_table(rows)


def report_dir(root: Path) -> Path:
    if _report_dir is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _report_dir(root)


def write_jsonl(path: Path, items: list[dict]) -> None:
    if _write_jsonl is None:
        raise RuntimeError("staff command callbacks are not configured")
    _write_jsonl(path, items)


def classify_article(title: str, account: str | None, text: str) -> tuple[str, int, list[str]]:
    if _classify_article is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _classify_article(title, account, text)


def corpus_dir(root: Path) -> Path:
    if _corpus_dir is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _corpus_dir(root)


def year_at_least(year: str | None, minimum: str) -> bool:
    if _year_at_least is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _year_at_least(year, minimum)


def writing_sample_score(label: dict) -> tuple[int, list[str]]:
    if _writing_sample_score is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _writing_sample_score(label)


def load_article_labels(root: Path) -> list[dict]:
    if _load_article_labels is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _load_article_labels(root)


def write_wiki_page(root: Path, title: str, page_type: str, body: str, sources: list[sqlite3.Row]) -> Path:
    if _write_wiki_page is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _write_wiki_page(root, title, page_type, body, sources)


def timeline_candidates(rows: list[sqlite3.Row], limit: int = 10) -> str:
    if _timeline_candidates is None:
        raise RuntimeError("staff command callbacks are not configured")
    return _timeline_candidates(rows, limit)


class _ArticleTypeNames(dict):
    def get(self, key, default=None):
        return _article_type_names.get(key, default)

    def __getitem__(self, key):
        return _article_type_names[key]


class _WritingStyleGuides(dict):
    def get(self, key, default=None):
        return _writing_style_guides.get(key, default)


ARTICLE_TYPE_NAMES = _ArticleTypeNames()
WRITING_STYLE_GUIDES = _WritingStyleGuides()


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
    return sources_commands.command_pro_sources(
        args,
        project_root_from_args,
        report_dir,
        append_wiki_log,
        log_operation,
        write_jsonl,
    )


def command_sources(args: argparse.Namespace) -> int:
    return sources_commands.command_sources(
        args,
        project_root_from_args,
        report_dir,
        append_wiki_log,
        log_operation,
    )


def command_source_urls(args: argparse.Namespace) -> int:
    return sources_commands.command_source_urls(
        args,
        project_root_from_args,
        report_dir,
        append_wiki_log,
        log_operation,
    )


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


