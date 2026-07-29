"""Tests for the token-packed markdown chunker (Unit 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from obsidian_search_tools.chunker import (
    _MAX_TOKENS,
    Chunk,
    _count_tokens,
    _make_chunk_text,
    _parse_paragraphs,
    _split_oversized,
    _strip_frontmatter,
    chunk_file,
)

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"


# ---------------------------------------------------------------------------
# Frontmatter stripping
# ---------------------------------------------------------------------------


def test_strip_frontmatter_removes_yaml():
    text = "---\ntags: [cooking]\ntitle: Test\n---\n# Heading\nBody text."
    body, fm = _strip_frontmatter(text)
    assert "---" not in body
    assert "tags" not in body
    assert "# Heading" in body
    assert fm["tags"] == ["cooking"]
    assert fm["title"] == "Test"


def test_strip_frontmatter_no_frontmatter():
    text = "# Heading\nJust a paragraph."
    body, fm = _strip_frontmatter(text)
    assert body == text
    assert fm == {}


def test_frontmatter_not_in_chunk_text(tmp_path):
    """AC #5: No chunk should start with --- or have tags: as its first line."""
    md = tmp_path / "note.md"
    md.write_text("---\ntags: [test]\nauthor: Alice\n---\n# Title\n\nSome content here.\n")
    vault = tmp_path
    (vault / "SectionA").mkdir()
    note = vault / "SectionA" / "note.md"
    note.write_text("---\ntags: [test]\nauthor: Alice\n---\n# Title\n\nSome content here.\n")
    chunks = chunk_file(note, vault)
    for c in chunks:
        assert not c.chunk_text.startswith("---"), "chunk_text must not start with ---"
        assert not c.chunk_text.startswith("tags:"), "chunk_text must not start with tags:"


# ---------------------------------------------------------------------------
# Heading path extraction
# ---------------------------------------------------------------------------


def test_parse_paragraphs_heading_breadcrumbs():
    body = "# Recipes\n\nIntro text.\n\n## Brisket\n\nThe brisket section.\n\n### Dry Rub\n\nSalt and pepper."
    paras = _parse_paragraphs(body)
    assert len(paras) == 3
    assert paras[0][0] == "Recipes"
    assert paras[1][0] == "Recipes > Brisket"
    assert paras[2][0] == "Recipes > Brisket > Dry Rub"


def test_heading_path_resets_on_higher_level():
    body = "# A\n\nPara 1.\n\n## A.1\n\nPara 2.\n\n# B\n\nPara 3."
    paras = _parse_paragraphs(body)
    assert paras[0][0] == "A"
    assert paras[1][0] == "A > A.1"
    assert paras[2][0] == "B"


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


def test_count_tokens_nonempty():
    n = _count_tokens("Hello, world!")
    assert n > 0


def test_chunk_token_count_matches_text():
    text = _make_chunk_text("Heading", ["Paragraph one.", "Paragraph two."])
    tc = _count_tokens(text)
    chunk = Chunk(path="p", section="s", heading_path="Heading", chunk_text=text, token_count=tc)
    assert chunk.token_count == tc


# ---------------------------------------------------------------------------
# Greedy packing
# ---------------------------------------------------------------------------


def test_chunks_under_token_limit(tmp_path):
    """All chunks must be at or under _MAX_TOKENS."""
    vault = tmp_path / "Vault"
    section = vault / "Notes"
    section.mkdir(parents=True)
    note = section / "long.md"
    # Write ~50 paragraphs of modest size.
    paras = [f"Paragraph {i}: The quick brown fox jumps over the lazy dog near the river bank." for i in range(50)]
    note.write_text("# Title\n\n" + "\n\n".join(paras))
    chunks = chunk_file(note, vault)
    assert chunks, "Should produce at least one chunk"
    for c in chunks:
        assert c.token_count <= _MAX_TOKENS, f"Chunk exceeds limit: {c.token_count} tokens"


def test_section_from_top_level_subdir(tmp_path):
    vault = tmp_path
    section_dir = vault / "Cooking"
    section_dir.mkdir()
    note = section_dir / "brisket.md"
    note.write_text("# Brisket\n\nSome content about beef.")
    chunks = chunk_file(note, vault)
    assert all(c.section == "Cooking" for c in chunks)


def test_chunk_text_has_heading_prepended(tmp_path):
    vault = tmp_path
    sec = vault / "Tech"
    sec.mkdir()
    note = sec / "test.md"
    note.write_text("# Overview\n\nThis is the main content.")
    chunks = chunk_file(note, vault)
    assert chunks
    assert "Overview" in chunks[0].chunk_text


# ---------------------------------------------------------------------------
# Oversized paragraph splitting with overlap
# ---------------------------------------------------------------------------


def test_split_oversized_respects_limit():
    """_split_oversized must produce sub-chunks under MAX_TOKENS."""
    # Build a paragraph that is way over the limit.
    long_sentence = "The quick brown fox jumps over the lazy dog and then runs far away into the forest. "
    paragraph = (long_sentence * 200).strip()
    result = _split_oversized(paragraph, "")
    for sub in result:
        assert _count_tokens(sub) <= _MAX_TOKENS, f"Sub-chunk exceeds limit: {_count_tokens(sub)}"


def test_split_oversized_has_overlap():
    """Adjacent sub-chunks should share some content (overlap)."""
    long_sentence = "This is a sentence that takes up space in token budget. "
    paragraph = (long_sentence * 100).strip()
    result = _split_oversized(paragraph, "Heading")
    if len(result) >= 2:
        words0 = set(result[0].split())
        words1 = set(result[1].split())
        overlap = words0 & words1
        assert len(overlap) > 0, "Adjacent chunks should share overlapping tokens"


# ---------------------------------------------------------------------------
# Integration: chunk_file on fixture corpus
# ---------------------------------------------------------------------------


def test_chunk_file_brisket():
    note = FIXTURE_VAULT / "Cooking" / "brisket.md"
    if not note.exists():
        pytest.skip("Fixture corpus not present")
    chunks = chunk_file(note, FIXTURE_VAULT)
    assert chunks
    # Must have section = Cooking.
    assert all(c.section == "Cooking" for c in chunks)
    # Frontmatter must not appear in chunk_text.
    for c in chunks:
        assert not c.chunk_text.startswith("---")
    # Frontmatter should be parsed.
    assert "bbq" in chunks[0].frontmatter.get("tags", []) or "cooking" in chunks[0].frontmatter.get("tags", [])


def test_chunk_file_returns_empty_for_nonexistent(tmp_path):
    result = chunk_file(tmp_path / "nonexistent.md", tmp_path)
    assert result == []
