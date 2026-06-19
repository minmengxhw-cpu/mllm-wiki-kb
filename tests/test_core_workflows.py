from __future__ import annotations

import argparse
import contextlib
import io
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kb.cli import (  # noqa: E402
    command_ask,
    command_export,
    command_import,
    command_index,
    command_obsidian_sync,
    command_search,
    command_staff,
    ensure_dirs,
    init_db,
    write_wiki_page,
)
from kb.store import connect_db  # noqa: E402


class CoreWorkflowFeatureTests(unittest.TestCase):
    def make_root(self) -> Path:
        root = Path(tempfile.mkdtemp(prefix="kb-core-workflow-"))
        shutil.copy2(ROOT / "schema.sql", root / "schema.sql")
        ensure_dirs(root)
        init_db(root)
        return root

    def write_fixture_articles(self, root: Path) -> Path:
        input_dir = root / "input" / "上海民盟"
        input_dir.mkdir(parents=True)
        article = (
            "新型政党制度研究\n\n"
            "原创 上海民盟 2026-06-19 10:00 上海\n\n"
            "沈钧儒参与民盟历史研究。中国新型政党制度与多党合作、政治协商密切相关。"
        )
        (input_dir / "a.md").write_text(article, encoding="utf-8")
        (input_dir / "duplicate.md").write_text(article, encoding="utf-8")
        (input_dir / "b.md").write_text(
            "五一口号研究\n\n"
            "原创 上海民盟 2026-06-18 09:00 上海\n\n"
            "民盟响应五一口号，推动新政协进程，是多党合作史的重要线索。",
            encoding="utf-8",
        )
        return root / "input"

    def import_fixture(self, root: Path, input_root: Path) -> None:
        args = argparse.Namespace(
            project_root=str(root),
            input=str(input_root),
            limit=10,
            dry_run=False,
            source_id="TEST-001",
            authority_level="L4",
            source_tier="参考与样本层",
            is_citable=False,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(command_import(args), 0)

    def test_import_index_search_ask_feature_chain(self) -> None:
        root = self.make_root()
        try:
            input_root = self.write_fixture_articles(root)
            self.import_fixture(root, input_root)

            conn = connect_db(root)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 2)
                hashes = [row[0] for row in conn.execute("SELECT content_hash FROM articles").fetchall()]
                self.assertEqual(len(hashes), len(set(hashes)))
            finally:
                conn.close()

            with contextlib.redirect_stdout(io.StringIO()) as index_out:
                self.assertEqual(command_index(argparse.Namespace(project_root=str(root))), 0)
            self.assertIn("SQLite FTS indexed chunks", index_out.getvalue())
            self.assertIn("Local vector indexed chunks", index_out.getvalue())

            search_args = argparse.Namespace(project_root=str(root), query="新型政党制度 多党合作", top_k=3)
            with contextlib.redirect_stdout(io.StringIO()) as search_out:
                self.assertEqual(command_search(search_args), 0)
            self.assertIn("新型政党制度研究", search_out.getvalue())
            self.assertIn("source=L4/样本", search_out.getvalue())

            ask_args = argparse.Namespace(project_root=str(root), query="新型政党制度和多党合作有什么关系", top_k=2)
            with contextlib.redirect_stdout(io.StringIO()) as ask_out:
                self.assertEqual(command_ask(ask_args), 0)
            text = ask_out.getvalue()
            self.assertIn("[S1]", text)
            self.assertIn("## 来源", text)
            self.assertIn("raw:", text)
        finally:
            shutil.rmtree(root)

    def test_staff_draft_marks_missing_material_as_pending_check(self) -> None:
        root = self.make_root()
        try:
            input_root = self.write_fixture_articles(root)
            self.import_fixture(root, input_root)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(command_index(argparse.Namespace(project_root=str(root))), 0)
            args = argparse.Namespace(
                project_root=str(root),
                staff_command="draft",
                topic="活动报道",
                top_k=3,
                material="今天举行活动，某领导出席并讲话。",
                file=None,
                save=False,
            )
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(command_staff(args), 0)
            self.assertIn("[待核]", out.getvalue())
        finally:
            shutil.rmtree(root)

    def test_export_and_obsidian_sync_preserve_human_notes_with_backup(self) -> None:
        root = self.make_root()
        try:
            input_root = self.write_fixture_articles(root)
            self.import_fixture(root, input_root)
            conn = connect_db(root)
            try:
                rows = conn.execute(
                    """
                    SELECT a.id AS article_id, c.id AS chunk_id, a.title, a.account, a.published_at,
                           a.raw_path, c.content AS snippet, a.authority_level, a.is_citable
                    FROM articles a
                    JOIN article_chunks c ON c.article_id = a.id
                    ORDER BY a.id
                    LIMIT 1
                    """
                ).fetchall()
            finally:
                conn.close()
            page = write_wiki_page(root, "测试导出页", "assistant", "# 测试导出页\n\n正文", list(rows))

            export_args = argparse.Namespace(
                project_root=str(root),
                title=None,
                path=str(page.relative_to(root)),
                format=["markdown"],
                output_name="feature-export",
            )
            with contextlib.redirect_stdout(io.StringIO()) as export_out:
                self.assertEqual(command_export(export_args), 0)
            exported = root / "exports" / "markdown" / "feature-export.md"
            self.assertTrue(exported.exists(), export_out.getvalue())
            self.assertIn("测试导出页", exported.read_text(encoding="utf-8"))

            vault = root / "vault"
            sync_args = argparse.Namespace(project_root=str(root), vault=str(vault), dry_run=False)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(command_obsidian_sync(sync_args), 0)
            dest = vault / "01-研究助手" / "测试导出页.md"
            self.assertTrue(dest.exists())
            first = dest.read_text(encoding="utf-8")
            self.assertIn("人工补充区", first)
            dest.write_text(first.replace("人工补充区", "人工保留内容"), encoding="utf-8")

            page.write_text(
                page.read_text(encoding="utf-8").replace("正文", "更新后的正文"),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(command_obsidian_sync(sync_args), 0)
            updated = dest.read_text(encoding="utf-8")
            self.assertIn("更新后的正文", updated)
            self.assertIn("人工保留内容", updated)
            self.assertTrue(list(dest.parent.glob("测试导出页.md.bak-*")))
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
