PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    account TEXT,
    author TEXT,
    published_at TEXT,
    source_path TEXT NOT NULL,
    raw_path TEXT,
    source_url TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    imported_at TEXT NOT NULL,
    file_type TEXT,
    status TEXT NOT NULL DEFAULT 'imported'
);

CREATE TABLE IF NOT EXISTS article_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    token_estimate INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE,
    UNIQUE(article_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    aliases TEXT,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(name, entity_type)
);

CREATE TABLE IF NOT EXISTS article_entities (
    article_id INTEGER NOT NULL,
    entity_id INTEGER NOT NULL,
    relation TEXT,
    confidence TEXT DEFAULT 'medium',
    created_at TEXT NOT NULL,
    PRIMARY KEY(article_id, entity_id, relation),
    FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wiki_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    page_type TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    source_count INTEGER NOT NULL DEFAULT 0,
    needs_review INTEGER NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT '待校订',
    priority_card INTEGER NOT NULL DEFAULT 0,
    card_batch TEXT,
    obsidian_path TEXT,
    content_hash TEXT,
    last_compiled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wiki_sources (
    wiki_page_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    raw_path TEXT,
    citation_note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(wiki_page_id, article_id),
    FOREIGN KEY(wiki_page_id) REFERENCES wiki_pages(id) ON DELETE CASCADE,
    FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS operations_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(chunk_id) REFERENCES article_chunks(id) ON DELETE CASCADE,
    FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_articles_published_at ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_account ON articles(account);
CREATE INDEX IF NOT EXISTS idx_articles_source_url ON articles(source_url);
CREATE INDEX IF NOT EXISTS idx_article_chunks_article_id ON article_chunks(article_id);
CREATE INDEX IF NOT EXISTS idx_entities_type_name ON entities(entity_type, name);
CREATE INDEX IF NOT EXISTS idx_chunk_vectors_article_id ON chunk_vectors(article_id);

CREATE VIRTUAL TABLE IF NOT EXISTS article_chunks_fts USING fts5(
    content,
    title UNINDEXED,
    account UNINDEXED,
    published_at UNINDEXED,
    raw_path UNINDEXED,
    article_id UNINDEXED,
    chunk_id UNINDEXED,
    tokenize = 'unicode61'
);
