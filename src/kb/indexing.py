from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from pathlib import Path

from kb.embeddings import HASH_MODEL, resolve_embedding_backend
from kb.store import connect_db, ensure_schema_columns, now_iso


AUTHORITY_RANKS = {
    "L1": 0,
    "L2": 1,
    "L3": 2,
    "L4": 3,
}


def init_db_schema(root: Path) -> None:
    conn = connect_db(root)
    try:
        conn.executescript((root / "schema.sql").read_text(encoding="utf-8"))
        ensure_schema_columns(conn)
        conn.commit()
    finally:
        conn.close()


def rebuild_fts(root: Path) -> tuple[int, int]:
    conn = connect_db(root)
    try:
        conn.executescript((root / "schema.sql").read_text(encoding="utf-8"))
        conn.execute("DELETE FROM article_chunks_fts")
        rows = conn.execute(
            """
            SELECT c.id AS chunk_id, c.article_id, c.content, a.title, a.account, a.published_at, a.raw_path
            FROM article_chunks c
            JOIN articles a ON a.id = c.article_id
            ORDER BY c.article_id, c.chunk_index
            """
        ).fetchall()
        with conn:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO article_chunks_fts(content, title, account, published_at, raw_path, article_id, chunk_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["content"],
                        row["title"],
                        row["account"],
                        row["published_at"],
                        row["raw_path"],
                        row["article_id"],
                        row["chunk_id"],
                    ),
                )
        count = conn.execute("SELECT COUNT(*) FROM article_chunks_fts").fetchone()[0]
        return len(rows), count
    finally:
        conn.close()


def fts_query(value: str) -> str:
    terms = [t for t in re.split(r"\s+", value.strip()) if t]
    if not terms:
        return value
    return " AND ".join(f'"{t}"' for t in terms)


def query_terms(value: str) -> list[str]:
    split_terms = [t for t in re.split(r"\s+", value.strip()) if t]
    if len(split_terms) > 1:
        return split_terms
    known = [
        "上海民盟",
        "中国民主同盟",
        "民盟中央",
        "盟史",
        "资源",
        "活化",
        "传统教育基地",
        "主题教育",
        "基层",
        "落实",
        "机制",
        "参政为公",
        "实干为民",
        "参政议政",
        "建言",
        "提案",
        "社情民意",
        "写作",
        "素材",
        "人物",
        "采访",
        "专访",
        "五一口号",
        "旧政协",
        "人民政协",
        "新政协",
        "李闻",
        "多党合作",
        "统一战线",
        "政治交接",
        "自身建设",
        "社会服务",
        "黄丝带",
        "烛光行动",
        "毕节",
        "传统",
        "先贤",
        "张澜",
        "沈钧儒",
        "黄炎培",
        "史良",
        "特园",
    ]
    terms = [term for term in known if term in value]
    if terms:
        return terms
    return split_terms or [value]


def vector_tokens(text: str) -> list[str]:
    text = re.sub(r"\s+", "", text)
    tokens = []
    for size in (2, 3, 4):
        for i in range(0, max(0, len(text) - size + 1)):
            token = text[i : i + size]
            if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", token):
                tokens.append(token)
    tokens.extend(query_terms(text))
    return tokens


def text_vector(text: str, dims: int = 256) -> list[float]:
    vec = [0.0] * dims
    for token in vector_tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:2], "big") % dims
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm:
        vec = [round(v / norm, 6) for v in vec]
    return vec


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def rebuild_vectors_with_info(root: Path, preferred_model: str | None = None) -> tuple[int, int, str, int, str | None]:
    conn = connect_db(root)
    try:
        ensure_schema_columns(conn)
        conn.execute("DELETE FROM chunk_vectors")
        rows = conn.execute(
            """
            SELECT c.id AS chunk_id, c.article_id, c.content, a.title
            FROM article_chunks c
            JOIN articles a ON a.id = c.article_id
            ORDER BY c.id
            """
        ).fetchall()
        backend, status = resolve_embedding_backend(preferred_model)
        with conn:
            texts = [f"{row['title']}\n{row['content']}" for row in rows]
            vectors = backend.encode(texts)
            for row, vector in zip(rows, vectors):
                conn.execute(
                    "INSERT INTO chunk_vectors(chunk_id, article_id, model, dim, vector_json, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        row["chunk_id"],
                        row["article_id"],
                        status.model,
                        len(vector),
                        json.dumps(vector, separators=(",", ":")),
                        now_iso(),
                    ),
                )
        count = conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
        return len(rows), count, status.model, status.dim, status.fallback_reason
    finally:
        conn.close()


