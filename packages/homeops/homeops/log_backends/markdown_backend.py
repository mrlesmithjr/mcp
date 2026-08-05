"""Markdown backend for the homeops home_log (issue #58).

CAVEAT -- row identity is position-based, not a stable id:
Markdown tables have no id column, so a row's "id" is its 1-indexed
position within the entity's section, RECOMPUTED FROM SCRATCH on every
read and every write, never persisted anywhere. update_row/delete_row by
id therefore only target the right row if the file's row order has not
changed since the id was obtained (e.g. from a prior *_list call) -- a
hand-edit, a concurrent write, or even a prior delete in the same section
can retarget a later update/delete at the wrong row. This is an accepted
simplicity trade-off for a file-backed, no-ORM store, not an oversight;
see the NOTE comments on update_row/delete_row below for where it applies.

Fixed-column markdown tables, one `## Heading` section per entity, in a
configured file (or per-entity files via `home_log.markdown.entities`).
Parsing is deterministic and header-driven: the header row is matched
against the entity's canonical column set/order (homeops.log_schema), and
any row with the wrong column count raises rather than being silently
skipped or reordered.

A cell value containing "|" is escaped to "\\|" on write and unescaped on
read (see _escape_cell/_unescape_cell), so it cannot be mistaken for an
extra column boundary -- an unescaped "|" would otherwise corrupt that
row's column count and raise "Malformed row" on every subsequent read of
the entity until hand-fixed. A newline in a cell value is intentionally
collapsed to a space on write, not escaped: this table format has no way
to represent a multi-line cell (one row is one physical line). Cell values
are also stripped of leading/trailing whitespace on read (the header/pad
spaces of a markdown table cannot be told apart from intentional padding in
the value), so whitespace-only fidelity at a value's edges is not preserved.

Every mutation is read-whole-file, modify-one-section, atomic_write the
whole file back (homeops.atomic_io), so a crash mid-write can never leave a
torn file and other entities' sections in the same file are left untouched.
Writing also requires the note's parent directory to already exist (see
_write_rows): atomic_write's own `mkdir(parents=True, exist_ok=True)` would
otherwise silently create a missing/unmounted vault path instead of failing
loud, which home_log.backend: markdown must never do.

Ported from lawnops' log_backends/markdown_backend.py (issue #57), same
contract, adapted to homeops' entity set and `home_log` config key.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

from homeops.atomic_io import atomic_write
from homeops.log_schema import ENTITIES, EntitySchema

_DEFAULT_DATE_FORMAT = "%Y-%m-%d"
_ROW_RE = re.compile(r"^\|(.*)\|\s*$")
# Splits a row's inner text on "|" characters that are NOT escaped with a
# leading backslash, so a cell value containing a literal "|" (escaped as
# "\|" on write, see _escape_cell) does not get mistaken for a column
# separator on read.
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")


def _escape_cell(value: str) -> str:
    """Make a display string safe to embed as one table cell.

    A literal "|" would otherwise be read back as an extra column boundary,
    corrupting that row's column count on the next read (and raising
    "Malformed row" for every subsequent read of the entity, wedging all read
    tools until hand-fixed). A newline would split one logical row across
    multiple physical lines, which this table format has no way to represent
    at all, so it is intentionally collapsed to a space rather than escaped.
    """
    value = re.sub(r"\r\n|\r|\n", " ", value)
    return value.replace("|", "\\|")


def _unescape_cell(value: str) -> str:
    return value.replace("\\|", "|")


def _split_row(line: str) -> list[str] | None:
    m = _ROW_RE.match(line.strip())
    if not m:
        return None
    return [_unescape_cell(c.strip()) for c in _UNESCAPED_PIPE_RE.split(m.group(1))]


_HEADING_RE = re.compile(r"^##\s+(.*?)\s*$", re.MULTILINE)


def _split_into_blocks(content: str) -> tuple[str, list[tuple[str, str]]]:
    """Split markdown content into (preamble, [(heading_text, block_text), ...]).

    `block_text` includes the '## Heading' line and everything up to (not
    including) the next top-level '## ' heading, with trailing blank lines
    stripped. Splitting/rejoining through this single helper (used by both
    the reader and the writer below) is what keeps blank-line spacing
    between sections from drifting across repeated writes -- every write
    fully reconstructs the file from blocks rather than regex-substituting
    a section in place, so formatting can never accumulate stray or missing
    blank lines from a previous write.
    """
    matches = list(_HEADING_RE.finditer(content))
    if not matches:
        return content, []
    preamble = content[: matches[0].start()]
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        blocks.append((m.group(1), content[start:end].rstrip("\n")))
    return preamble, blocks


def _extract_section_body(content: str, heading: str) -> str | None:
    _, blocks = _split_into_blocks(content)
    for h, text in blocks:
        if h == heading:
            # Body is everything after the "## Heading" line itself.
            return text.split("\n", 1)[1] if "\n" in text else ""
    return None


def _parse_section_raw(content: str, heading: str, spec: EntitySchema) -> list[dict]:
    """Column-addressed parse of one entity's section. Returns raw string cells (or None)."""
    body = _extract_section_body(content, heading)
    if body is None:
        return []
    lines = [ln for ln in body.splitlines() if ln.strip()]
    if not lines:
        return []

    header_cols = _split_row(lines[0])
    if header_cols is None:
        raise RuntimeError(f"Malformed table header under '## {heading}' in home_log markdown file")
    if tuple(header_cols) != spec.columns:
        raise RuntimeError(
            f"Column mismatch under '## {heading}': expected {list(spec.columns)}, found {header_cols}. "
            "homeops owns this table's column set/order when backend: markdown -- fix the header, do not reorder it."
        )

    # lines[1] is the '---|---|...' separator row.
    data_lines = lines[2:]
    rows = []
    for line in data_lines:
        cols = _split_row(line)
        if cols is None or len(cols) != len(spec.columns):
            raise RuntimeError(f"Malformed row under '## {heading}': {line!r}")
        rows.append({col: (val if val != "" else None) for col, val in zip(spec.columns, cols)})
    return rows


