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
    external_sources_report_markdown,
    normalized_similarity,
    staff_check_issues,
    staff_draft_body,
    staff_history_body,
    staff_material_draft_body,
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

    def test_parser_accepts_staff_draft_material(self) -> None:
        args = build_parser().parse_args(["staff", "draft", "会议报道", "--material", "2026年6月1日召开会议"])
        self.assertEqual(args.staff_command, "draft")
        self.assertEqual(args.topic, "会议报道")
        self.assertEqual(args.material, ["2026年6月1日召开会议"])

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
            self.assertIn("精选写作样本", body)
            self.assertIn("体裁写作骨架", body)
            self.assertIn("标题写法", body)
            self.assertIn("导语写法", body)
        finally:
            shutil.rmtree(root)

    def test_staff_draft_body_uses_curated_samples_when_available(self) -> None:
        root = self.make_root()
        try:
            (root / "index" / "corpus").mkdir(parents=True)
            (root / "index" / "corpus" / "article_labels.jsonl").write_text(
                "\n".join(
                    [
                        (
                            '{"article_id":1,"title":"民盟上海市委召开十六届十四次常委（扩大）会议",'
                            '"account":"上海民盟","published_at":"2025-04-11","year":"2025",'
                            '"article_type":"meeting_report","article_type_name":"会议报道",'
                            '"classification_confidence":95,"matched_keywords":["会议"],"topic_tags":["上海民盟"],'
                            '"people":[],"raw_path":"/tmp/meeting.md","token_estimate":1500,'
                            '"is_history":false,"is_writing_sample":true,"can_be_formulation_source":true}'
                        )
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            row = dict_to_row(
                {
                    "article_id": 2,
                    "chunk_id": 2,
                    "title": "民盟上海市委召开会议",
                    "account": "上海民盟",
                    "published_at": "2025-05-01",
                    "raw_path": "/tmp/raw.md",
                    "snippet": "会议围绕重点工作进行部署。",
                    "score": 0,
                }
            )
            body = staff_draft_body(root, "民盟市委会议报道", [row])
            self.assertIn("初步判断适用体裁：会议报道", body)
            self.assertIn("民盟上海市委召开十六届十四次常委", body)
            self.assertIn("/tmp/meeting.md", body)
            self.assertIn("标题点明会议名称或核心任务", body)
        finally:
            shutil.rmtree(root)

    def test_staff_draft_body_links_drive_external_reference(self) -> None:
        root = self.make_root()
        try:
            external_dir = root / "index" / "external_sources"
            external_dir.mkdir(parents=True)
            (external_dir / "google_drive_inventory.jsonl").write_text(
                (
                    '{"source":"研究室知识库","layer":"wiki","path":"wiki/体例范式/先贤诞辰纪念致辞.md",'
                    '"title":"先贤诞辰纪念致辞.md","url":"https://drive.example/file",'
                    '"import_decision":"外部参考，适合文史纪念讲话体例参考"}\n'
                ),
                encoding="utf-8",
            )
            row = dict_to_row(
                {
                    "article_id": 1,
                    "chunk_id": 1,
                    "title": "民盟先贤纪念活动",
                    "account": "上海民盟",
                    "published_at": "2025-05-01",
                    "raw_path": "/tmp/raw.md",
                    "snippet": "活动纪念民盟先贤。",
                    "score": 0,
                }
            )
            body = staff_draft_body(root, "先贤诞辰纪念致辞", [row])
            self.assertIn("Drive 外部参考层", body)
            self.assertIn("先贤诞辰纪念致辞.md", body)
            self.assertIn("外部参考，适合文史纪念讲话体例参考", body)
        finally:
            shutil.rmtree(root)

    def test_staff_material_draft_body_generates_article_draft(self) -> None:
        root = self.make_root()
        try:
            row = dict_to_row(
                {
                    "article_id": 1,
                    "chunk_id": 1,
                    "title": "民盟上海市委召开会议",
                    "account": "上海民盟",
                    "published_at": "2025-05-01",
                    "raw_path": "/tmp/raw.md",
                    "snippet": "会议围绕重点工作进行部署。",
                    "score": 0,
                }
            )
            material = "2026年6月1日，民盟市委机关在民主党派大厦召开专题交流会。会议围绕主题教育开展交流。"
            body = staff_material_draft_body(root, "主题教育会议报道", material, [row])
            self.assertIn("## 初稿", body)
            self.assertIn("标题备选", body)
            self.assertIn("正文初稿", body)
            self.assertIn("[M1]", body)
            self.assertIn("用户材料核验", body)
            self.assertIn("初稿核验", body)
            self.assertIn("民盟上海市委召开会议", body)
        finally:
            shutil.rmtree(root)

    def test_staff_history_body_links_core_dossier(self) -> None:
        root = self.make_root()
        try:
            dossier_dir = root / "wiki" / "研究助手" / "核心人物研究档案"
            dossier_dir.mkdir(parents=True)
            (dossier_dir / "沈钧儒.md").write_text("# 沈钧儒研究档案\n\n- 结论状态：待人工核验\n", encoding="utf-8")
            row = dict_to_row(
                {
                    "article_id": 1,
                    "chunk_id": 1,
                    "title": "沈钧儒与人民政协制度的创建",
                    "account": "中国民主同盟",
                    "published_at": "2025-01-02",
                    "raw_path": "/tmp/shen.md",
                    "snippet": "沈钧儒参与新政协筹备。",
                    "score": 0,
                }
            )
            body = staff_history_body(root, "沈钧儒", [row])
            self.assertIn("核心研究档案", body)
            self.assertIn("沈钧儒", body)
            self.assertIn("研究路线", body)
            self.assertIn("待人工核验", body)
        finally:
            shutil.rmtree(root)

    def test_staff_history_body_links_drive_external_reference(self) -> None:
        root = self.make_root()
        try:
            external_dir = root / "index" / "external_sources"
            external_dir.mkdir(parents=True)
            (external_dir / "google_drive_inventory.jsonl").write_text(
                (
                    '{"source":"研究室知识库","layer":"wiki","path":"wiki/先贤/谷超豪.md",'
                    '"title":"谷超豪.md","url":"https://drive.example/gu",'
                    '"import_decision":"外部参考，适合人物研究补充"}\n'
                ),
                encoding="utf-8",
            )
            row = dict_to_row(
                {
                    "article_id": 1,
                    "chunk_id": 1,
                    "title": "谷超豪与民盟",
                    "account": "上海民盟",
                    "published_at": "2025-01-02",
                    "raw_path": "/tmp/gu.md",
                    "snippet": "谷超豪相关材料。",
                    "score": 0,
                }
            )
            body = staff_history_body(root, "谷超豪", [row])
            self.assertIn("Drive 外部参考层", body)
            self.assertIn("谷超豪.md", body)
            self.assertIn("外部参考，适合人物研究补充", body)
        finally:
            shutil.rmtree(root)

    def test_external_sources_report_summarizes_drive_layer(self) -> None:
        root = self.make_root()
        try:
            external_dir = root / "index" / "external_sources"
            external_dir.mkdir(parents=True)
            (external_dir / "google_drive_folders.jsonl").write_text(
                (
                    '{"name":"研究室知识库","role":"参政议政外部参考层","status":"已登记"}\n'
                ),
                encoding="utf-8",
            )
            (external_dir / "google_drive_inventory.jsonl").write_text(
                (
                    '{"source":"研究室知识库","layer":"wiki","path":"wiki/先贤/谷超豪.md",'
                    '"title":"谷超豪.md","url":"https://drive.example/gu",'
                    '"item_type":"file","import_decision":"外部参考，适合人物研究补充"}\n'
                ),
                encoding="utf-8",
            )
            body = external_sources_report_markdown(root, "2026-06-11T00:00:00")
            self.assertIn("Google Drive外部参考层状态", body)
            self.assertIn("已登记知识库：1 个", body)
            self.assertIn("谷超豪.md", body)
            self.assertIn("不等同于微信公众号主语料", body)
        finally:
            shutil.rmtree(root)

    def test_topic_similarity_prefers_containment(self) -> None:
        self.assertGreaterEqual(normalized_similarity("费孝通与江村", "午间盟史课堂：费孝通与江村"), 0.72)


if __name__ == "__main__":
    unittest.main()
