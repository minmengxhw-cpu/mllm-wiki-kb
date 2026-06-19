from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path


PRO_SOURCE_QUERY_TOPICS = [
    "中国民主同盟",
    "民盟",
    "多党合作",
    "统一战线",
    "人民政协",
    "参政议政",
    "民主党派",
    "盟史",
    "上海民盟",
]


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * len(rows[0])) + " |"]
    lines.extend("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows[1:])
    return "\n".join(lines)


def pro_sources_dir(root: Path) -> Path:
    return root / "index" / "pro_sources"


def load_pro_sources(root: Path) -> list[dict]:
    return load_jsonl(pro_sources_dir(root) / "source_map.jsonl")


def source_record(item: dict, now: str) -> dict:
    return {
        "source_id": str(item.get("source_id") or item.get("id") or item.get("name") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "url": str(item.get("url") or "").strip(),
        "authority_level": str(item.get("authority_level") or "L4").strip(),
        "source_tier": str(item.get("source_tier") or "参考与样本层").strip(),
        "is_citable": 1 if item.get("is_citable") else 0,
        "collection_method": str(item.get("collection_method") or "").strip(),
        "update_frequency": str(item.get("update_frequency") or "").strip(),
        "copyright_boundary": str(item.get("public_use_boundary") or item.get("copyright_boundary") or "").strip(),
        "note": str(item.get("note") or item.get("ingest_decision") or "").strip(),
        "created_at": now,
        "updated_at": now,
    }


def sync_sources_table(conn: sqlite3.Connection, sources: list[dict], now: str) -> int:
    count = 0
    with conn:
        for item in sources:
            record = source_record(item, now)
            if not record["source_id"] or not record["name"]:
                continue
            conn.execute(
                """
                INSERT INTO sources(
                    source_id, name, url, authority_level, source_tier, is_citable,
                    collection_method, update_frequency, copyright_boundary, note, created_at, updated_at
                )
                VALUES (
                    :source_id, :name, :url, :authority_level, :source_tier, :is_citable,
                    :collection_method, :update_frequency, :copyright_boundary, :note, :created_at, :updated_at
                )
                ON CONFLICT(source_id) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    authority_level = excluded.authority_level,
                    source_tier = excluded.source_tier,
                    is_citable = excluded.is_citable,
                    collection_method = excluded.collection_method,
                    update_frequency = excluded.update_frequency,
                    copyright_boundary = excluded.copyright_boundary,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                record,
            )
            count += 1
    return count


def pro_source_level_names(root: Path) -> dict[str, str]:
    path = pro_sources_dir(root) / "source_types.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(item.get("code")): str(item.get("name")) for item in data.get("source_levels", [])}


def pro_source_action(item: dict) -> str:
    decision = str(item.get("ingest_decision") or "")
    if decision == "优先全文入库":
        return "建立栏目目录；抽取公开文章元数据；确认版权边界后进入全文入库队列"
    if decision == "先建目录后抽取":
        return "先建目录和重点篇目清单；只抽取与民盟、多党合作、政协、统战直接相关条目"
    if decision == "只建索引不全文":
        return "只登记题名、作者、出处、摘要、关键词；不保存受版权限制全文"
    if decision == "仅人工查阅":
        return "保留为人工参考入口；不进入公开语料和自动引用链"
    return "暂缓；先核准来源、链接和使用边界"


def pro_source_queries(item: dict) -> list[str]:
    source_name = str(item.get("name") or "")
    domains = [str(value) for value in item.get("topic_domains", []) if value]
    seeds = []
    for topic in domains[:3]:
        seeds.append(f"{source_name} {topic}")
    for topic in PRO_SOURCE_QUERY_TOPICS[:4]:
        seeds.append(f"{source_name} {topic}")
    deduped = []
    for seed in seeds:
        if seed not in deduped:
            deduped.append(seed)
    return deduped[:6]


def pro_source_intake_tasks(sources: list[dict], priority: str = "P0") -> list[dict]:
    tasks = []
    for item in sources:
        if priority and item.get("priority") != priority:
            continue
        decision = str(item.get("ingest_decision") or "")
        if decision in {"仅人工查阅", "暂缓"}:
            continue
        task_id = f"TASK-{len(tasks) + 1:03d}"
        tasks.append(
            {
                "task_id": task_id,
                "source_id": item.get("source_id"),
                "source_name": item.get("name"),
                "source_level": item.get("source_level"),
                "priority": item.get("priority"),
                "ingest_decision": decision,
                "action": pro_source_action(item),
                "queries": pro_source_queries(item),
                "url": item.get("url") or "",
                "evidence_roles": item.get("evidence_roles") or [],
                "public_use_boundary": item.get("public_use_boundary") or "",
                "status": "待建目录",
            }
        )
    return tasks


def pro_source_query_seeds(tasks: list[dict]) -> list[dict]:
    rows = []
    for task in tasks:
        for idx, query in enumerate(task.get("queries") or [], 1):
            rows.append(
                {
                    "task_id": task.get("task_id"),
                    "source_id": task.get("source_id"),
                    "source_name": task.get("source_name"),
                    "query_id": f"{task.get('task_id')}-Q{idx:02d}",
                    "query": query,
                    "status": "待检索",
                }
            )
    return rows


def pro_sources_report_markdown(root: Path, created_at: str, priority: str = "P0") -> str:
    sources = load_pro_sources(root)
    levels = pro_source_level_names(root)
    tasks = pro_source_intake_tasks(sources, priority)
    query_seeds = pro_source_query_seeds(tasks)
    by_level = Counter(str(item.get("source_level") or "unknown") for item in sources)
    by_decision = Counter(str(item.get("ingest_decision") or "未判定") for item in sources)

    level_rows = [["层级", "名称", "来源数"]]
    for code, count in by_level.most_common():
        level_rows.append([code, levels.get(code, ""), str(count)])

    decision_rows = [["入库判断", "来源数"]]
    for decision, count in by_decision.most_common():
        decision_rows.append([decision, str(count)])

    task_rows = [["任务", "来源", "层级", "入库判断", "动作", "状态"]]
    for task in tasks:
        task_rows.append(
            [
                str(task.get("task_id") or ""),
                str(task.get("source_name") or ""),
                str(task.get("source_level") or ""),
                str(task.get("ingest_decision") or ""),
                str(task.get("action") or ""),
                str(task.get("status") or ""),
            ]
        )

    query_rows = [["查询", "来源", "检索词"]]
    for seed in query_seeds[:30]:
        query_rows.append([
            str(seed.get("query_id") or ""),
            str(seed.get("source_name") or ""),
            str(seed.get("query") or ""),
        ])

    return f"""# 专业语料库首批来源入库工作台

生成时间：{created_at}

## 总体判断

- 当前专业来源候选：{len(sources)} 个。
- 本轮优先级：{priority}。
- 本轮可执行入库任务：{len(tasks)} 个。
- 查询种子：{len(query_seeds)} 条。
- 本页只建立目录任务和查询入口，不把未核验材料直接写成史实结论。

## 来源层级

{markdown_table(level_rows)}

## 入库判断

{markdown_table(decision_rows)}

## 首批任务

{markdown_table(task_rows)}

## 查询种子节选

{markdown_table(query_rows)}

## 执行顺序

1. 先做民盟中央、中央统战部、全国政协、人民政协网、团结网的栏目目录。
2. 每个来源先保存题名、日期、栏目、URL、摘要和主题词。
3. 只有官方公开网页和权威媒体公开报道进入全文候选；出版物、论文和档案目录只建索引。
4. 每条材料入库前标明用途：事实依据、口径依据、理论依据、历史解释、写作样本或待核线索。
5. 对涉及建盟日期、组织沿革、人物职务、政治表述的材料，进入 `/核` 和人工终审链。

## 产物文件

- `index/pro_sources/source_map.jsonl`
- `index/pro_sources/source_types.json`
- `index/pro_sources/intake_tasks.jsonl`
- `index/pro_sources/query_seeds.jsonl`

## 使用边界

- 微信公众号仍是写作风格和公开表述层，不承担最终史实定论。
- 专业来源优先补强事实、口径、制度和研究解释。
- 内部材料不进入公开语料；学术论文不自动等同组织口径；档案目录不等同已经证实的结论。
"""


def sources_dashboard_markdown(root: Path, created_at: str) -> str:
    sources = load_pro_sources(root)
    by_authority = Counter(str(item.get("authority_level") or "未分级") for item in sources)
    by_tier = Counter(str(item.get("source_tier") or "未分层") for item in sources)
    by_citable = Counter("可引用" if item.get("is_citable") else "不可直接引用" for item in sources)
    by_decision = Counter(str(item.get("ingest_decision") or "未判定") for item in sources)
    citable_count = by_citable.get("可引用", 0)
    authority_rows = [["权威级别", "来源数"]]
    for key in ["L1", "L2", "L3", "L4", "未分级"]:
        if by_authority.get(key):
            authority_rows.append([key, str(by_authority[key])])
    tier_rows = [["来源层", "来源数"]]
    for key, count in by_tier.most_common():
        tier_rows.append([key, str(count)])
    decision_rows = [["入库判断", "来源数"]]
    for key, count in by_decision.most_common():
        decision_rows.append([key, str(count)])
    source_rows = [["来源", "权威级别", "是否可引用", "入库判断", "采集方式", "URL"]]
    for item in sources[:80]:
        url = str(item.get("url") or "")
        display_url = f"[打开]({url})" if url.startswith(("http://", "https://")) else "待登记"
        source_rows.append(
            [
                str(item.get("name") or ""),
                str(item.get("authority_level") or ""),
                "是" if item.get("is_citable") else "否",
                str(item.get("ingest_decision") or ""),
                str(item.get("collection_method") or ""),
                display_url,
            ]
        )
    return f"""# 权威公开资料来源体检

生成时间：{created_at}

## 总体判断

- 已登记专业来源：{len(sources)} 个。
- 可直接引用来源：{citable_count} 个。
- L1-L3 作为事实层；L4 仅作写作样本、线索或人工参考。
- 当前体检只确认来源分级与引用边界，不代表具体史实已经校订完成。

## 按权威级别

{markdown_table(authority_rows)}

## 按来源层

{markdown_table(tier_rows)}

## 按入库判断

{markdown_table(decision_rows)}

## 来源清单

{markdown_table(source_rows)}

## 使用规则

1. `/史`、`/核`、`kb ask` 的史实结论优先使用 L1-L3。
2. L4 微信公众号语料只作写作样本和线索，不作最终定论。
3. 冲突时按 L1 > L2 > L3 > L4 排序，并保留争议记录。
4. 没有 L1-L3 支撑的结论必须标注 `[待核]`。
"""
