"""Gold-query eval set (Unit 5).

Builds the index from the synthetic fixture corpus and runs 15+ gold queries,
asserting >= 80% hit@3 (expected note appears in top-3 results).

The fixture corpus in tests/fixtures/vault/ is self-contained and does not
require a real Obsidian vault. All tests in this file run anywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"

# ---------------------------------------------------------------------------
# Gold queries
#
# Each entry: (query_text, expected_path_fragment, section_filter | None)
#
# expected_path_fragment is matched as a case-insensitive substring of result.path.
# section_filter=None means search all sections.
# ---------------------------------------------------------------------------


class GoldQuery(NamedTuple):
    query: str
    expected_fragment: str
    section: str | None = None


GOLD_QUERIES: list[GoldQuery] = [
    # Semantic: wording differs from note content
    GoldQuery("how to cook beef at low temperature for many hours", "brisket"),
    GoldQuery("fermented dough with natural wild yeast for artisan loaves", "sourdough"),
    GoldQuery("tomato based sauce with chiles and lime", "salsa"),
    GoldQuery("aromatic spice blend with turmeric ginger and cardamom", "curry"),
    GoldQuery("creamy arborio rice dish cooked with ladle by ladle stock", "risotto"),
    GoldQuery("warming broth made from simmered animal bones", "soup"),
    GoldQuery("Italian noodles cooked al dente with egg and pork fat", "pasta"),
    GoldQuery("packaging applications with linux namespace isolation", "docker"),
    GoldQuery("orchestrating and scheduling distributed containerized workloads", "kubernetes"),
    GoldQuery("tracking file changes branching merging commit history", "git"),
    GoldQuery("structured query language relational tables rows columns", "databases"),
    GoldQuery("IP address resolution and packet routing between networks", "networking"),
    GoldQuery("managing processes file permissions shell scripting", "linux"),
    GoldQuery("dynamic interpreted scripting language with object classes", "python"),
    # Section-filtered query: must return only Cooking section results
    GoldQuery("polymerized oil baked into iron surface for non-stick", "castiron", "Cooking"),
    # Extra queries (bonus coverage)
    GoldQuery("sour tang from lactobacillus bacteria preserving chiles", "salsa"),
    GoldQuery("gluten network stretch fold bulk fermentation overnight", "sourdough"),
    GoldQuery("nearest neighbor vector embedding semantic search", "databases"),
]

_MIN_HIT_AT_3_RATE = 0.80


@pytest.fixture(scope="module")
def eval_index(tmp_path_factory):
    """Build index from fixture vault once for the entire eval module."""
    if not FIXTURE_VAULT.exists():
        pytest.skip("Fixture corpus not present at tests/fixtures/vault/")
    from fastembed import TextEmbedding

    from obsidian_search_tools.db import connect
    from obsidian_search_tools.indexer import build_index

    db_path = tmp_path_factory.mktemp("eval_db") / "vault.db"
    build_index(FIXTURE_VAULT, excluded=set(), db_path=db_path)
    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    conn = connect(db_path)
    yield conn, model
    conn.close()


def _hit_at_k(results: list, expected_fragment: str, k: int = 3) -> bool:
    """Return True if expected_fragment appears in the path of any top-k result."""
    for r in results[:k]:
        if expected_fragment.lower() in r.path.lower():
            return True
    return False


def test_gold_query_eval(eval_index):
    """Run all gold queries and assert >= 80% hit@3."""
    conn, model = eval_index
    from obsidian_search_tools.searcher import search

    hits = 0
    misses: list[str] = []

    for gq in GOLD_QUERIES:
        results = search(conn, model, gq.query, section=gq.section, limit=3)
        if _hit_at_k(results, gq.expected_fragment, k=3):
            hits += 1
        else:
            top_paths = [r.path for r in results[:3]]
            misses.append(f"MISS query={gq.query!r} expected={gq.expected_fragment!r} top3={top_paths}")

    total = len(GOLD_QUERIES)
    rate = hits / total
    miss_summary = "\n".join(misses)
    assert rate >= _MIN_HIT_AT_3_RATE, (
        f"Hit@3 rate {rate:.1%} ({hits}/{total}) below threshold {_MIN_HIT_AT_3_RATE:.0%}.\nMisses:\n{miss_summary}"
    )


def test_section_filter_gold_query(eval_index):
    """AC #2: section-filtered query returns only results from the specified section."""
    conn, model = eval_index
    from obsidian_search_tools.searcher import search

    # Use a query that without the filter would mostly match Tech notes.
    results = search(conn, model, "process management and file permissions", section="Cooking", limit=5)
    for r in results:
        assert r.section == "Cooking", f"Section filter failed: got {r.section!r} in path {r.path!r}"


def test_per_note_dedup_in_eval(eval_index):
    """AC #6: No note should appear more than 2 times in any result set."""
    conn, model = eval_index
    from obsidian_search_tools.searcher import search

    for gq in GOLD_QUERIES[:5]:
        results = search(conn, model, gq.query, section=gq.section, limit=10)
        path_counts: dict[str, int] = {}
        for r in results:
            path_counts[r.path] = path_counts.get(r.path, 0) + 1
        for path, count in path_counts.items():
            assert count <= 2, f"Note {path!r} appeared {count} times for query {gq.query!r}"
