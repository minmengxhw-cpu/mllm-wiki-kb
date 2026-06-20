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
    wiki_columns = {row["name"] for row in conn.execute("PRAGMA table_info(wiki_pages)").fetchall()}
    wiki_additions = {
        "review_status": "ALTER TABLE wiki_pages ADD COLUMN review_status TEXT NOT NULL DEFAULT '待校订'",
        "priority_card": "ALTER TABLE wiki_pages ADD COLUMN priority_card INTEGER NOT NULL DEFAULT 0",
        "card_batch": "ALTER TABLE wiki_pages ADD COLUMN card_batch TEXT",
    }
    for name, sql in wiki_additions.items():
        if name not in wiki_columns:
            conn.execute(sql)
    article_columns = {row["name"] for row in conn.execute("PRAGMA table_info(articles)").fetchall()}
    article_additions = {
        "source_id": "ALTER TABLE articles ADD COLUMN source_id TEXT",
        "source_tier": "ALTER TABLE articles ADD COLUMN source_tier TEXT NOT NULL DEFAULT 'L4'",
        "authority_level": "ALTER TABLE articles ADD COLUMN authority_level TEXT NOT NULL DEFAULT 'L4'",
        "is_citable": "ALTER TABLE articles ADD COLUMN is_citable INTEGER NOT NULL DEFAULT 0",
    }
    for name, sql in article_additions.items():
        if name not in article_columns:
            conn.execute(sql)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sources (
            source_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT,
            authority_level TEXT NOT NULL,
            source_tier TEXT NOT NULL,
            is_citable INTEGER NOT NULL DEFAULT 0,
            collection_method TEXT,
            update_frequency TEXT,
            copyright_boundary TEXT,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_pages_review_status ON wiki_pages(review_status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiki_pages_priority_card ON wiki_pages(priority_card)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_source_id ON articles(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_authority_level ON articles(authority_level)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_source_tier ON articles(source_tier)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_is_citable ON articles(is_citable)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_authority_level ON sources(authority_level)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunk_vectors (
            chunk_id INTEGER PRIMARY KEY,
            article_id INTEGER NOT NULL,
            model TEXT NOT NULL DEFAULT 'hash-local-v1',
            dim INTEGER NOT NULL DEFAULT 256,
            vector_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(chunk_id) REFERENCES article_chunks(id) ON DELETE CASCADE,
            FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
        )
        """
    )
    vector_columns = {row["name"] for row in conn.execute("PRAGMA table_info(chunk_vectors)").fetchall()}
    vector_additions = {
        "model": "ALTER TABLE chunk_vectors ADD COLUMN model TEXT NOT NULL DEFAULT 'hash-local-v1'",
        "dim": "ALTER TABLE chunk_vectors ADD COLUMN dim INTEGER NOT NULL DEFAULT 256",
    }
    for name, sql in vector_additions.items():
        if name not in vector_columns:
            conn.execute(sql)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunk_vectors_article_id ON chunk_vectors(article_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunk_vectors_model ON chunk_vectors(model)")
