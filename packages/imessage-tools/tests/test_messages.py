"""Tests for MessagesManager.search_messages allowlist/limit ordering.

Regression coverage for issue #69: the SQL query used to slice to a fixed
`limit * 3` candidate window before the Python-side allowlist filter ran,
which could silently under-return or zero-return results when the allowlist
is small relative to total handles in chat.db.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from imessage_tools import messages as messages_mod
from imessage_tools.messages import _NS_FACTOR, MessagesManager


class _FakeContactResolver:
    """Avoids real Contacts/AppleScript lookups during tests."""

    def resolve_or_handle(self, handle: str) -> str:
        return handle

    def search_by_name(self, name: str) -> list[dict]:
        return []


def _apple_ns_for_offset(seconds_from_epoch: int) -> int:
    """Build an Apple-epoch nanosecond timestamp `seconds_from_epoch` after 2001-01-01."""
    return seconds_from_epoch * _NS_FACTOR


@pytest.fixture
def seeded_chat_db(tmp_path):
    """A chat.db with 100+ handles where only a handful are allowlisted.

    Disallowed handles get the most recent messages (higher date values);
    allowlisted handles get the oldest messages. Under the old `limit * 3`
    heuristic, a small `limit` would fetch only the most recent candidates -
    all disallowed - and silently return zero results even though enough
    allowed matches exist further down the date-ordered set.
    """
    db_path = tmp_path / "chat.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT,
            display_name TEXT, style INTEGER, service_name TEXT
        );
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, date INTEGER,
            is_from_me INTEGER, handle_id INTEGER, associated_message_type INTEGER,
            cache_has_attachments INTEGER, attributedBody BLOB, is_read INTEGER
        );
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        """
    )

    allowed_handles = ["+15550000001", "+15550000002", "+15550000003"]
    disallowed_count = 150  # comfortably more than any limit*3 heuristic window

    rowid = 1
    # Disallowed handles: newest messages (sorted first in date DESC order).
    for i in range(disallowed_count):
        handle_id = f"+1555999{i:04d}"
        chat_guid = f"iMessage;-;{handle_id}"
        msg_date = _apple_ns_for_offset(1_000_000_000 - i)  # descending, all newer than allowed

        conn.execute("INSERT INTO handle (ROWID, id) VALUES (?, ?)", (rowid, handle_id))
        conn.execute(
            "INSERT INTO chat (ROWID, guid, chat_identifier, display_name, style, service_name) "
            "VALUES (?, ?, ?, NULL, 45, 'iMessage')",
            (rowid, chat_guid, handle_id),
        )
        conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", (rowid, rowid))
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, date, is_from_me, handle_id, cache_has_attachments, is_read) "
            "VALUES (?, ?, 'hello world', ?, 0, ?, 0, 1)",
            (rowid, f"msg-disallowed-{i}", msg_date, rowid),
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)", (rowid, rowid))
        rowid += 1

    # Allowed handles: oldest messages (sorted last in date DESC order).
    for i, handle_id in enumerate(allowed_handles):
        chat_guid = f"iMessage;-;{handle_id}"
        msg_date = _apple_ns_for_offset(1_000 + i)  # far older than any disallowed message

        conn.execute("INSERT INTO handle (ROWID, id) VALUES (?, ?)", (rowid, handle_id))
        conn.execute(
            "INSERT INTO chat (ROWID, guid, chat_identifier, display_name, style, service_name) "
            "VALUES (?, ?, ?, NULL, 45, 'iMessage')",
            (rowid, chat_guid, handle_id),
        )
        conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)", (rowid, rowid))
        conn.execute(
            "INSERT INTO message (ROWID, guid, text, date, is_from_me, handle_id, cache_has_attachments, is_read) "
            "VALUES (?, ?, 'hello world', ?, 0, ?, 0, 1)",
            (rowid, f"msg-allowed-{i}", msg_date, rowid),
        )
        conn.execute("INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)", (rowid, rowid))
        rowid += 1

    conn.commit()
    conn.close()
    return db_path, allowed_handles


@pytest.fixture
def manager(monkeypatch, tmp_path, seeded_chat_db):
    db_path, allowed_handles = seeded_chat_db
    access_file = tmp_path / "access.json"
    access_file.write_text(json.dumps({"allow": allowed_handles}))

    monkeypatch.setattr(messages_mod, "CHAT_DB", db_path)
    monkeypatch.setattr(messages_mod, "ACCESS_FILE", access_file)

    mgr = MessagesManager()
    mgr._contacts = _FakeContactResolver()
    return mgr, allowed_handles


