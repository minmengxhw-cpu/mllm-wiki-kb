from __future__ import annotations

import argparse
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kb.cli import (  # noqa: E402
    brief_body,
    build_parser,
    command_refresh,
    command_check,
    external_sources_report_markdown,
    guardrails_report_markdown,
    normalized_similarity,
    obsidian_status_markdown,
    obsidian_sync_status,
    staff_draft_body,
    staff_history_body,
    staff_info_body,
    staff_material_draft_body,
    staff_stats_body,
    verify_report_markdown,
)
from kb.indexing import dict_to_row, query_terms, text_vector  # noqa: E402
from kb.ingest import chunk_text, extract_doc, normalize_text, sha256_text  # noqa: E402
from kb.sources import (  # noqa: E402
    pro_source_intake_tasks,
    pro_source_query_seeds,
    pro_sources_report_markdown,
    sources_dashboard_markdown,
)
from kb.staff_check import staff_check_issues  # noqa: E402


class StaffCommandTests(unittest.TestCase):
    def make_root(self) -> Path:
        temp = Path(tempfile.mkdtemp())
        (temp / "index").mkdir(parents=True)
        shutil.copy(ROOT / "schema.sql", temp / "schema.sql")
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

    def test_parser_accepts_staff_info(self) -> None:
        args = build_parser().parse_args(["staff", "info", "科技创新人才"])
        self.assertEqual(args.command, "staff")
        self.assertEqual(args.staff_command, "info")
        self.assertEqual(args.topic, "科技创新人才")

    def test_parser_accepts_staff_stats(self) -> None:
        args = build_parser().parse_args(["staff", "stats", "2025 参政议政"])
        self.assertEqual(args.command, "staff")
        self.assertEqual(args.staff_command, "stats")
        self.assertEqual(args.topic, "2025 参政议政")

    def test_parser_accepts_brief(self) -> None:
        args = build_parser().parse_args(["brief", "80周年", "工作"])
        self.assertEqual(args.command, "brief")
        self.assertEqual(args.query, ["80周年", "工作"])

    def test_parser_accepts_pro_sources(self) -> None:
        args = build_parser().parse_args(["pro-sources", "--priority", "P0", "--save"])
        self.assertEqual(args.command, "pro-sources")
        self.assertEqual(args.priority, "P0")
        self.assertTrue(args.save)

    def test_parser_accepts_sources(self) -> None:
        args = build_parser().parse_args(["sources", "--save"])
        self.assertEqual(args.command, "sources")
        self.assertTrue(args.save)

    def test_check_command_can_act_as_hard_gate(self) -> None:
        root = self.make_root()
        try:
            args = argparse.Namespace(
                project_root=str(root),
                file=None,
                text=["沈均儒参与民盟特设支部工作，中国民主同盟成立于1941年3月19日。"],
            )
            with contextlib.redirect_stdout(io.StringIO()):
                code = command_check(args)
            self.assertEqual(code, 1)
        finally:
            shutil.rmtree(root)

    def test_ingest_extracts_markdown_article_metadata(self) -> None:
        root = self.make_root()
        try:
            account_dir = root / "input" / "上海民盟"
            account_dir.mkdir(parents=True)
            path = account_dir / "sample.md"
            path.write_text(
                "文章标题\n==============\n原创 作者 上海民盟 2026-06-19 10:00 上海\n\n正文内容\nhttps://mp.weixin.qq.com/s/example",
                encoding="utf-8",
            )
            doc = extract_doc(path, root / "input")
            self.assertEqual(doc.title, "文章标题")
            self.assertEqual(doc.account, "上海民盟")
            self.assertEqual(doc.published_at, "2026-06-19")
            self.assertIn("正文内容", doc.text)
            self.assertEqual(doc.source_url, "https://mp.weixin.qq.com/s/example")
        finally:
            shutil.rmtree(root)

    def test_ingest_helpers_normalize_hash_and_chunk(self) -> None:
        text = "正文\n\n点击上方蓝字关注我们\n\n![](x)\n\n第二段"
        self.assertNotIn("点击上方", normalize_text(text))
        self.assertEqual(sha256_text("abc"), sha256_text("abc"))
        chunks = chunk_text("第一段\n\n第二段", max_chars=20)
        self.assertEqual(chunks, ["第一段\n\n第二段"])

    def test_indexing_helpers_extract_known_terms_and_vectors(self) -> None:
        self.assertEqual(query_terms("沈钧儒与民盟史"), ["盟史", "沈钧儒"])
        vector = text_vector("沈钧儒 民盟史")
        self.assertEqual(len(vector), 256)
        self.assertGreater(sum(abs(item) for item in vector), 0)

    def test_pro_source_tasks_skip_deferred_sources(self) -> None:
        sources = [
            {
                "source_id": "AUTH-001",
                "name": "民盟中央官网",
                "source_level": "S1_authoritative_official",
                "priority": "P0",
                "ingest_decision": "优先全文入库",
                "topic_domains": ["民盟史"],
                "url": "https://example.test",
            },
            {
                "source_id": "AUTH-010",
                "name": "上海统一战线官网",
                "source_level": "S1_authoritative_official",
                "priority": "P1",
                "ingest_decision": "暂缓",
            },
            {
                "source_id": "INT-001",
                "name": "内部材料",
                "source_level": "S6_internal_reference",
                "priority": "P2",
                "ingest_decision": "仅人工查阅",
            },
        ]
        tasks = pro_source_intake_tasks(sources, "P0")
        seeds = pro_source_query_seeds(tasks)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["source_id"], "AUTH-001")
        self.assertGreaterEqual(len(seeds), 1)
        self.assertIn("民盟中央官网", seeds[0]["query"])

    def test_pro_sources_report_contains_guarded_workbench(self) -> None:
        root = self.make_root()
        try:
            pro_dir = root / "index" / "pro_sources"
            pro_dir.mkdir(parents=True)
            (pro_dir / "source_map.jsonl").write_text(
                '{"source_id":"AUTH-001","name":"民盟中央官网","source_level":"S1_authoritative_official","priority":"P0","ingest_decision":"优先全文入库","topic_domains":["民盟史"],"url":"https://example.test"}\n',
                encoding="utf-8",
            )
            (pro_dir / "source_types.json").write_text(
                '{"source_levels":[{"code":"S1_authoritative_official","name":"权威官方层"}]}',
                encoding="utf-8",
            )
            body = pro_sources_report_markdown(root, "2026-06-19T00:00:00", "P0")
            self.assertIn("专业语料库首批来源入库工作台", body)
            self.assertIn("民盟中央官网", body)
            self.assertIn("不把未核验材料直接写成史实结论", body)
        finally:
            shutil.rmtree(root)

    def test_sources_dashboard_marks_authority_and_citable_boundary(self) -> None:
        root = self.make_root()
        try:
            pro_dir = root / "index" / "pro_sources"
            pro_dir.mkdir(parents=True)
            (pro_dir / "source_map.jsonl").write_text(
                '{"source_id":"AUTH-001","name":"民盟中央官网","source_level":"S1_authoritative_official","authority_level":"L1","source_tier":"权威定本层","is_citable":true,"ingest_decision":"优先全文入库","collection_method":"手动喂 URL + 站点解析","url":"https://example.test"}\n'
                '{"source_id":"INT-001","name":"内部材料","source_level":"S6_internal_reference","authority_level":"L4","source_tier":"参考与样本层","is_citable":false,"ingest_decision":"仅人工查阅","collection_method":"人工查阅","url":""}\n',
                encoding="utf-8",
            )
            body = sources_dashboard_markdown(root, "2026-06-19T00:00:00")
            self.assertIn("权威公开资料来源体检", body)
            self.assertIn("L1-L3 作为事实层", body)
            self.assertIn("民盟中央官网", body)
            self.assertIn("内部材料", body)
        finally:
            shutil.rmtree(root)

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

    def test_staff_check_flags_formulation_variants(self) -> None:
        root = self.make_root()
        try:
            text = "公众号口径即内部口径，可以先写结论后补出处。"
            issues = staff_check_issues(root, text)
            patterns = {item["pattern"] for item in issues}
            suggestions = {item["suggestion"] for item in issues}
            self.assertIn("公众号口径即内部口径", patterns)
            self.assertIn("先写结论后补出处", patterns)
            self.assertIn("红头文件、内部口径和人工终审优先级高于本公开语料库", suggestions)
            self.assertIn("事实性表述须带来源；无出处内容标注[待核]", suggestions)
        finally:
            shutil.rmtree(root)

    def test_staff_check_flags_expanded_seed_risks(self) -> None:
        root = self.make_root()
        try:
            text = "上海民盟市委纪念80周年就是民盟成立80周年，张兰参加五一号召相关活动。"
            issues = staff_check_issues(root, text)
            patterns = {item["pattern"] for item in issues}
            suggestions = {item["suggestion"] for item in issues}
            self.assertIn("上海民盟市委", patterns)
            self.assertIn("张兰", patterns)
            self.assertIn("五一号召", patterns)
            self.assertIn("80周年相关文稿应先核定纪念对象、组织沿革、活动层级和正式口径，再组织历史叙事与现实工作", suggestions)
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

    def test_staff_info_body_uses_problem_solution_structure(self) -> None:
        root = self.make_root()
        try:
            row = dict_to_row(
                {
                    "article_id": 1,
                    "chunk_id": 1,
                    "title": "民盟市委围绕科技创新开展调研",
                    "account": "上海民盟",
                    "published_at": "2025-01-02",
                    "raw_path": "/tmp/info.md",
                    "snippet": "调研聚焦科技创新人才发展，提出建议。",
                    "score": 0,
                }
            )
            body = staff_info_body(root, "科技创新人才", [row])
            self.assertIn("盟参 /信", body)
            self.assertIn("问题发现素材", body)
            self.assertIn("对策建议骨架", body)
            self.assertIn("[S1]", body)
        finally:
            shutil.rmtree(root)

    def test_brief_body_uses_brief_structure(self) -> None:
        row = dict_to_row(
            {
                "article_id": 1,
                "chunk_id": 1,
                "title": "民盟市委围绕80周年开展工作",
                "account": "上海民盟",
                "published_at": "2026-01-02",
                "raw_path": "/tmp/brief.md",
                "snippet": "围绕80周年开展相关工作。",
                "score": 0,
            }
        )
        body = brief_body("80周年工作", [row])
        self.assertIn("民盟简报素材", body)
        self.assertIn("三点摘要", body)
        self.assertIn("简报结构建议", body)
        self.assertIn("[S1]", body)

    def test_staff_stats_body_uses_article_labels(self) -> None:
        root = self.make_root()
        try:
            corpus_dir = root / "index" / "corpus"
            corpus_dir.mkdir(parents=True)
            (corpus_dir / "article_labels.jsonl").write_text(
                (
                    '{"article_id":1,"title":"科技创新调研","account":"上海民盟","published_at":"2025-01-01",'
                    '"year":"2025","article_type_name":"参政议政","topic_tags":["参政议政"],"people":[],'
                    '"raw_path":"/tmp/1.md"}\n'
                    '{"article_id":2,"title":"主题教育活动","account":"中国民主同盟","published_at":"2024-01-01",'
                    '"year":"2024","article_type_name":"主题教育","topic_tags":["主题教育"],"people":[],'
                    '"raw_path":"/tmp/2.md"}\n'
                    '{"article_id":3,"title":"2025年度参政议政表彰","account":"中国民主同盟","published_at":"2026-01-01",'
                    '"year":"2026","article_type_name":"参政议政","topic_tags":["参政议政"],"people":[],'
                    '"raw_path":"/tmp/3.md"}\n'
                ),
                encoding="utf-8",
            )
            body = staff_stats_body(root, "2025 参政议政")
            self.assertIn("盟参 /数", body)
            self.assertIn("命中 1 篇文章", body)
            self.assertIn("上海民盟", body)
            self.assertIn("/tmp/1.md", body)
            self.assertNotIn("/tmp/3.md", body)
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

    def test_verify_report_lists_staff_modes_and_outputs(self) -> None:
        root = self.make_root()
        try:
            (root / "wiki" / "研究助手").mkdir(parents=True)
            (root / "index" / "corpus").mkdir(parents=True)
            (root / "index" / "external_sources").mkdir(parents=True)
            (root / "index" / "corpus" / "article_labels.jsonl").write_text(
                '{"article_id":1,"title":"测试","account":"上海民盟","year":"2025"}\n',
                encoding="utf-8",
            )
            (root / "index" / "external_sources" / "google_drive_inventory.jsonl").write_text(
                '{"title":"测试.md"}\n',
                encoding="utf-8",
            )
            body = verify_report_markdown(root, "2026-06-12T00:00:00")
            self.assertIn("盟参系统可用性验收报告", body)
            self.assertIn("/稿", body)
            self.assertIn("/数", body)
            self.assertIn("文章标签", body)
            self.assertIn("Drive 外部参考记录", body)
        finally:
            shutil.rmtree(root)

    def test_obsidian_status_detects_missing_and_stale_files(self) -> None:
        root = self.make_root()
        vault = root / "vault"
        try:
            (root / "wiki" / "研究助手").mkdir(parents=True)
            (root / "wiki" / "index.md").parent.mkdir(parents=True, exist_ok=True)
            (root / "wiki" / "index.md").write_text("首页", encoding="utf-8")
            (root / "wiki" / "研究助手" / "A.md").write_text("A", encoding="utf-8")
            (root / "wiki" / "研究助手" / "B.md").write_text("B", encoding="utf-8")
            (vault / "00-总索引").mkdir(parents=True)
            (vault / "01-研究助手").mkdir(parents=True)
            (vault / "00-总索引" / "首页.md").write_text("首页", encoding="utf-8")
            (vault / "01-研究助手" / "A.md").write_text("旧A", encoding="utf-8")

            status = obsidian_sync_status(root, vault)
            self.assertEqual(status["current"], 1)
            self.assertEqual(len(status["stale"]), 1)
            self.assertEqual(len(status["missing"]), 1)

            body = obsidian_status_markdown(root, vault, "2026-06-12T00:00:00")
            self.assertIn("Obsidian 同步状态", body)
            self.assertIn("需更新", body)
            self.assertIn("缺失", body)
        finally:
            shutil.rmtree(root)

    def test_parser_accepts_obsidian_status(self) -> None:
        args = build_parser().parse_args(["obsidian-status", "--save"])
        self.assertEqual(args.command, "obsidian-status")
        self.assertTrue(args.save)

    def test_guardrails_report_lists_blacklist_and_formulations(self) -> None:
        root = self.make_root()
        try:
            body = guardrails_report_markdown(root, "2026-06-12T00:00:00")
            self.assertIn("口径风险清单", body)
            self.assertIn("黑名单词条", body)
            self.assertIn("上海民盟市委", body)
            self.assertIn("80周年纪念写作边界", body)
        finally:
            shutil.rmtree(root)

    def test_parser_accepts_guardrails(self) -> None:
        args = build_parser().parse_args(["guardrails", "--save"])
        self.assertEqual(args.command, "guardrails")
        self.assertTrue(args.save)

    def test_refresh_dry_run_mentions_full_refresh_chain(self) -> None:
        root = self.make_root()
        input_dir = root / "input"
        input_dir.mkdir()
        (input_dir / "sample.md").write_text(
            "# 测试文章\n\n原创 上海民盟 2026-01-01\n\n测试内容。",
            encoding="utf-8",
        )
        try:
            args = argparse.Namespace(
                project_root=str(root),
                input=str(input_dir),
                limit=1,
                top_k=2,
                dry_run=True,
                vault="",
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = command_refresh(args)
            self.assertEqual(code, 0)
            text = out.getvalue()
            self.assertIn("corpus reports", text)
            self.assertIn("research dossiers", text)
            self.assertIn("guardrails report", text)
            self.assertIn("verification report", text)
        finally:
            shutil.rmtree(root)

    def test_topic_similarity_prefers_containment(self) -> None:
        self.assertGreaterEqual(normalized_similarity("费孝通与江村", "午间盟史课堂：费孝通与江村"), 0.72)


if __name__ == "__main__":
    unittest.main()
