from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kb.cli import (  # noqa: E402
    build_parser,
    dict_to_row,
    normalized_similarity,
    staff_check_issues,
    staff_draft_body,
)


class StaffCommandTests(unittest.TestCase):
    def make_root(self) -> Path:
        temp = Path(tempfile.mkdtemp())
        (temp / "index").mkdir(parents=True)
        shutil.copy(ROOT / "index" / "blacklist.csv", temp / "index" / "blacklist.csv")
        shutil.copy(ROOT / "index" / "formulations.jsonl", temp / "index" / "formulations.jsonl")
        return temp

    def test_parser_accepts_staff_history(self) -> None:
        args = build_parser().parse_args(["staff", "history", "沈钧儒"])
        self.assertEqual(args.command, "staff")
        self.assertEqual(args.staff_command, "history")
        self.assertEqual(args.topic, "沈钧儒")

    def test_staff_check_flags_blacklist_and_missing_citation(self) -> None:
        root = self.make_root()
        try:
            text = "沈均儒参与民盟特设支部工作，中国民主同盟成立于1941年3月19日。"
            issues = staff_check_issues(root, text)
            patterns = {item["pattern"] for item in issues}
            self.assertIn("沈均儒", patterns)
            self.assertIn("民盟特设支部", patterns)
            self.assertIn("中国民主同盟成立于1941年3月19日", patterns)
            self.assertTrue(any(item["category"] == "引用" for item in issues))
        finally:
            shutil.rmtree(root)

    def test_staff_draft_body_uses_three_part_structure_and_citation(self) -> None:
        root = self.make_root()
        try:
            row = dict_to_row(
                {
                    "article_id": 1,
                    "chunk_id": 1,
                    "title": "上海民盟举行纪念活动",
                    "account": "上海民盟",
                    "published_at": "2025-05-01",
                    "raw_path": "/tmp/raw.md",
                    "snippet": "上海民盟围绕主题开展纪念活动，强调传承民盟优良传统。",
                    "score": 0,
                }
            )
            body = staff_draft_body(root, "80周年主委讲话", [row])
            self.assertIn("## 结论", body)
            self.assertIn("## 素材", body)
            self.assertIn("## 风险提示", body)
            self.assertIn("[S1]", body)
            self.assertIn("raw 原文", body)
        finally:
            shutil.rmtree(root)

    def test_topic_similarity_prefers_containment(self) -> None:
        self.assertGreaterEqual(normalized_similarity("费孝通与江村", "午间盟史课堂：费孝通与江村"), 0.72)


if __name__ == "__main__":
    unittest.main()
