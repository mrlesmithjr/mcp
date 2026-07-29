"""Token-packed markdown chunker for obsidian-search-tools.

Splits a markdown file into overlapping chunks suitable for embedding and search.
Heading breadcrumbs are prepended to each chunk. YAML frontmatter is stripped
from chunk text but parsed into the `frontmatter` dict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")
_MAX_TOKENS = 512
_OVERLAP_TOKENS = 64

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    path: str
    section: str
    heading_path: str
    chunk_text: str
    frontmatter: dict = field(default_factory=dict)
    token_count: int = 0


def _count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def _strip_frontmatter(text: str) -> tuple[str, dict]:
    """Strip YAML frontmatter and return (body, parsed_dict).

    Parses only simple key: value pairs and key: [list] inline arrays.
    pyyaml is not used to avoid the dependency.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text, {}
    fm_block = m.group(1)
    body = text[m.end() :]
    parsed: dict = {}
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            items = [v.strip().strip('"').strip("'") for v in inner.split(",")]
            parsed[key] = [i for i in items if i]
        else:
            parsed[key] = val.strip('"').strip("'")
    return body, parsed


def _parse_paragraphs(body: str) -> list[tuple[str, str]]:
    """Return list of (heading_path, paragraph_text) pairs from a markdown body."""
    lines = body.splitlines()
    heading_stack: list[tuple[int, str]] = []
    paragraphs: list[tuple[str, str]] = []
    current_lines: list[str] = []

    def _heading_path() -> str:
        return " > ".join(title for _, title in heading_stack)

    def _flush() -> None:
        para = "\n".join(current_lines).strip()
        if para:
            paragraphs.append((_heading_path(), para))
        current_lines.clear()

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            _flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            heading_stack[:] = [(lvl, txt) for lvl, txt in heading_stack if lvl < level]
            heading_stack.append((level, title))
        elif line.strip() == "":
            _flush()
        else:
            current_lines.append(line)

    _flush()
    return paragraphs


def _make_chunk_text(heading_path: str, paragraphs: list[str]) -> str:
    body = "\n\n".join(paragraphs)
    if heading_path:
        return heading_path + "\n" + body
    return body


def _split_oversized(paragraph: str, heading_path: str) -> list[str]:
    """Split a paragraph that exceeds MAX_TOKENS at sentence boundaries.

    Produces multiple chunks with OVERLAP_TOKENS of context carried forward.
    Each chunk has heading_path prepended.
    """
    prefix = heading_path + "\n" if heading_path else ""
    prefix_tokens = _count_tokens(prefix)
    budget = _MAX_TOKENS - prefix_tokens
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(paragraph.strip()) if s.strip()]
    if not sentences:
        return [prefix + paragraph]

    result: list[str] = []
    window: list[str] = []
    window_tokens = 0

    for sent in sentences:
        sent_tok = _count_tokens(sent + " ")
        if window_tokens + sent_tok > budget and window:
            result.append(prefix + " ".join(window))
            # Build overlap tail.
            overlap: list[str] = []
            overlap_tok = 0
            for s in reversed(window):
                t = _count_tokens(s + " ")
                if overlap_tok + t > _OVERLAP_TOKENS:
                    break
                overlap.insert(0, s)
                overlap_tok += t
            window = overlap
            window_tokens = overlap_tok
        window.append(sent)
        window_tokens += sent_tok

    if window:
        result.append(prefix + " ".join(window))

    return result if result else [prefix + paragraph]


def chunk_file(path: Path, vault_root: Path) -> list[Chunk]:
    """Parse a markdown file into Chunk objects. Returns empty list for unreadable or empty files."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    body, frontmatter = _strip_frontmatter(text)

    try:
        rel = path.relative_to(vault_root)
    except ValueError:
        return []

    rel_path = str(rel)
    parts = rel.parts
    section = parts[0] if len(parts) > 1 else ""

    paragraphs = _parse_paragraphs(body)
    if not paragraphs:
        return []

    chunks: list[Chunk] = []
    current_heading = paragraphs[0][0]
    current_paras: list[str] = []

    def _emit() -> None:
        if not current_paras:
            return
        text_ = _make_chunk_text(current_heading, current_paras)
        tc = _count_tokens(text_)
        chunks.append(
            Chunk(
                path=rel_path,
                section=section,
                heading_path=current_heading,
                chunk_text=text_,
                frontmatter=frontmatter,
                token_count=tc,
            )
        )

    for heading, para in paragraphs:
        prefix = heading + "\n" if heading else ""
        para_tokens = _count_tokens(para)
        prefix_tokens = _count_tokens(prefix) if not current_paras else 0

        # Paragraph itself exceeds budget: flush, then split.
        if prefix_tokens + para_tokens > _MAX_TOKENS:
            _emit()
            current_paras = []
            current_heading = heading
            for sub_text in _split_oversized(para, heading):
                tc = _count_tokens(sub_text)
                chunks.append(
                    Chunk(
                        path=rel_path,
                        section=section,
                        heading_path=heading,
                        chunk_text=sub_text,
                        frontmatter=frontmatter,
                        token_count=tc,
                    )
                )
            continue

        # Check if adding this paragraph would exceed the budget.
        proposed_text = _make_chunk_text(current_heading, current_paras + [para])
        if current_paras and _count_tokens(proposed_text) > _MAX_TOKENS:
            _emit()
            current_paras = [para]
            current_heading = heading
        else:
            if not current_paras:
                current_heading = heading
            current_paras.append(para)

    _emit()
    return chunks
