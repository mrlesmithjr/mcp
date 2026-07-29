"""Tests for ContactsManager.find_last_interaction's query-count fix (issue #68).

find_last_interaction used to run 2 SQL queries against Mail's Envelope
Index PER CONTACT WITH AN EMAIL. It now issues exactly 2 grouped queries
total, regardless of contact count - matching find_unknown_senders's
existing pattern - with results matched back to contacts in Python.

Builds a real temp SQLite DB with the Envelope Index's minimal schema
(messages/addresses/recipients) so the actual SQL runs, counts real
sqlite3.Connection.execute() calls to prove the query count is constant,
and asserts the per-contact output shape is unchanged from the old
per-contact-loop behavior.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from contacts_tools.contacts import ContactsManager

CD_EPOCH = datetime(2001, 1, 1)


def _cd_ts(dt):
    return (dt - CD_EPOCH).total_seconds()


@pytest.fixture()
def mail_db(tmp_path):
    """A minimal Envelope-Index-shaped SQLite DB."""
    db_path = tmp_path / "Envelope Index"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE addresses (ROWID INTEGER PRIMARY KEY, address TEXT, comment TEXT);
        CREATE TABLE messages (ROWID INTEGER PRIMARY KEY, sender INTEGER, date_sent REAL);
        CREATE TABLE recipients (message_id INTEGER, address_id INTEGER);
        """
    )
    conn.executemany(
        "INSERT INTO addresses (ROWID, address, comment) VALUES (?, ?, ?)",
        [
            (1, "alice@example.com", "Alice"),
            (2, "bob@example.com", "Bob"),
            (3, "me@example.com", "Me"),
        ],
    )

    now = datetime.now()
    insert_message = "INSERT INTO messages (ROWID, sender, date_sent) VALUES (?, ?, ?)"
    # Two messages received FROM alice - most recent (5 days ago) must win.
    conn.execute(insert_message, (1, 1, _cd_ts(now - timedelta(days=10))))
    conn.execute(insert_message, (2, 1, _cd_ts(now - timedelta(days=5))))
    # One message sent (by "me") TO bob, 3 days ago.
    conn.execute(insert_message, (3, 3, _cd_ts(now - timedelta(days=3))))
    conn.execute("INSERT INTO recipients (message_id, address_id) VALUES (3, 2)")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture(autouse=True)
def _patch_mail_db_path(mail_db, monkeypatch):
    """find_last_interaction hardcodes ~/Library/Mail/V10/.../Envelope Index -
    redirect just that one expanduser() call to the temp DB fixture.
    """
    import os

    original_expanduser = os.path.expanduser

    def fake_expanduser(path):
        if path == "~/Library/Mail/V10/MailData/Envelope Index":
            return str(mail_db)
        return original_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)


class _CountingConnection:
    """Wraps a real sqlite3.Connection, counting .execute() calls while
    forwarding everything else (row_factory, close, ...) untouched.
    """

    def __init__(self, real_conn):
        object.__setattr__(self, "_real", real_conn)
        object.__setattr__(self, "execute_count", 0)

    def execute(self, *args, **kwargs):
        object.__setattr__(self, "execute_count", self.execute_count + 1)
        return self._real.execute(*args, **kwargs)

    def close(self):
        self._real.close()

    def __setattr__(self, name, value):
        setattr(self._real, name, value)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture()
def counting_connections(monkeypatch):
    """Patches sqlite3.connect to return a _CountingConnection, and returns
    the list of every connection created during the test so callers can
    inspect execute_count per connection.
    """
    real_connect = sqlite3.connect
    created = []

    def fake_connect(*args, **kwargs):
        wrapped = _CountingConnection(real_connect(*args, **kwargs))
        created.append(wrapped)
        return wrapped

    monkeypatch.setattr(sqlite3, "connect", fake_connect)
    return created


def _contact(cid, name, email):
    return {"id": cid, "name": name, "emails": [{"value": email}]}


class TestFindLastInteractionQueryCount:
    def test_issues_exactly_two_queries_regardless_of_contact_count(self, counting_connections):
        mgr = ContactsManager()
        # 50 target contacts, none of which even appear in the fixture DB -
        # the old per-contact code would have issued 100 queries here.
        contacts = [_contact(f"id{i}", f"Contact {i}", f"nobody{i}@example.com") for i in range(50)]
        mgr.list_contacts = MagicMock(return_value=contacts)

        mgr.find_last_interaction()

        assert len(counting_connections) == 1
        assert counting_connections[0].execute_count == 2

    def test_output_matches_old_per_contact_semantics(self, counting_connections):
        mgr = ContactsManager()
        contacts = [
            _contact("alice-id", "Alice", "alice@example.com"),
            _contact("bob-id", "Bob", "bob@example.com"),
            _contact("carol-id", "Carol", "carol@example.com"),  # no mail history at all
        ]
        mgr.list_contacts = MagicMock(return_value=contacts)

        result = mgr.find_last_interaction()

        by_id = {c["id"]: c for c in result["contacts"]}

        alice = by_id["alice-id"]
        assert alice["last_sent"] is None
        assert alice["last_received"] is not None
        assert alice["days_ago"] == 5
        assert alice["last_interaction"] == alice["last_received"]

        bob = by_id["bob-id"]
        assert bob["last_received"] is None
        assert bob["last_sent"] is not None
        assert bob["days_ago"] == 3
        assert bob["last_interaction"] == bob["last_sent"]

        carol = by_id["carol-id"]
        assert carol["last_sent"] is None
        assert carol["last_received"] is None
        assert carol["last_interaction"] is None
        assert carol["days_ago"] is None

        # Most recent interaction first, no-history contact last.
        assert [c["id"] for c in result["contacts"]] == ["bob-id", "alice-id", "carol-id"]

    def test_single_contact_lookup_still_uses_the_same_two_grouped_queries(self, counting_connections):
        mgr = ContactsManager()
        mgr.get_contact = MagicMock(return_value=_contact("alice-id", "Alice", "alice@example.com"))

        result = mgr.find_last_interaction(contact_id="alice-id")

        assert len(counting_connections) == 1
        assert counting_connections[0].execute_count == 2
        assert result["contacts"][0]["last_received"] is not None


class TestFindStaleContactsQueryCount:
    def test_find_stale_contacts_stays_at_two_queries(self, counting_connections):
        """find_stale_contacts calls find_last_interaction(limit=500) under
        the hood - the fix must carry through by extension (issue #68).
        """
        mgr = ContactsManager()
        contacts = [_contact(f"id{i}", f"Contact {i}", f"nobody{i}@example.com") for i in range(30)]
        mgr.list_contacts = MagicMock(return_value=contacts)

        mgr.find_stale_contacts(days=1)

        assert len(counting_connections) == 1
        assert counting_connections[0].execute_count == 2
