from __future__ import annotations

import argparse
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

from kb.indexing import query_terms, search_rows
from kb.ingest import sha256_text, slugify
from kb.store import connect_db, now_iso

_project_root_from_args: Callable[[str | None], Path] | None = None
_append_wiki_log: Callable[[Path, str], None] | None = None
_log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None] | None = None
_init_db: Callable[[Path], None] | None = None
_merge_generated_with_fresh_metadata: Callable[[str, str], str] | None = None
_row_source_line: Callable[[sqlite3.Row, int], str] | None = None
_clean_snippet: Callable[[str, int], str] | None = None
_row_source_md: Callable[[sqlite3.Row, int], str] | None = None
_timeline_candidates: Callable[[list[sqlite3.Row], int], str] | None = None
_entity_candidates: Callable[[list[sqlite3.Row], str, int], str] | None = None
_source_title_list: Callable[[list[sqlite3.Row], int], str] | None = None
_markdown_table: Callable[[list[list[str]]], str] | None = None
_people_hits_for_text: Callable[[str], list[str]] | None = None
_unique_source_rows: Callable[[list[sqlite3.Row], int], list[sqlite3.Row]] | None = None
_row_authority_label: Callable[[sqlite3.Row], str] | None = None


def configure(
    project_root_from_args: Callable[[str | None], Path],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
    init_db: Callable[[Path], None],
    merge_generated_with_fresh_metadata: Callable[[str, str], str],
    row_source_line: Callable[[sqlite3.Row, int], str],
    clean_snippet: Callable[[str, int], str],
    row_source_md: Callable[[sqlite3.Row, int], str],
    timeline_candidates: Callable[[list[sqlite3.Row], int], str],
    entity_candidates: Callable[[list[sqlite3.Row], str, int], str],
    source_title_list: Callable[[list[sqlite3.Row], int], str],
    markdown_table: Callable[[list[list[str]]], str],
    people_hits_for_text: Callable[[str], list[str]],
    unique_source_rows: Callable[[list[sqlite3.Row], int], list[sqlite3.Row]],
    row_authority_label: Callable[[sqlite3.Row], str],
) -> None:
    global _project_root_from_args, _append_wiki_log, _log_operation, _init_db
    global _merge_generated_with_fresh_metadata, _row_source_line, _clean_snippet, _row_source_md
    global _timeline_candidates, _entity_candidates, _source_title_list, _markdown_table
    global _people_hits_for_text, _unique_source_rows, _row_authority_label
    _project_root_from_args = project_root_from_args
    _append_wiki_log = append_wiki_log
    _log_operation = log_operation
    _init_db = init_db
    _merge_generated_with_fresh_metadata = merge_generated_with_fresh_metadata
    _row_source_line = row_source_line
    _clean_snippet = clean_snippet
    _row_source_md = row_source_md
    _timeline_candidates = timeline_candidates
    _entity_candidates = entity_candidates
    _source_title_list = source_title_list
    _markdown_table = markdown_table
    _people_hits_for_text = people_hits_for_text
    _unique_source_rows = unique_source_rows
    _row_authority_label = row_authority_label


def project_root_from_args(value: str | None) -> Path:
    if _project_root_from_args is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _project_root_from_args(value)


def append_wiki_log(root: Path, message: str) -> None:
    if _append_wiki_log is None:
        raise RuntimeError("compile command callbacks are not configured")
    _append_wiki_log(root, message)


def log_operation(root: Path, operation: str, status: str, message: str, details: dict | None = None) -> None:
    if _log_operation is None:
        raise RuntimeError("compile command callbacks are not configured")
    _log_operation(root, operation, status, message, details)


def init_db(root: Path) -> None:
    if _init_db is None:
        raise RuntimeError("compile command callbacks are not configured")
    _init_db(root)


def merge_generated_with_fresh_metadata(old_text: str, new_text: str) -> str:
    if _merge_generated_with_fresh_metadata is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _merge_generated_with_fresh_metadata(old_text, new_text)


def row_source_line(row: sqlite3.Row, idx: int) -> str:
    if _row_source_line is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _row_source_line(row, idx)


def clean_snippet(value: str, limit: int = 240) -> str:
    if _clean_snippet is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _clean_snippet(value, limit)


def row_source_md(row: sqlite3.Row, idx: int) -> str:
    if _row_source_md is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _row_source_md(row, idx)


def timeline_candidates(rows: list[sqlite3.Row], limit: int = 10) -> str:
    if _timeline_candidates is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _timeline_candidates(rows, limit)


def entity_candidates(rows: list[sqlite3.Row], query: str, limit: int = 16) -> str:
    if _entity_candidates is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _entity_candidates(rows, query, limit)


def source_title_list(rows: list[sqlite3.Row], limit: int = 12) -> str:
    if _source_title_list is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _source_title_list(rows, limit)


def markdown_table(rows: list[list[str]]) -> str:
    if _markdown_table is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _markdown_table(rows)


def people_hits_for_text(text: str) -> list[str]:
    if _people_hits_for_text is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _people_hits_for_text(text)


def unique_source_rows(rows: list[sqlite3.Row], limit: int = 12) -> list[sqlite3.Row]:
    if _unique_source_rows is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _unique_source_rows(rows, limit)


def row_authority_label(row: sqlite3.Row) -> str:
    if _row_authority_label is None:
        raise RuntimeError("compile command callbacks are not configured")
    return _row_authority_label(row)


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