def rebuild_vectors(root: Path) -> tuple[int, int]:
    rows, count, _, _, _ = rebuild_vectors_with_info(root)
    return rows, count


def dict_to_row(data: dict) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    keys = list(data.keys())
    conn.execute("CREATE TABLE t (" + ",".join(f"{key} TEXT" for key in keys) + ")")
    conn.execute("INSERT INTO t VALUES (" + ",".join("?" for _ in keys) + ")", [data[key] for key in keys])
    row = conn.execute("SELECT * FROM t").fetchone()
    conn.close()
    return row


def authority_rank(value: str | None) -> int:
    return AUTHORITY_RANKS.get(str(value or "L4"), 9)


def authority_select(prefix: str = "a") -> str:
    return (
        f"{prefix}.source_id AS source_id, "
        f"{prefix}.authority_level AS authority_level, "
        f"{prefix}.source_tier AS source_tier, "
        f"{prefix}.is_citable AS is_citable"
    )


def sort_rows_by_authority(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return sorted(
        rows,
        key=lambda row: (
            authority_rank(row["authority_level"] if "authority_level" in row.keys() else None),
            -int(row["is_citable"] or 0) if "is_citable" in row.keys() else 0,
        ),
    )


def semantic_rows(root: Path, query: str, top_k: int) -> list[sqlite3.Row]:
    conn = connect_db(root)
    try:
        ensure_schema_columns(conn)
        if conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0] == 0:
            conn.close()
            rebuild_vectors(root)
            conn = connect_db(root)
        model_row = conn.execute(
            "SELECT model, dim, COUNT(*) AS count FROM chunk_vectors GROUP BY model, dim ORDER BY count DESC LIMIT 1"
        ).fetchone()
        model = model_row["model"] if model_row else HASH_MODEL
        dim = int(model_row["dim"] or 256) if model_row else 256
        backend, status = resolve_embedding_backend(model)
        if status.model != model:
            return []
        qvec = backend.encode([query])[0]
        if len(qvec) != dim:
            return []
        rows = conn.execute(
            """
            SELECT v.chunk_id, v.article_id, v.model, v.dim, v.vector_json, c.content, a.title, a.account, a.published_at, a.raw_path,
                   a.source_id, a.authority_level, a.source_tier, a.is_citable
            FROM chunk_vectors v
            JOIN article_chunks c ON c.id = v.chunk_id
            JOIN articles a ON a.id = v.article_id
            WHERE v.model = ? AND v.dim = ?
            """
            ,
            (model, dim),
        ).fetchall()
        scored = []
        for row in rows:
            vector = json.loads(row["vector_json"])
            if len(vector) != len(qvec):
                continue
            score = cosine_similarity(qvec, vector)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        out = []
        for score, row in scored[:top_k]:
            out.append(
                {
                    "article_id": row["article_id"],
                    "chunk_id": row["chunk_id"],
                    "title": row["title"],
                    "account": row["account"],
                    "published_at": row["published_at"],
                    "raw_path": row["raw_path"],
                    "source_id": row["source_id"],
                    "authority_level": row["authority_level"],
                    "source_tier": row["source_tier"],
                    "is_citable": row["is_citable"],
                    "snippet": row["content"][:220],
                    "score": score,
                }
            )
        return [dict_to_row(row) for row in out]
    finally:
        conn.close()


