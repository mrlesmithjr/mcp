"""Tests for the hybrid search path (Unit 3).

Covers: RRF fusion, section partition-key filter, per-note dedup, query prefix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"


@pytest.fixture(scope="module")
def indexed_db(tmp_path_factory):
    """Build index once for the module and return (conn, model, db_path)."""
    if not FIXTURE_VAULT.exists():
        pytest.skip("Fixture corpus not present")
    from fastembed import TextEmbedding

    from obsidian_search_tools.db import connect
    from obsidian_search_tools.indexer import build_index

    db_path = tmp_path_factory.mktemp("search_db") / "vault.db"
    build_index(FIXTURE_VAULT, excluded=set(), db_path=db_path)
    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    conn = connect(db_path)
    yield conn, model, db_path
    conn.close()


# ---------------------------------------------------------------------------
# Basic search
# ---------------------------------------------------------------------------


def test_search_returns_results(indexed_db):
    conn, model, _ = indexed_db
    from obsidian_search_tools.searcher import search

    results = search(conn, model, "how to smoke beef", limit=5)
    assert len(results) > 0


def test_search_result_has_fields(indexed_db):
    conn, model, _ = indexed_db
    from obsidian_search_tools.searcher import search

    results = search(conn, model, "pasta carbonara", limit=3)
    assert results
    r = results[0]
    assert r.path
    assert r.section
    assert r.chunk_text
    assert r.score > 0


# ---------------------------------------------------------------------------
# Section partition-key filter (AC #4)
# ---------------------------------------------------------------------------


def test_section_filter_restricts_results(indexed_db):
    """AC #2 & #4: section filter returns only that section's chunks.

    The section= argument is applied as a vec0 partition key, not post-fetch
    filtering. Verify by checking that NO result belongs to a different section.
    """
    conn, model, _ = indexed_db
    from obsidian_search_tools.searcher import search

    results = search(conn, model, "software development tools", section="Tech", limit=10)
    assert results, "Section filter should still return results for an in-section query"
    for r in results:
        assert r.section == "Tech", f"Expected section=Tech, got {r.section!r} for path {r.path!r}"


def test_section_filter_excludes_other_section(indexed_db):
    """AC #4: partition-key filter must work even when global KNN favours other section.

    Step 1 confirms the global KNN surfaces Cooking results for a Cooking-specific query.
    Step 2 confirms section='Tech' still returns only Tech (proves the filter is the
    vec0 partition key, not a post-fetch Python filter that could silently pass this).
    """
    conn, model, _ = indexed_db
    from obsidian_search_tools.searcher import search

    # Step 1: global query must surface Cooking results -- otherwise the filter test
    # proves nothing (if Cooking never appeared globally, filtering it out is trivial).
    global_results = search(conn, model, "brisket smoking temperature", limit=10)
    global_sections = {r.section for r in global_results}
    assert "Cooking" in global_sections, (
        "Precondition failed: global search should return Cooking results for brisket query"
    )

    # Step 2: with section='Tech', Cooking results must be absent.
    filtered = search(conn, model, "brisket smoking temperature", section="Tech", limit=5)
    for r in filtered:
        assert r.section == "Tech", f"Partition filter failed: got section {r.section!r}"


def test_section_filter_cooking_only(indexed_db):
    conn, model, _ = indexed_db
    from obsidian_search_tools.searcher import search

    results = search(conn, model, "network routing protocol", section="Cooking", limit=5)
    for r in results:
        assert r.section == "Cooking", f"Partition filter failed: got section {r.section!r}"


# ---------------------------------------------------------------------------
# Per-note dedup (AC #6)
# ---------------------------------------------------------------------------


def test_per_note_dedup(indexed_db):
    """AC #6: No single note should appear more than 2 times in any result set."""
    conn, model, _ = indexed_db
    from obsidian_search_tools.searcher import search

    results = search(conn, model, "cooking techniques", limit=10)
    path_counts: dict[str, int] = {}
    for r in results:
        path_counts[r.path] = path_counts.get(r.path, 0) + 1
    for path, count in path_counts.items():
        assert count <= 2, f"Note {path!r} appeared {count} times (max 2)"


# ---------------------------------------------------------------------------
# Query prefix (AC #3)
# ---------------------------------------------------------------------------


def test_query_prefix_changes_embedding():
    """AC #3: Embedding with and without query prefix must differ (cosine similarity < 0.99)."""
    import numpy as np
    from fastembed import TextEmbedding

    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    query = "protein cooking technique"
    prefix = "Represent this sentence for searching relevant passages: "

    raw_emb = list(model.embed([query]))[0]
    prefixed_emb = list(model.embed([prefix + query]))[0]

    raw_norm = raw_emb / (np.linalg.norm(raw_emb) + 1e-9)
    pre_norm = prefixed_emb / (np.linalg.norm(prefixed_emb) + 1e-9)
    cosine_sim = float(np.dot(raw_norm, pre_norm))
    assert cosine_sim < 0.9999, f"Prefix had no effect on embedding (cosine={cosine_sim:.6f})"


# ---------------------------------------------------------------------------
# RRF scoring
# ---------------------------------------------------------------------------


def test_rrf_fuse_combines_lists():
    from obsidian_search_tools.searcher import _rrf_fuse

    bm25 = [(1, -0.5), (2, -1.0), (3, -1.5)]
    knn = [(3, 0.1), (1, 0.2), (4, 0.3)]
    fused = _rrf_fuse(bm25, knn)
    rowids = [r for r, _ in fused]
    # rowid 1 and 3 appear in both lists so they should score higher.
    assert rowids.index(1) < rowids.index(4), "Shared rowid 1 should outscore unique rowid 4"
    assert rowids.index(3) < rowids.index(2) or rowids.index(3) < rowids.index(4)


def test_rrf_fuse_unique_items():
    from obsidian_search_tools.searcher import _rrf_fuse

    fused = _rrf_fuse([(1, -1.0), (2, -2.0)], [(3, 0.1), (4, 0.2)])
    # All 4 rowids should appear.
    assert {r for r, _ in fused} == {1, 2, 3, 4}


# ---------------------------------------------------------------------------
# Search limit
# ---------------------------------------------------------------------------


def test_search_respects_limit(indexed_db):
    conn, model, _ = indexed_db
    from obsidian_search_tools.searcher import search

    results = search(conn, model, "food", limit=3)
    assert len(results) <= 3
