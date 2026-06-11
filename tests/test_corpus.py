from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kb.cli import (  # noqa: E402
    build_article_label,
    classify_article,
    corpus_audit_markdown,
    corpus_quality_diagnostic_markdown,
    corpus_priority_review_markdown,
    corpus_priority_review_rows,
    corpus_review_rows,
    dict_to_row,
    history_research_entry_markdown,
    writing_style_templates_markdown,
)


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

    def test_theme_education_does_not_match_scientific_thought(self) -> None:
        article_type, _, matched = classify_article("田刚院士：科学思想的力量", "上海民盟", "")
        self.assertNotEqual(article_type, "theme_education")
        self.assertNotIn("学思想", matched)

    def test_achievement_and_notice_title_priority(self) -> None:
        article_type, _, matched = classify_article(
            "祝贺！盟员陈子善获颁中国现代文学学术贡献荣誉奖",
            "上海民盟",
            "活动举办，现场举行颁奖。",
        )
        self.assertEqual(article_type, "member_achievement")
        self.assertIn("祝贺", matched)

        article_type, _, matched = classify_article("预告 | 周末盟史讲座报名开启", "上海民盟", "")
        self.assertEqual(article_type, "notice_info")
        self.assertIn("预告", matched)

        article_type, _, matched = classify_article("民盟上海市委祝广大盟员新年快乐！", "上海民盟", "")
        self.assertEqual(article_type, "notice_info")
        self.assertIn("新年快乐", matched)

    def test_theme_and_history_title_priority(self) -> None:
        article_type, _, matched = classify_article(
            "丰富形式，走深走实！上海民盟各级组织开展主题教育",
            "上海民盟",
            "活动举办，现场交流。",
        )
        self.assertEqual(article_type, "theme_education")
        self.assertIn("主题教育", matched)

        article_type, _, matched = classify_article(
            "民盟先贤廉洁自律事迹丨张澜：布衣风骨 清廉永存",
            "中国民主同盟",
            "活动开展，现场参观。",
        )
        self.assertEqual(article_type, "history_commemoration")
        self.assertIn("先贤", matched)

    def test_style_and_history_markdown_have_core_sections(self) -> None:
        labels = [
            {
                "article_id": 1,
                "title": "民盟上海市委开展专题调研",
                "account": "上海民盟",
                "published_at": "2025-01-01",
                "year": "2025",
                "article_type": "activity_report",
                "article_type_name": "活动报道",
                "topic_tags": ["上海民盟"],
                "people": [],
                "raw_path": "/tmp/1.md",
                "is_writing_sample": True,
                "is_history": False,
            },
            {
                "article_id": 2,
                "title": "沈钧儒与民盟历史",
                "account": "中国民主同盟",
                "published_at": "2024-01-01",
                "year": "2024",
                "article_type": "history_research",
                "article_type_name": "盟史研究",
                "topic_tags": ["民盟史"],
                "people": ["沈钧儒"],
                "raw_path": "/tmp/2.md",
                "is_writing_sample": False,
                "is_history": True,
            },
        ]
        style = writing_style_templates_markdown(labels, "2026-06-11T00:00:00")
        history = history_research_entry_markdown(labels, "2026-06-11T00:00:00")
        self.assertIn("上海民盟微信公众号分体裁写作模板", style)
        self.assertIn("活动报道", style)
        self.assertIn("微信公众号文史盟史研究入口清单", history)
        self.assertIn("沈钧儒", history)

    def test_quality_diagnostic_markdown_contains_boundary_sections(self) -> None:
        labels = [
            {
                "title": "民盟上海市委祝广大盟员新年快乐！",
                "account": "上海民盟",
                "published_at": "2025-01-01",
                "article_type": "other",
                "article_type_name": "其他/待判",
                "matched_keywords": [],
                "raw_path": "/tmp/1.md",
            }
        ]
        body = corpus_quality_diagnostic_markdown(labels, "2026-06-11T00:00:00")
        self.assertIn("微信公众号分类质量诊断报告", body)
        self.assertIn("其他/待判中疑似通知预告", body)

    def test_priority_review_rows_rank_suspicious_items(self) -> None:
        labels = [
            {
                "article_id": 1,
                "title": "预告 | 周末盟史讲座报名开启",
                "account": "上海民盟",
                "published_at": "2025-01-01",
                "year": "2025",
                "article_type": "other",
                "article_type_name": "其他/待判",
                "classification_confidence": 0,
                "matched_keywords": [],
                "topic_tags": [],
                "people": [],
                "raw_path": "/tmp/1.md",
                "is_history": False,
                "is_writing_sample": False,
            },
            {
                "article_id": 2,
                "title": "民盟市委召开会议",
                "account": "上海民盟",
                "published_at": "2025-01-02",
                "year": "2025",
                "article_type": "meeting_report",
                "article_type_name": "会议报道",
                "classification_confidence": 90,
                "matched_keywords": ["会议"],
                "topic_tags": [],
                "people": [],
                "raw_path": "/tmp/2.md",
                "is_history": False,
                "is_writing_sample": True,
            },
        ]
        rows = corpus_priority_review_rows(labels, limit=10)
        self.assertEqual(rows[0]["article_id"], 1)
        self.assertEqual(rows[0]["suggested_type"], "notice_info")
        body = corpus_priority_review_markdown(rows, "2026-06-11T00:00:00")
        self.assertIn("微信公众号分类优先校订清单", body)
        self.assertIn("通知公告/信息发布", body)


if __name__ == "__main__":
    unittest.main()
