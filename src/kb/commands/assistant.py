from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

from kb.indexing import search_rows
from kb.staff_check import load_blacklist, load_formulations, staff_severity_rank
from kb.store import connect_db, db_path, now_iso

_project_root_from_args: Callable[[str | None], Path] | None = None
_report_dir: Callable[[Path], Path] | None = None
_append_wiki_log: Callable[[Path, str], None] | None = None
_log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None] | None = None
_markdown_table: Callable[[list[list[str]]], str] | None = None
_external_sources_report_markdown: Callable[[Path, str], str] | None = None
_cited_excerpts: Callable[[list[sqlite3.Row], int], str] | None = None
_write_wiki_page: Callable[[Path, str, str, str, list[sqlite3.Row]], Path] | None = None
_load_article_labels: Callable[[Path], list[dict]] | None = None
_load_external_inventory: Callable[[Path], list[dict]] | None = None
_obsidian_sync_status: Callable[[Path, Path], dict[str, object]] | None = None
_row_source_md: Callable[[sqlite3.Row, int], str] | None = None
_clean_snippet: Callable[[str, int], str] | None = None
_command_obsidian_sync: Callable[[argparse.Namespace], int] | None = None


def configure(
    project_root_from_args: Callable[[str | None], Path],
    report_dir: Callable[[Path], Path],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
    markdown_table: Callable[[list[list[str]]], str],
    external_sources_report_markdown: Callable[[Path, str], str],
    cited_excerpts: Callable[[list[sqlite3.Row], int], str],
    write_wiki_page: Callable[[Path, str, str, str, list[sqlite3.Row]], Path],
    load_article_labels: Callable[[Path], list[dict]],
    load_external_inventory: Callable[[Path], list[dict]],
    obsidian_sync_status: Callable[[Path, Path], dict[str, object]],
    row_source_md: Callable[[sqlite3.Row, int], str],
    clean_snippet: Callable[[str, int], str],
    command_obsidian_sync: Callable[[argparse.Namespace], int],
) -> None:
    global _project_root_from_args, _report_dir, _append_wiki_log, _log_operation, _markdown_table
    global _external_sources_report_markdown, _cited_excerpts, _write_wiki_page, _load_article_labels
    global _load_external_inventory, _obsidian_sync_status, _row_source_md, _clean_snippet, _command_obsidian_sync
    _project_root_from_args = project_root_from_args
    _report_dir = report_dir
    _append_wiki_log = append_wiki_log
    _log_operation = log_operation
    _markdown_table = markdown_table
    _external_sources_report_markdown = external_sources_report_markdown
    _cited_excerpts = cited_excerpts
    _write_wiki_page = write_wiki_page
    _load_article_labels = load_article_labels
    _load_external_inventory = load_external_inventory
    _obsidian_sync_status = obsidian_sync_status
    _row_source_md = row_source_md
    _clean_snippet = clean_snippet
    _command_obsidian_sync = command_obsidian_sync


def project_root_from_args(value: str | None) -> Path:
    if _project_root_from_args is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _project_root_from_args(value)


def report_dir(root: Path) -> Path:
    if _report_dir is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _report_dir(root)


def append_wiki_log(root: Path, message: str) -> None:
    if _append_wiki_log is None:
        raise RuntimeError("assistant command callbacks are not configured")
    _append_wiki_log(root, message)


def log_operation(root: Path, operation: str, status: str, message: str, details: dict | None = None) -> None:
    if _log_operation is None:
        raise RuntimeError("assistant command callbacks are not configured")
    _log_operation(root, operation, status, message, details)


def markdown_table(rows: list[list[str]]) -> str:
    if _markdown_table is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _markdown_table(rows)


def external_sources_report_markdown(root: Path, created_at: str) -> str:
    if _external_sources_report_markdown is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _external_sources_report_markdown(root, created_at)


def cited_excerpts(rows: list[sqlite3.Row], limit: int = 10) -> str:
    if _cited_excerpts is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _cited_excerpts(rows, limit)


def write_wiki_page(root: Path, title: str, page_type: str, body: str, sources: list[sqlite3.Row]) -> Path:
    if _write_wiki_page is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _write_wiki_page(root, title, page_type, body, sources)


def load_article_labels(root: Path) -> list[dict]:
    if _load_article_labels is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _load_article_labels(root)


def load_external_inventory(root: Path) -> list[dict]:
    if _load_external_inventory is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _load_external_inventory(root)


def obsidian_sync_status(root: Path, vault: Path) -> dict[str, object]:
    if _obsidian_sync_status is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _obsidian_sync_status(root, vault)


def row_source_md(row: sqlite3.Row, idx: int) -> str:
    if _row_source_md is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _row_source_md(row, idx)


def clean_snippet(value: str, limit: int = 240) -> str:
    if _clean_snippet is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _clean_snippet(value, limit)


def command_obsidian_sync(args: argparse.Namespace) -> int:
    if _command_obsidian_sync is None:
        raise RuntimeError("assistant command callbacks are not configured")
    return _command_obsidian_sync(args)


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


