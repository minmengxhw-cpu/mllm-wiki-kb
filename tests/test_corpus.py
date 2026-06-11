from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kb.cli import build_article_label, classify_article, corpus_audit_markdown, dict_to_row  # noqa: E402


class CorpusCommandTests(unittest.TestCase):
    def test_classify_meeting_report_from_title(self) -> None:
        article_type, confidence, matched = classify_article(
            "民盟上海市委召开十六届三次全委会议",
            "上海民盟",
            "会议在民主党派大厦举行，主委作工作报告并讲话。",
        )
        self.assertEqual(article_type, "meeting_report")
        self.assertGreaterEqual(confidence, 60)
        self.assertIn("会议", matched)

    def test_build_article_label_marks_recent_shanghai_sample(self) -> None:
        row = dict_to_row(
            {
                "id": 1,
                "title": "民盟市委举行主题教育学习交流会",
                "account": "上海民盟",
                "author": "",
                "published_at": "2025-05-23",
                "source_url": "https://example.test",
                "raw_path": "/tmp/raw.md",
                "content_hash": "abc",
                "file_type": "md",
                "status": "imported",
                "chunk_count": 1,
                "token_estimate": 100,
                "sample_text": "主题教育 参政为公 实干为民 会议 举行",
            }
        )
        label = build_article_label(row)
        self.assertEqual(label["account"], "上海民盟")
        self.assertEqual(label["year"], "2025")
        self.assertTrue(label["is_writing_sample"])
        self.assertIn("主题教育", label["topic_tags"])

    def test_corpus_audit_markdown_contains_core_sections(self) -> None:
        labels = [
            {
                "account": "上海民盟",
                "year": "2025",
                "article_type_name": "会议报道",
                "is_history": False,
                "is_writing_sample": True,
                "needs_metadata_review": False,
                "source_url": "https://example.test",
                "raw_path": "/tmp/raw.md",
            }
        ]
        body = corpus_audit_markdown(labels, "2026-06-11T00:00:00")
        self.assertIn("微信公众号语料库体检报告", body)
        self.assertIn("按账号分布", body)
        self.assertIn("按文章类型分布", body)


if __name__ == "__main__":
    unittest.main()