def _parse_display_date(raw: str, date_format: str) -> str:
    try:
        return datetime.strptime(raw, date_format).strftime(_DEFAULT_DATE_FORMAT)
    except ValueError as e:
        raise RuntimeError(
            f"Date {raw!r} does not match configured home_log.markdown.date_format {date_format!r}"
        ) from e


def _format_display_date(iso_value: str, date_format: str) -> str:
    try:
        return datetime.strptime(iso_value, _DEFAULT_DATE_FORMAT).strftime(date_format)
    except ValueError:
        return iso_value


def _format_number(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _to_typed(spec: EntitySchema, raw_row: dict, date_format: str) -> dict:
    typed = {}
    for col in spec.columns:
        raw = raw_row.get(col)
        if raw in (None, ""):
            typed[col] = None
        elif col in spec.numeric_columns:
            try:
                typed[col] = float(raw)
            except ValueError as e:
                raise RuntimeError(f"Non-numeric value {raw!r} in home_log column {col!r}") from e
        elif col in spec.date_columns:
            typed[col] = _parse_display_date(raw, date_format)
        else:
            typed[col] = raw
    return typed


def _to_display(spec: EntitySchema, typed_row: dict, date_format: str) -> dict:
    display = {}
    for col in spec.columns:
        val = typed_row.get(col)
        if val is None:
            display[col] = ""
        elif col in spec.date_columns:
            display[col] = _format_display_date(val, date_format)
        elif col in spec.numeric_columns:
            display[col] = _format_number(val)
        else:
            display[col] = str(val)
    return display


def _render_section(heading: str, spec: EntitySchema, typed_rows: list[dict], date_format: str) -> str:
    lines = [
        f"## {heading}",
        "| " + " | ".join(spec.columns) + " |",
        "| " + " | ".join(["---"] * len(spec.columns)) + " |",
    ]
    for row in typed_rows:
        display = _to_display(spec, row, date_format)
        lines.append("| " + " | ".join(_escape_cell(display[c]) for c in spec.columns) + " |")
    return "\n".join(lines) + "\n"


def _replace_section(content: str, heading: str, new_section_text: str) -> str:
    """Rebuild the whole file from blocks with `heading`'s block replaced (or
    appended, if not already present). Other entities' sections, and any
    preamble text before the first heading, are carried through unchanged.
    Always reconstructing from blocks -- rather than substituting a section
    in place with a regex -- is what keeps blank-line spacing between
    sections from drifting across repeated writes."""
    preamble, blocks = _split_into_blocks(content)
    new_block_text = new_section_text.rstrip("\n")
    replaced = False
    out_blocks = []
    for h, text in blocks:
        if h == heading:
            out_blocks.append(new_block_text)
            replaced = True
        else:
            out_blocks.append(text)
    if not replaced:
        out_blocks.append(new_block_text)
    body = "\n\n".join(out_blocks) + "\n"
    if preamble and preamble.strip():
        return preamble.rstrip("\n") + "\n\n" + body
    return body


class MarkdownBackend:
    def __init__(self, config: dict):
        self.config = config
        self._md_config = (config.get("home_log") or {}).get("markdown") or {}

    def _resolve_path(self, entity: str) -> Path:
        entities_cfg = self._md_config.get("entities") or {}
        raw_path = entities_cfg.get(entity) or self._md_config.get("note")
        if not raw_path:
            raise RuntimeError(
                f"home_log.backend is 'markdown' but no file path is configured for entity {entity!r}. "
                f"Set home_log.markdown.note or home_log.markdown.entities.{entity} in config."
            )
        return Path(os.path.expanduser(raw_path))

    def _heading(self, entity: str) -> str:
        headings_cfg = self._md_config.get("section_headings") or {}
        return headings_cfg.get(entity) or ENTITIES[entity].heading

    def _date_format(self) -> str:
        return self._md_config.get("date_format") or _DEFAULT_DATE_FORMAT

    def _read_rows(self, path: Path, entity: str, spec: EntitySchema, heading: str) -> tuple[str, list[dict]]:
        content = path.read_text() if path.exists() else ""
        raw_rows = _parse_section_raw(content, heading, spec)
        typed_rows = [_to_typed(spec, r, self._date_format()) for r in raw_rows]
        return content, typed_rows

    def _write_rows(self, path: Path, content: str, heading: str, spec: EntitySchema, typed_rows: list[dict]) -> None:
        # atomic_io.atomic_write does `parent.mkdir(parents=True, exist_ok=True)`,
        # so without this check a note pointing at a missing/unmounted vault
        # would silently create the whole directory tree and write there
        # instead of failing loud. This still lets a not-yet-existent note
        # FILE be created inside an existing parent directory -- only a
        # missing/non-directory parent is rejected.
        if not path.parent.is_dir():
            raise RuntimeError(
                f"home_log markdown parent directory does not exist: {path.parent}. "
                "Refusing to auto-create it -- create the directory (or vault) first, "
                "or fix home_log.markdown.note / home_log.markdown.entities in config."
            )
        new_section = _render_section(heading, spec, typed_rows, self._date_format())
        new_content = _replace_section(content, heading, new_section)
        try:
            atomic_write(path, lambda f: f.write(new_content))
        except OSError as e:
            raise RuntimeError(f"Failed writing home_log markdown file {path}: {e}") from e

    def _matches(self, row: dict, position: int, match: dict):
        if "id" in match:
            return position == match["id"]
        if "name_contains" in match:
            needle = str(match["name_contains"]).lower()
            return needle in str(row.get("name") or "").lower()
        raise RuntimeError(f"Unsupported match criteria for markdown backend: {match!r}")

    def read_table(self, entity: str) -> list[dict]:
        spec = ENTITIES[entity]
        path = self._resolve_path(entity)
        heading = self._heading(entity)
        _, typed_rows = self._read_rows(path, entity, spec, heading)
        out = []
        for i, row in enumerate(typed_rows, start=1):
            d = dict(row)
            d["id"] = i
            # sqlite-only metadata (created_at, costs.source_id, task_id) is
            # never stored in the markdown table -- surfaced as None here so
            # the KEY SET returned by read_table matches SqliteBackend's
            # (see EntitySchema.passthrough's docstring).
            for col in spec.passthrough:
                d[col] = None
            out.append(d)
        return out

    def append_row(self, entity: str, row: dict) -> dict:
        spec = ENTITIES[entity]
        path = self._resolve_path(entity)
        heading = self._heading(entity)
        content, typed_rows = self._read_rows(path, entity, spec, heading)
        new_row = {col: row.get(col) for col in spec.columns}
        typed_rows.append(new_row)
        self._write_rows(path, content, heading, spec, typed_rows)
        return new_row

    def update_row(self, entity: str, match: dict, changes: dict) -> int:
        # NOTE: when `match` is {"id": N}, N is a position recomputed fresh
        # on this read, not a stable identifier -- see the module docstring
        # CAVEAT. A row inserted/removed earlier in the section since the id
        # was obtained shifts every later id, so this can update the wrong
        # row. {"name_contains": ...} is unaffected (it matches by value).
        spec = ENTITIES[entity]
        path = self._resolve_path(entity)
        heading = self._heading(entity)
        content, typed_rows = self._read_rows(path, entity, spec, heading)
        updated = 0
        for i, row in enumerate(typed_rows, start=1):
            if self._matches(row, i, match):
                for k, v in changes.items():
                    if k in spec.columns:
                        row[k] = v
                updated += 1
        if updated:
            self._write_rows(path, content, heading, spec, typed_rows)
        return updated

    def replace_table(self, entity: str, rows: list[dict]) -> int:
        """Overwrite the entire entity section with `rows`. Used by the
        `homeops db log-export` migration so re-running it is idempotent
        (append_row would duplicate every row on a second run)."""
        spec = ENTITIES[entity]
        path = self._resolve_path(entity)
        heading = self._heading(entity)
        content = path.read_text() if path.exists() else ""
        typed_rows = [{col: row.get(col) for col in spec.columns} for row in rows]
        self._write_rows(path, content, heading, spec, typed_rows)
        return len(typed_rows)

    def delete_row(self, entity: str, match: dict) -> int:
        # NOTE: same position-based id caveat as update_row above -- see the
        # module docstring CAVEAT before relying on an id obtained from an
        # earlier read if the section may have changed since.
        spec = ENTITIES[entity]
        path = self._resolve_path(entity)
        heading = self._heading(entity)
        content, typed_rows = self._read_rows(path, entity, spec, heading)
        kept = []
        deleted = 0
        for i, row in enumerate(typed_rows, start=1):
            if self._matches(row, i, match):
                deleted += 1
            else:
                kept.append(row)
        if deleted:
            self._write_rows(path, content, heading, spec, kept)
        return deleted
