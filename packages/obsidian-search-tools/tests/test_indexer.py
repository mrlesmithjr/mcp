"""Tests for the index builder (Unit 2)."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"


@pytest.fixture(scope="module")
def built_index(tmp_path_factory):
    """Build index from fixture vault once for the module."""
    if not FIXTURE_VAULT.exists():
        pytest.skip("Fixture corpus not present")
    from obsidian_search_tools.indexer import build_index

    db_path = tmp_path_factory.mktemp("db") / "vault.db"
    report = build_index(FIXTURE_VAULT, excluded=set(), db_path=db_path)
    return report, db_path


def test_build_index_notes_indexed(built_index):
    report, _ = built_index
    assert report.notes_indexed >= 10, "Expected at least 10 notes indexed"


def test_build_index_chunks_indexed(built_index):
    report, _ = built_index
    assert report.chunks_indexed > 0


def test_build_index_sections(built_index):
    report, _ = built_index
    assert "Cooking" in report.sections
    assert "Tech" in report.sections


def test_build_index_model_id(built_index):
    report, _ = built_index
    assert report.model_id == "BAAI/bge-small-en-v1.5"


def test_build_index_elapsed_positive(built_index):
    report, _ = built_index
    assert report.elapsed_seconds > 0


def test_index_meta_stored(built_index):
    """index_meta must contain last_reindex, model_id, and indexed_sections."""
    _, db_path = built_index
    from obsidian_search_tools.db import connect

    conn = connect(db_path)
    try:
        rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM index_meta").fetchall()}
    finally:
        conn.close()
    assert "last_reindex" in rows
    assert "model_id" in rows
    assert rows["model_id"] == "BAAI/bge-small-en-v1.5"
    assert "indexed_sections" in rows


def test_chunks_have_sections(built_index):
    """All indexed chunks must have a non-empty section."""
    _, db_path = built_index
    from obsidian_search_tools.db import connect

    conn = connect(db_path)
    try:
        empty_section = conn.execute("SELECT COUNT(*) FROM chunks WHERE section = ''").fetchone()[0]
    finally:
        conn.close()
    assert empty_section == 0, "All chunks must have a non-empty section"


def test_no_frontmatter_in_chunk_text(built_index):
    """AC #5: No chunk_text should start with --- or tags:."""
    _, db_path = built_index
    from obsidian_search_tools.db import connect

    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT chunk_text FROM chunks").fetchall()
    finally:
        conn.close()
    for row in rows:
        text = row[0]
        assert not text.startswith("---"), f"Chunk starts with ---: {text[:50]!r}"
        assert not text.startswith("tags:"), f"Chunk starts with tags:: {text[:50]!r}"


def test_vec0_has_embeddings(built_index):
    """chunks_vec must contain one embedding per chunk."""
    _, db_path = built_index
    from obsidian_search_tools.db import connect

    conn = connect(db_path)
    try:
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        vec_count = conn.execute("SELECT COUNT(*) FROM chunks_vec").fetchone()[0]
    finally:
        conn.close()
    assert vec_count == chunk_count, "Vec count must match chunk count"


def test_excluded_sections(tmp_path):
    """Sections in excluded set must not appear in the index."""
    if not FIXTURE_VAULT.exists():
        pytest.skip("Fixture corpus not present")
    from obsidian_search_tools.db import connect
    from obsidian_search_tools.indexer import build_index

    db_path = tmp_path / "vault_excl.db"
    build_index(FIXTURE_VAULT, excluded={"Tech"}, db_path=db_path)
    conn = connect(db_path)
    try:
        tech_chunks = conn.execute("SELECT COUNT(*) FROM chunks WHERE section = 'Tech'").fetchone()[0]
    finally:
        conn.close()
    assert tech_chunks == 0, "Excluded section 'Tech' must not appear in index"


def test_indexed_files_populated_after_force_build(tmp_path):
    """After a force=True build, indexed_files row count must equal notes_indexed."""
    if not FIXTURE_VAULT.exists():
        pytest.skip("Fixture corpus not present")
    from obsidian_search_tools.db import connect
    from obsidian_search_tools.indexer import build_index

    db_path = tmp_path / "vault.db"
    report = build_index(FIXTURE_VAULT, excluded=set(), db_path=db_path, force=True)

    conn = connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0]
    finally:
        conn.close()

    assert count == report.notes_indexed


def test_incremental_unchanged_skips_reindex(tmp_path):
    """A force=False run with no file changes must report notes_indexed=0 and notes_unchanged>0."""
    if not FIXTURE_VAULT.exists():
        pytest.skip("Fixture corpus not present")
    from obsidian_search_tools.db import connect
    from obsidian_search_tools.indexer import build_index

    db_path = tmp_path / "vault.db"
    # Initial full build.
    build_index(FIXTURE_VAULT, excluded=set(), db_path=db_path, force=True)

    # Capture chunk count before incremental run.
    conn = connect(db_path)
    try:
        chunks_before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()

    # Incremental run with no file changes.
    report = build_index(FIXTURE_VAULT, excluded=set(), db_path=db_path, force=False)

    conn = connect(db_path)
    try:
        chunks_after = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        conn.close()

    assert report.notes_indexed == 0, "No files changed; nothing should be re-indexed"
    assert report.notes_unchanged > 0, "All files should be reported as unchanged"
    assert chunks_after == chunks_before, "Chunk count must not change after a no-op incremental run"


def test_incremental_detects_deleted_file(tmp_path):
    """Deleting a file and running force=False must report notes_deleted=1 and remove its chunks."""
    if not FIXTURE_VAULT.exists():
        pytest.skip("Fixture corpus not present")
    from obsidian_search_tools.db import connect
    from obsidian_search_tools.indexer import build_index

    # Copy fixture vault to a temp dir so we can mutate it.
    vault_copy = tmp_path / "vault"
    shutil.copytree(FIXTURE_VAULT, vault_copy)

    db_path = tmp_path / "vault.db"
    build_index(vault_copy, excluded=set(), db_path=db_path, force=True)

    # Delete one file.
    target = vault_copy / "Cooking" / "curry.md"
    deleted_name = target.name
    target.unlink()

    report = build_index(vault_copy, excluded=set(), db_path=db_path, force=False)

    conn = connect(db_path)
    try:
        remaining = conn.execute("SELECT COUNT(*) FROM chunks WHERE path LIKE ?", (f"%{deleted_name}%",)).fetchone()[0]
    finally:
        conn.close()

    assert report.notes_deleted == 1, "One deleted file must be reported"
    assert remaining == 0, "Chunks for the deleted file must be removed from the index"


def test_incremental_detects_changed_file(tmp_path):
    """Bumping a file's mtime and running force=False must report notes_indexed=1."""
    if not FIXTURE_VAULT.exists():
        pytest.skip("Fixture corpus not present")
    from obsidian_search_tools.indexer import build_index

    # Copy fixture vault to a temp dir so we can mutate it.
    vault_copy = tmp_path / "vault"
    shutil.copytree(FIXTURE_VAULT, vault_copy)

    db_path = tmp_path / "vault.db"
    first_report = build_index(vault_copy, excluded=set(), db_path=db_path, force=True)
    original_count = first_report.notes_indexed

    # Bump mtime of one file into the future.
    target = vault_copy / "Cooking" / "pasta.md"
    new_mtime = time.time() + 1
    os.utime(target, (new_mtime, new_mtime))

    report = build_index(vault_copy, excluded=set(), db_path=db_path, force=False)

    assert report.notes_indexed == 1, "Exactly one changed file must be re-indexed"
    assert report.notes_unchanged == original_count - 1, "Remaining files must be unchanged"
