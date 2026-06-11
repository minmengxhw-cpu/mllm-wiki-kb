from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kb.cli import build_article_label, classify_article, corpus_audit_markdown, corpus_review_rows, dict_to_row  # noqa: E402


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

    def test_corpus_review_rows_include_priority_buckets(self) -> None:
        labels = [
            {
                "article_id": 1,
                "title": "会议报道",
                "account": "上海民盟",
                "published_at": "2025-01-01",
                "article_type": "meeting_report",
                "article_type_name": "会议报道",
                "classification_confidence": 80,
                "matched_keywords": ["会议"],
                "topic_tags": [],
                "raw_path": "/tmp/1.md",
            },
            {
                "article_id": 2,
                "title": "待判文章",
                "account": "群言杂志",
                "published_at": "2025-01-02",
                "article_type": "other",
                "article_type_name": "其他/待判",
                "classification_confidence": 0,
                "matched_keywords": [],
                "topic_tags": [],
                "raw_path": "/tmp/2.md",
            },
        ]
        rows = corpus_review_rows(labels, per_type=1, low_confidence_limit=1, other_limit=1)
        buckets = {row["review_bucket"] for row in rows}
        self.assertIn("按类型抽检:会议报道", buckets)
        self.assertIn("低置信抽检", buckets)
        self.assertIn("其他/待判抽检", buckets)


if __name__ == "__main__":
    unittest.main()