def search_rows(root: Path, query: str, top_k: int = 20) -> list[sqlite3.Row]:
    init_db_schema(root)
    conn = connect_db(root)
    try:
        fts_count = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='article_chunks_fts'"
        ).fetchone()
        if not fts_count or conn.execute("SELECT COUNT(*) FROM article_chunks_fts").fetchone()[0] == 0:
            conn.close()
            rebuild_fts(root)
            conn = connect_db(root)
        q = fts_query(query)
        rows = conn.execute(
            f"""
            SELECT article_chunks_fts.article_id AS article_id,
                   article_chunks_fts.chunk_id AS chunk_id,
                   a.title AS title,
                   a.account AS account,
                   a.published_at AS published_at,
                   a.raw_path AS raw_path,
                   a.source_id AS source_id,
                   a.authority_level AS authority_level,
                   a.source_tier AS source_tier,
                   a.is_citable AS is_citable,
                   snippet(article_chunks_fts, 0, '[', ']', '...', 18) AS snippet,
                   bm25(article_chunks_fts) AS score
            FROM article_chunks_fts
            JOIN articles a ON a.id = article_chunks_fts.article_id
            WHERE article_chunks_fts MATCH ?
            ORDER BY
                CASE a.authority_level WHEN 'L1' THEN 0 WHEN 'L2' THEN 1 WHEN 'L3' THEN 2 WHEN 'L4' THEN 3 ELSE 9 END,
                a.is_citable DESC,
                score
            LIMIT ?
            """,
            (q, top_k),
        ).fetchall()
        if not rows:
            terms = query_terms(query)
            if terms:
                where = " AND ".join(["c.content LIKE ?" for _ in terms])
                params = [f"%{term}%" for term in terms]
            else:
                where = "c.content LIKE ?"
                params = [f"%{query}%"]
            rows = conn.execute(
                f"""
                SELECT c.article_id, c.id AS chunk_id, a.title, a.account, a.published_at, a.raw_path,
                       {authority_select("a")},
                       substr(c.content, 1, 220) AS snippet,
                       0 AS score
                FROM article_chunks c
                JOIN articles a ON a.id = c.article_id
                WHERE {where}
                ORDER BY
                    CASE a.authority_level WHEN 'L1' THEN 0 WHEN 'L2' THEN 1 WHEN 'L3' THEN 2 WHEN 'L4' THEN 3 ELSE 9 END,
                    a.is_citable DESC,
                    a.published_at DESC,
                    c.article_id,
                    c.chunk_index
                LIMIT ?
                """,
                (*params, top_k),
            ).fetchall()
        if not rows:
            terms = query_terms(query)
            if terms:
                where = " OR ".join(["c.content LIKE ?" for _ in terms])
                score_expr = " + ".join(["CASE WHEN c.content LIKE ? THEN 1 ELSE 0 END" for _ in terms])
                params = [f"%{term}%" for term in terms]
                score_params = [f"%{term}%" for term in terms]
                rows = conn.execute(
                    f"""
                    SELECT c.article_id, c.id AS chunk_id, a.title, a.account, a.published_at, a.raw_path,
                           {authority_select("a")},
                           substr(c.content, 1, 220) AS snippet,
                           ({score_expr}) AS score
                    FROM article_chunks c
                    JOIN articles a ON a.id = c.article_id
                    WHERE {where}
                    ORDER BY
                        CASE a.authority_level WHEN 'L1' THEN 0 WHEN 'L2' THEN 1 WHEN 'L3' THEN 2 WHEN 'L4' THEN 3 ELSE 9 END,
                        a.is_citable DESC,
                        score DESC,
                        a.published_at DESC,
                        c.article_id,
                        c.chunk_index
                    LIMIT ?
                    """,
                    (*score_params, *params, top_k),
                ).fetchall()
        elif len(rows) < top_k:
            terms = query_terms(query)
            if terms:
                existing_chunk_ids = {int(row["chunk_id"]) for row in rows if row["chunk_id"] is not None}
                where = " OR ".join(["c.content LIKE ?" for _ in terms])
                score_expr = " + ".join(["CASE WHEN c.content LIKE ? THEN 1 ELSE 0 END" for _ in terms])
                params = [f"%{term}%" for term in terms]
                score_params = [f"%{term}%" for term in terms]
                exclude = ""
                exclude_params: list[int] = []
                if existing_chunk_ids:
                    placeholders = ",".join(["?"] * len(existing_chunk_ids))
                    exclude = f" AND c.id NOT IN ({placeholders})"
                    exclude_params = sorted(existing_chunk_ids)
                supplement = conn.execute(
                    f"""
                    SELECT c.article_id, c.id AS chunk_id, a.title, a.account, a.published_at, a.raw_path,
                           {authority_select("a")},
                           substr(c.content, 1, 220) AS snippet,
                           ({score_expr}) AS score
                    FROM article_chunks c
                    JOIN articles a ON a.id = c.article_id
                    WHERE ({where}){exclude}
                    ORDER BY
                        CASE a.authority_level WHEN 'L1' THEN 0 WHEN 'L2' THEN 1 WHEN 'L3' THEN 2 WHEN 'L4' THEN 3 ELSE 9 END,
                        a.is_citable DESC,
                        score DESC,
                        a.published_at DESC,
                        c.article_id,
                        c.chunk_index
                    LIMIT ?
                    """,
                    (*score_params, *params, *exclude_params, top_k - len(rows)),
                ).fetchall()
                rows = list(rows) + list(supplement)
        if len(rows) < top_k:
            existing_chunk_ids = {str(row["chunk_id"]) for row in rows if row["chunk_id"] is not None}
            semantic = semantic_rows(root, query, top_k)
            for row in semantic:
                if str(row["chunk_id"]) in existing_chunk_ids:
                    continue
                rows = list(rows) + [row]
                existing_chunk_ids.add(str(row["chunk_id"]))
                if len(rows) >= top_k:
                    break
    finally:
        conn.close()
    return sort_rows_by_authority(list(rows))
