"""Hybrid search (FTS5 BM25 + vec0 KNN) with RRF fusion for obsidian-search-tools.

Query path:
1. Prepend the BGE query instruction to the query string.
2. Run FTS5 BM25 (top 50) and vec0 KNN (top 50) in parallel.
3. Fuse results via Reciprocal Rank Fusion (k=60).
4. Dedup: keep at most 2 chunks per note path.
5. Return top ``limit`` SearchResult objects.

Section filter for vec0 is applied as a vec0 partition key query (AND section = ?),
NOT by post-fetch Python filtering.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass

import sqlite_vec
from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_RRF_K = 60
_KNN_LIMIT = 50
_BM25_LIMIT = 50
_MAX_PER_NOTE = 2


@dataclass
class SearchResult:
    path: str
    section: str
    heading_path: str
    chunk_text: str
    score: float


def _embed_query(model: TextEmbedding, query: str) -> bytes:
    """Embed a query string with the BGE query instruction prefix."""
    prefixed = _QUERY_PREFIX + query
    embeddings = list(model.embed([prefixed]))
    return sqlite_vec.serialize_float32(embeddings[0].tolist())


def _bm25_search(
    conn: sqlite3.Connection,
    query: str,
    section: str | None,
) -> list[tuple[int, float]]:
    """Return (rowid, rank) pairs from FTS5 BM25 search, best match first."""
    try:
        if section is not None:
            rows = conn.execute(
                """
                SELECT rowid, rank
                FROM chunks_fts
                WHERE chunk_text MATCH ?
                  AND rowid IN (SELECT id FROM chunks WHERE section = ?)
                ORDER BY rank
                LIMIT ?
                """,
                (query, section, _BM25_LIMIT),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT rowid, rank FROM chunks_fts WHERE chunk_text MATCH ? ORDER BY rank LIMIT ?",
                (query, _BM25_LIMIT),
            ).fetchall()
        return [(int(r[0]), float(r[1])) for r in rows]
    except Exception:
        logger.warning("FTS5 search failed, falling back to KNN-only", exc_info=True)
        return []


def _knn_search(
    conn: sqlite3.Connection,
    embedding_bytes: bytes,
    section: str | None,
) -> list[tuple[int, float]]:
    """Return (rowid, distance) pairs from vec0 KNN search, closest first."""
    try:
        if section is not None:
            rows = conn.execute(
                """
                SELECT rowid, distance
                FROM chunks_vec
                WHERE embedding MATCH ?
                  AND section = ?
                ORDER BY distance
                LIMIT ?
                """,
                (embedding_bytes, section, _KNN_LIMIT),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT rowid, distance
                FROM chunks_vec
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (embedding_bytes, _KNN_LIMIT),
            ).fetchall()
        return [(int(r[0]), float(r[1])) for r in rows]
    except Exception:
        logger.warning("KNN search failed", exc_info=True)
        return []


def _rrf_fuse(
    bm25: list[tuple[int, float]],
    knn: list[tuple[int, float]],
    k: int = _RRF_K,
) -> list[tuple[int, float]]:
    """Fuse two ranked lists via Reciprocal Rank Fusion.

    Returns (rowid, score) sorted descending by score (higher = better).
    """
    scores: dict[int, float] = {}
    for rank, (rowid, _) in enumerate(bm25):
        scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (k + rank + 1)
    for rank, (rowid, _) in enumerate(knn):
        scores[rowid] = scores.get(rowid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def search(
    conn: sqlite3.Connection,
    model: TextEmbedding,
    query: str,
    section: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
) -> list[SearchResult]:
    """Hybrid BM25 + KNN search with RRF fusion. section= uses vec0 partition key (not post-fetch)."""
    embedding_bytes = _embed_query(model, query)
    bm25_results = _bm25_search(conn, query, section)
    knn_results = _knn_search(conn, embedding_bytes, section)

    fused = _rrf_fuse(bm25_results, knn_results)

    # Fetch chunk metadata and apply dedup + optional tag filter.
    results: list[SearchResult] = []
    path_counts: dict[str, int] = {}

    for rowid, score in fused:
        if len(results) >= limit:
            break

        row = conn.execute(
            "SELECT path, section, heading_path, chunk_text FROM chunks WHERE id = ?",
            (rowid,),
        ).fetchone()
        if row is None:
            continue

        note_path = row["path"]
        if path_counts.get(note_path, 0) >= _MAX_PER_NOTE:
            continue

        # Optional tag filter (OR-match across chunk_tags).
        if tags:
            tag_row = conn.execute(
                "SELECT 1 FROM chunk_tags WHERE chunk_id = ? AND tag IN ({})".format(",".join("?" for _ in tags)),
                (rowid, *tags),
            ).fetchone()
            if tag_row is None:
                continue

        path_counts[note_path] = path_counts.get(note_path, 0) + 1
        results.append(
            SearchResult(
                path=note_path,
                section=row["section"],
                heading_path=row["heading_path"],
                chunk_text=row["chunk_text"],
                score=round(score, 6),
            )
        )

    return results
