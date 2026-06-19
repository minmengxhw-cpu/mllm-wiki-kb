from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Callable

from kb.store import connect_db, now_iso

_project_root_from_args: Callable[[str | None], Path] | None = None
_append_wiki_log: Callable[[Path, str], None] | None = None
_log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None] | None = None


def configure(
    project_root_from_args: Callable[[str | None], Path],
    append_wiki_log: Callable[[Path, str], None],
    log_operation: Callable[[Path, str, str, str, dict[str, object] | None], None],
) -> None:
    global _project_root_from_args, _append_wiki_log, _log_operation
    _project_root_from_args = project_root_from_args
    _append_wiki_log = append_wiki_log
    _log_operation = log_operation


def project_root_from_args(value: str | None) -> Path:
    if _project_root_from_args is None:
        raise RuntimeError("corpus command callbacks are not configured")
    return _project_root_from_args(value)


def append_wiki_log(root: Path, message: str) -> None:
    if _append_wiki_log is None:
        raise RuntimeError("corpus command callbacks are not configured")
    _append_wiki_log(root, message)


def log_operation(root: Path, operation: str, status: str, message: str, details: dict | None = None) -> None:
    if _log_operation is None:
        raise RuntimeError("corpus command callbacks are not configured")
    _log_operation(root, operation, status, message, details)


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


