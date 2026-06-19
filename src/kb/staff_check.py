from __future__ import annotations

import csv
import json
import re
from pathlib import Path


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


def match_formulation_risks(root: Path, text: str) -> list[dict]:
    matches = []
    for item in load_formulations(root):
        canonical = str(item.get("canonical") or "").strip()
        status = str(item.get("status") or "").strip()
        for variant in value_variants({"variants": item.get("variants") or []}):
            if variant and variant in text and variant != canonical:
                matches.append(
                    {
                        "severity": "high" if status in {"禁用", "待核"} else "medium",
                        "category": "口径",
                        "pattern": variant,
                        "suggestion": canonical or "请回到现行规范表述核定",
                        "note": item.get("note") or "",
                    }
                )
    return matches


def staff_severity_rank(value: str | None) -> int:
    return {"blocker": 0, "high": 1, "medium": 2, "low": 3}.get((value or "").strip(), 4)


def severity_label(value: str | None) -> str:
    return {
        "blocker": "必须修改",
        "high": "高风险",
        "medium": "中风险",
        "low": "提示",
    }.get((value or "").strip(), value or "未标注")


def draft_has_citation(text: str) -> bool:
    return bool(re.search(r"\[[SM]\d+\]|raw:|raw 原文|来源|出处", text))


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
    issues.extend(match_formulation_risks(root, text))
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