class TestSearchMessagesAllowlistBeforeLimit:
    def test_returns_full_limit_when_enough_allowed_matches_exist(self, manager):
        mgr, allowed_handles = manager

        results = mgr.search_messages("hello", limit=3)

        assert len(results) == 3
        returned_handles = {r["chat_id"].split(";-;")[-1] for r in results}
        assert returned_handles == set(allowed_handles)

    def test_naive_limit_times_three_window_would_have_missed_matches(self, manager):
        """Sanity check that the fixture actually exercises the bug: the most
        recent limit*3 rows (date DESC) are all disallowed."""
        mgr, _allowed_handles = manager
        conn = mgr._conn()
        try:
            rows = conn.execute(
                "SELECT c.chat_identifier FROM message m "
                "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
                "JOIN chat c ON c.ROWID = cmj.chat_id "
                "WHERE m.text LIKE '%hello%' ORDER BY m.date DESC LIMIT 9"
            ).fetchall()
        finally:
            conn.close()
        naive_window = [r["chat_identifier"] for r in rows]
        assert all(handle.startswith("+1555999") for handle in naive_window)

    def test_respects_limit_when_more_allowed_matches_exist_than_requested(self, manager):
        mgr, _allowed_handles = manager

        results = mgr.search_messages("hello", limit=2)

        assert len(results) == 2

    def test_returns_fewer_than_limit_when_fewer_allowed_matches_exist(self, manager):
        mgr, allowed_handles = manager

        results = mgr.search_messages("hello", limit=10)

        assert len(results) == len(allowed_handles)


class TestSearchMessagesKeysetPagination:
    """Regression coverage for issue #69's follow-up fix: OFFSET -> keyset pagination.

    chat.db is Messages.app's live database, continuously written while we
    read it. OFFSET pagination is positional: a row inserted between two page
    fetches shifts every later row's offset by one, so the next page can
    re-return the last row of the previous page (chat.db results aren't
    deduplicated by message id) while silently skipping the row that should
    have landed at the new page boundary. Keyset (seek) pagination seeks
    strictly past the last row actually returned, so it is immune to inserts
    that land anywhere at or above the cursor.
    """

    def test_search_messages_never_uses_sql_offset(self, manager):
        """Spy on the executed SQL via sqlite3's trace callback and assert
        OFFSET never appears, even across the multiple pages this fixture
        forces (150 disallowed rows exhaust the first batch before any
        allowed match is found, so search_messages must fetch a second page)."""
        mgr, _allowed_handles = manager
        executed_sql: list[str] = []
        real_conn_factory = mgr._conn

        def spying_conn():
            conn = real_conn_factory()
            conn.set_trace_callback(executed_sql.append)
            return conn

        mgr._conn = spying_conn

        results = mgr.search_messages("hello", limit=3)

        assert len(results) == 3
        message_queries = [sql for sql in executed_sql if "FROM message m" in sql]
        assert len(message_queries) >= 2, "fixture should force at least two pages"
        assert all("OFFSET" not in sql for sql in message_queries)

    def test_keyset_cursor_survives_insert_between_pages(self, seeded_chat_db):
        """Exercises the exact keyset SQL pattern search_messages uses,
        proving a row inserted between two page fetches (simulating a
        message arriving in chat.db mid-pagination) cannot cause a
        duplicate or a skipped row across the pages - then contrasts it
        with the equivalent OFFSET query, which does re-return a page-1
        row after the same insert, confirming the fixture actually
        exercises the bug OFFSET pagination has under concurrent writes.
        """
        db_path, _allowed_handles = seeded_chat_db
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            page_size = 5

            page1 = conn.execute(
                "SELECT m.rowid as msg_rowid, m.date FROM message m "
                "WHERE m.text LIKE '%hello%' ORDER BY m.date DESC, m.rowid DESC LIMIT ?",
                (page_size,),
            ).fetchall()
            assert len(page1) == page_size

            last_row = page1[-1]
            last_date, last_rowid = last_row["date"], last_row["msg_rowid"]

            # Simulate a concurrent write: a brand new message arrives,
            # newer than everything currently in the table - exactly what
            # happens whenever an iMessage is received while a search is
            # paginating through chat.db.
            conn.execute(
                "INSERT INTO message (ROWID, guid, text, date, is_from_me, handle_id, "
                "cache_has_attachments, is_read) VALUES (999999, 'msg-concurrent', "
                "'hello world', ?, 0, 1, 0, 1)",
                (_apple_ns_for_offset(9_000_000_000),),
            )
            conn.commit()

            # Page 2 via keyset seek: strictly past the last row we already returned.
            page2 = conn.execute(
                "SELECT m.rowid as msg_rowid FROM message m "
                "WHERE m.text LIKE '%hello%' AND (m.date < ? OR (m.date = ? AND m.rowid < ?)) "
                "ORDER BY m.date DESC, m.rowid DESC LIMIT ?",
                (last_date, last_date, last_rowid, page_size),
            ).fetchall()

            page1_ids = {r["msg_rowid"] for r in page1}
            page2_ids = {r["msg_rowid"] for r in page2}

            assert 999999 not in page2_ids, "the concurrently inserted row is newer than the cursor and must not appear"
            assert page1_ids.isdisjoint(page2_ids), "keyset pagination must not re-return a page-1 row on page 2"

            # Contrast: the equivalent OFFSET query re-derives its window
            # from position 0 on every call, so the same insert shifts
            # every row's offset by one and reproduces the historical bug.
            offset_page2 = conn.execute(
                "SELECT m.rowid as msg_rowid FROM message m "
                "WHERE m.text LIKE '%hello%' ORDER BY m.date DESC LIMIT ? OFFSET ?",
                (page_size, page_size),
            ).fetchall()
            offset_page2_ids = {r["msg_rowid"] for r in offset_page2}
            assert not page1_ids.isdisjoint(offset_page2_ids), (
                "sanity check: OFFSET pagination re-returns a page-1 row after the "
                "concurrent insert, which is exactly the bug keyset pagination avoids"
            )
        finally:
            conn.close()
