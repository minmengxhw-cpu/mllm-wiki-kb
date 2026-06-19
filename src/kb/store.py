from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def db_path(root: Path) -> Path:
    return root / "index" / "kb.sqlite"


def connect_db(root: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(root))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(wiki_pages)").fetchall()}
    additions = {
        "review_status": "ALTER TABLE wiki_pages ADD COLUMN review_status TEXT NOT NULL DEFAULT '待校订'",
        "priority_card": "ALTER TABLE wiki_pages ADD COLUMN priority_card INTEGER NOT NULL DEFAULT 0",
        "card_batch": "ALTER TABLE wiki_pages ADD COLUMN card_batch TEXT",
    }
    for name, sql in additions.items():
        if name not in columns:
            conn.execute(sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_pages_review_status ON wiki_pages(review_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_pages_priority_card ON wiki_pages(priority_card)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_vectors (
            chunk_id INTEGER PRIMARY KEY,
            article_id INTEGER NOT NULL,
            vector_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(chunk_id) REFERENCES article_chunks(id) ON DELETE CASCADE,
            FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunk_vectors_article_id ON chunk_vectors(article_id)")
