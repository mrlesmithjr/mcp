"""Unit tests for MailManager batch-serialization and id-matching logic
(issues #43 and #44).

Uses lightweight fakes that mimic the small slice of ScriptingBridge's
SBElementArray/SBObject API mail_tools/mail.py depends on
(arrayByApplyingSelector_, indexing, and a handful of message accessors) -
no real Mail.app, no PyObjC object construction.
"""

from __future__ import annotations

import json
import time
import types
from datetime import date
from unittest.mock import patch

import pytest
from mail_tools.mail import MailError, MailManager, _batch_serialize_messages, _match_messages_by_id


class FakeDate:
    """Mimics NSDate's timeIntervalSince1970()."""

    def __init__(self, ts):
        self._ts = ts

    def timeIntervalSince1970(self):
        return self._ts


class FakeMessage:
    def __init__(
        self, message_id, subject="Test", sender="a@example.com", date_ts=0, read=False, flagged=False, content="Test"
    ):
        self._message_id = message_id
        self._subject = subject
        self._sender = sender
        self._date = FakeDate(date_ts)
        self._read = read
        self._flagged = flagged
        self._content = content

    def messageId(self):
        return self._message_id

    def subject(self):
        return self._subject

    def sender(self):
        return self._sender

    def content(self):
        return self._content

    def dateReceived(self):
        return self._date

    def readStatus(self):
        return self._read

    def flaggedStatus(self):
        return self._flagged

    def setReadStatus_(self, value):
        self._read = value

    def setFlaggedStatus_(self, value):
        self._flagged = value

    def moveTo_(self, mailbox):
        self.moved_to = mailbox

    def delete(self):
        self.deleted = True

    def toRecipients(self):
        return []

    def ccRecipients(self):
        return []


class FakeMessageCollection(list):
    """Mimics an SBElementArray - list-like plus arrayByApplyingSelector_."""

    def arrayByApplyingSelector_(self, selector):
        return [getattr(msg, selector)() for msg in self]


class FakeAccount:
    def __init__(self, name, emails=None):
        self._name = name
        self._emails = emails if emails is not None else []

    def name(self):
        return self._name

    def emailAddresses(self):
        return self._emails


class FakeMailbox:
    """Mimics the small slice of an SBObject mailbox mail.py depends on."""

    def __init__(self, messages, name="INBOX"):
        self._messages = messages
        self._name = name

    def messages(self):
        return self._messages

    def name(self):
        return self._name


class FakeMailboxWithUnread:
    """Mimics the mailbox.name()/unreadCount() slice get_unread_count()'s
    ScriptingBridge branch depends on (issue #101) - deliberately has no
    .messages(), since that path never calls it.
    """

    def __init__(self, name, unread_count):
        self._name = name
        self._unread_count = unread_count

    def name(self):
        return self._name

    def unreadCount(self):
        return self._unread_count


def _manager():
    """A MailManager with __init__ side effects safe for unit tests -
    _init_gmail() is a no-op when no Gmail credentials file exists.
    """
    return MailManager()


class TestMatchMessagesById:
    def test_matches_via_batch_selector_not_per_message_call(self):
        msgs = FakeMessageCollection([FakeMessage("a"), FakeMessage("b"), FakeMessage("c")])
        matches = _match_messages_by_id(msgs, {"b", "c"})
        assert {mid for _, mid in matches} == {"b", "c"}

    def test_no_matches_returns_empty_list(self):
        msgs = FakeMessageCollection([FakeMessage("a")])
        assert _match_messages_by_id(msgs, {"z"}) == []


class TestBatchSerializeMessages:
    def test_serializes_full_collection_without_limit(self):
        msgs = FakeMessageCollection([FakeMessage("a", date_ts=100), FakeMessage("b", date_ts=200)])
        results = _batch_serialize_messages(msgs)
        assert len(results) == 2
        assert {r["message_id"] for r in results} == {"a", "b"}

    def test_limit_still_supported_for_other_callers(self):
        msgs = FakeMessageCollection([FakeMessage("a"), FakeMessage("b"), FakeMessage("c")])
        results = _batch_serialize_messages(msgs, limit=2)
        assert len(results) == 2


class TestListMessagesFilterAfterFetch:
    """Issue #43: unread_only filtering (and the limit slice) must happen
    after the full batch fetch, not before - for every branch.
    """

    def test_unified_inbox_unread_only_survives_per_account_over_limit(self):
        # Two accounts. Account 1's newest `limit` messages are read, but an
        # older message is unread. The old code truncated to `limit` inside
        # each account's fetch *before* the global unread_only filter, so
        # this unread message would have been silently dropped.
        acct1 = FakeAccount("Acct1")
        inbox1 = FakeMessageCollection(
            [
                FakeMessage("m1", date_ts=300, read=True),
                FakeMessage("m2", date_ts=200, read=True),
                FakeMessage("m3", date_ts=100, read=False),
            ]
        )
        acct2 = FakeAccount("Acct2")
        inbox2 = FakeMessageCollection([FakeMessage("m4", date_ts=250, read=True)])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [
            (acct1, FakeMailbox(inbox1)),
            (acct2, FakeMailbox(inbox2)),
        ]

        results = mgr.list_messages(mailbox="INBOX", account=None, limit=2, unread_only=True)

        assert [r["message_id"] for r in results] == ["m3"]

    def test_account_scoped_inbox_unread_only_survives_over_limit(self):
        acct = FakeAccount("Acct1")
        inbox = FakeMessageCollection(
            [
                FakeMessage("m1", date_ts=300, read=True),
                FakeMessage("m2", date_ts=200, read=True),
                FakeMessage("m3", date_ts=100, read=False),
            ]
        )

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(inbox))]

        results = mgr.list_messages(mailbox="INBOX", account="Acct1", limit=2, unread_only=True)

        assert [r["message_id"] for r in results] == ["m3"]

    def test_named_mailbox_unread_only_survives_over_limit(self):
        msgs = FakeMessageCollection(
            [
                FakeMessage("m1", date_ts=300, read=True),
                FakeMessage("m2", date_ts=200, read=True),
                FakeMessage("m3", date_ts=100, read=False),
            ]
        )

        mgr = _manager()
        mgr._get_mailbox_messages = lambda mailbox, account: msgs

        results = mgr.list_messages(mailbox="Important", account=None, limit=2, unread_only=True)

        assert [r["message_id"] for r in results] == ["m3"]

    def test_results_sorted_by_date_descending_then_sliced_to_limit(self):
        acct = FakeAccount("Acct1")
        inbox = FakeMessageCollection(
            [
                FakeMessage("old", date_ts=100),
                FakeMessage("new", date_ts=300),
                FakeMessage("mid", date_ts=200),
            ]
        )

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(inbox))]

        results = mgr.list_messages(mailbox="INBOX", account="Acct1", limit=2, unread_only=False)

        assert [r["message_id"] for r in results] == ["new", "mid"]

    def test_account_scoped_inbox_not_found_raises(self):
        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: []

        try:
            mgr.list_messages(mailbox="INBOX", account="Ghost")
        except Exception as e:
            assert "Ghost" in str(e)
        else:
            raise AssertionError("expected MailError for missing account INBOX")


class TestArchiveMessagesBatchCollection:
    """Issue #44: the INBOX collection step uses a single batch messageId
    fetch (_match_messages_by_id) instead of a per-message .messageId() loop.
    """

    def test_matches_targets_via_batch_selector_non_gmail(self):
        acct = FakeAccount("Acct1")
        msgs = FakeMessageCollection([FakeMessage("keep"), FakeMessage("archive_me"), FakeMessage("also_archive")])
        archive_mb = FakeMailbox(FakeMessageCollection([]), name="Archive")

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: False
        mgr._get_archive_info = lambda a: ("archive", archive_mb)

        result = mgr.archive_messages(["archive_me", "also_archive"])

        assert result["archived"] == 2
        assert result["not_found"] == 0
        archived_ids = {r["message_id"] for r in result["results"] if r.get("archived")}
        assert archived_ids == {"archive_me", "also_archive"}

    def test_missing_ids_reported_not_found(self):
        acct = FakeAccount("Acct1")
        msgs = FakeMessageCollection([FakeMessage("keep")])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: False
        mgr._get_archive_info = lambda a: ("archive", FakeMailbox(FakeMessageCollection([]), name="Archive"))
        mgr._gmail = None

        result = mgr.archive_messages(["ghost"])

        assert result["archived"] == 0
        assert result["not_found"] == 1


class TestDeleteMessagesBatchCollection:
    def test_matches_targets_via_batch_selector_non_gmail(self):
        acct = FakeAccount("Acct1")
        msgs = FakeMessageCollection([FakeMessage("keep"), FakeMessage("trash_me")])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: False

        result = mgr.delete_messages(["trash_me"])

        assert result["deleted"] == 1
        assert result["not_found"] == 0
        deleted = [r for r in result["results"] if r["message_id"] == "trash_me"]
        assert deleted[0]["deleted"] is True


class TestMarkMessagesBatchCollectionAndFallback:
    """Issue #44: mark_read_messages/mark_unread_messages mirror
    archive_messages/delete_messages, plus a Pass 4 ScriptingBridge
    mailbox-scan fallback for non-Gmail accounts (issue #89 inserted a new
    Pass 3 direct-id lookup ahead of it - see
    TestMarkMessagesPass4NeverReachedForGmailAccount).
    """

    def test_marks_matching_inbox_messages_read(self):
        acct = FakeAccount("Acct1")
        msgs = FakeMessageCollection([FakeMessage("a", read=False), FakeMessage("b", read=False)])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: False
        mgr._gmail = None

        result = mgr.mark_read_messages(["a"])

        assert result["marked"] == 1
        assert result["not_found"] == 0
        assert msgs[0].readStatus() is True
        assert msgs[1].readStatus() is False

    def test_fallback_scans_other_mailboxes_for_non_gmail_accounts(self):
        acct = FakeAccount("Acct1")
        empty_inbox = FakeMessageCollection([])
        important = FakeMessageCollection([FakeMessage("filed_away", read=True)])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: False
        mgr._get_accounts = lambda: [acct]
        acct.mailboxes = lambda: [FakeMailbox(important, name="Important")]
        mgr._gmail = None

        result = mgr.mark_unread_messages(["filed_away"])

        assert result["marked"] == 1
        assert result["not_found"] == 0
        assert important[0].readStatus() is False

    def test_message_not_found_anywhere_reports_not_found(self):
        acct = FakeAccount("Acct1")

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(FakeMessageCollection([])))]
        mgr._is_gmail_api_account = lambda a: False
        mgr._get_accounts = lambda: [acct]
        acct.mailboxes = lambda: []
        mgr._gmail = None

        result = mgr.mark_read_messages(["ghost"])

        assert result["marked"] == 0
        assert result["not_found"] == 1
        assert result["results"][0]["reason"] == "not found"

    def test_mark_read_and_mark_unread_singular_wrappers(self):
        acct = FakeAccount("Acct1")
        msgs = FakeMessageCollection([FakeMessage("a", read=False)])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: False
        mgr._gmail = None

        result = mgr.mark_read("a")
        assert result["message_id"] == "a"
        assert result["marked"] is True
        assert result["status"] == "read"


class FakeGmailForBatching:
    """Records calls to the plural batch methods (archive_messages,
    delete_messages, mark_read_messages, mark_unread_messages).

    Deliberately does NOT implement the singular per-message methods
    (archive_message, delete_message) - if regressed code loops those
    instead of batching, the test fails loudly with an AttributeError
    rather than silently passing.
    """

    def __init__(self):
        self.archive_calls = []
        self.delete_calls = []
        self.mark_read_calls = []
        self.mark_unread_calls = []
        self.authorized_accounts = []

    def archive_messages(self, email, ids):
        self.archive_calls.append((email, list(ids)))
        return [{"message_id": mid, "archived": True, "destination": "All Mail", "account": email} for mid in ids]

    def delete_messages(self, email, ids):
        self.delete_calls.append((email, list(ids)))
        return [{"message_id": mid, "deleted": True, "account": email} for mid in ids]

    def mark_read_messages(self, email, ids):
        self.mark_read_calls.append((email, list(ids)))
        return [{"message_id": mid, "marked": True, "status": "read", "account": email} for mid in ids]

    def mark_unread_messages(self, email, ids):
        self.mark_unread_calls.append((email, list(ids)))
        return [{"message_id": mid, "marked": True, "status": "unread", "account": email} for mid in ids]

    def list_authorized_accounts(self):
        return self.authorized_accounts


class TestGmailApiAccountBatching:
    """Code review follow-up on #44: every prior test forced
    `_is_gmail_api_account` to False, so the Gmail branch (gmail_batch
    collection in Pass 1, and the Pass 2 API fallback) was never exercised.
    That's exactly why the Pass 2 delete regression - looping the singular
    delete_message() per id instead of batching via delete_messages() -
    went undetected. These tests force the Gmail branch and assert the
    plural batch method receives the FULL id list in a single call.
    """

    def test_archive_messages_pass1_batches_full_id_list(self):
        acct = FakeAccount("Gmail")
        msgs = FakeMessageCollection([FakeMessage("a"), FakeMessage("b"), FakeMessage("keep")])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._get_archive_info = lambda a: ("archive", FakeMailbox(FakeMessageCollection([]), name="Archive"))
        fake_gmail = FakeGmailForBatching()
        mgr._gmail = fake_gmail

        result = mgr.archive_messages(["a", "b"])

        assert len(fake_gmail.archive_calls) == 1
        email, ids = fake_gmail.archive_calls[0]
        assert email == "user@gmail.com"
        assert set(ids) == {"a", "b"}
        assert result["archived"] == 2
        assert result["not_found"] == 0

    def test_delete_messages_pass1_batches_full_id_list(self):
        acct = FakeAccount("Gmail")
        msgs = FakeMessageCollection([FakeMessage("a"), FakeMessage("b"), FakeMessage("keep")])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForBatching()
        mgr._gmail = fake_gmail

        result = mgr.delete_messages(["a", "b"])

        assert len(fake_gmail.delete_calls) == 1
        email, ids = fake_gmail.delete_calls[0]
        assert email == "user@gmail.com"
        assert set(ids) == {"a", "b"}
        assert result["deleted"] == 2
        assert result["not_found"] == 0

    def test_delete_messages_pass2_missing_ids_batched_not_looped(self):
        """Regression test for the Pass 2 delete bug: ids NOT found in the
        Pass 1 INBOX scan must go through a single delete_messages() call
        with the full missing-id list, not a delete_message() loop.
        """
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForBatching()
        fake_gmail.authorized_accounts = ["user@gmail.com"]
        mgr._gmail = fake_gmail

        result = mgr.delete_messages(["x", "y", "z"])

        assert len(fake_gmail.delete_calls) == 1
        email, ids = fake_gmail.delete_calls[0]
        assert email == "user@gmail.com"
        assert set(ids) == {"x", "y", "z"}
        assert result["deleted"] == 3
        assert result["not_found"] == 0

    def test_archive_messages_pass2_missing_ids_batched_not_looped(self):
        """Regression test for issue #46: ids NOT found in the Pass 1 INBOX
        scan must go through a single archive_messages() call with the full
        missing-id list, not an archive_message() loop (mirrors the #44
        delete Pass 2 regression test above).
        """
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._get_archive_info = lambda a: ("archive", FakeMailbox(FakeMessageCollection([]), name="Archive"))
        fake_gmail = FakeGmailForBatching()
        fake_gmail.authorized_accounts = ["user@gmail.com"]
        mgr._gmail = fake_gmail

        result = mgr.archive_messages(["x", "y", "z"])

        assert len(fake_gmail.archive_calls) == 1
        email, ids = fake_gmail.archive_calls[0]
        assert email == "user@gmail.com"
        assert set(ids) == {"x", "y", "z"}
        assert result["archived"] == 3
        assert result["not_found"] == 0

    def test_mark_read_messages_pass1_batches_full_id_list(self):
        acct = FakeAccount("Gmail")
        msgs = FakeMessageCollection([FakeMessage("a"), FakeMessage("b")])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForBatching()
        mgr._gmail = fake_gmail

        result = mgr.mark_read_messages(["a", "b"])

        assert len(fake_gmail.mark_read_calls) == 1
        email, ids = fake_gmail.mark_read_calls[0]
        assert email == "user@gmail.com"
        assert set(ids) == {"a", "b"}
        assert result["marked"] == 2
        assert result["not_found"] == 0

    def test_mark_unread_messages_pass1_batches_full_id_list(self):
        acct = FakeAccount("Gmail")
        msgs = FakeMessageCollection([FakeMessage("a"), FakeMessage("b")])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForBatching()
        mgr._gmail = fake_gmail

        result = mgr.mark_unread_messages(["a", "b"])

        assert len(fake_gmail.mark_unread_calls) == 1
        email, ids = fake_gmail.mark_unread_calls[0]
        assert email == "user@gmail.com"
        assert set(ids) == {"a", "b"}
        assert result["marked"] == 2
        assert result["not_found"] == 0


class FakeGmailForDirectLookup:
    """Records message_exists()/modify_by_gmail_id() calls for Pass 3
    direct-id lookup tests (issue #89).

    `ownership` maps gmail_id -> the single authorized account that "has"
    it - message_exists() returns True only for that (email, id) pair,
    mirroring the real GmailClient.message_exists() 404-means-False
    contract. `rfc822_lookup_fails` is not modeled explicitly: this fake
    deliberately has no archive_messages()/delete_messages()/
    mark_read_messages()/mark_unread_messages() methods at all, so a
    regression that routes Pass 3 through those rfc822-id-oriented methods
    (instead of modify_by_gmail_id()) fails loudly with an AttributeError,
    the same "no singular/wrong-shaped method" trick FakeGmailForBatching
    uses for Pass 1/Pass 2.

    `raises` maps an account email to an exception raised by EVERY call for
    that account (message_exists() and modify_by_gmail_id() alike),
    simulating a dead/expired token encountered mid-Pass-3.
    """

    def __init__(self, authorized_accounts, ownership=None, raises=None):
        self._authorized_accounts = authorized_accounts
        self._ownership = ownership or {}
        self._raises = raises or {}
        self.message_exists_calls = []
        self.modify_by_gmail_id_calls = []

    def list_authorized_accounts(self):
        return self._authorized_accounts

    def message_exists(self, email, gmail_id):
        self.message_exists_calls.append((email, gmail_id))
        if email in self._raises:
            raise self._raises[email]
        return self._ownership.get(gmail_id) == email

    def modify_by_gmail_id(self, email, gmail_ids, add_label_ids=None, remove_label_ids=None):
        self.modify_by_gmail_id_calls.append((email, list(gmail_ids), add_label_ids, remove_label_ids))
        if email in self._raises:
            raise self._raises[email]
        return [{"message_id": gid, "modified": True, "account": email} for gid in gmail_ids]


class TestGmailDirectIdLookupFallback:
    """Issue #89: mail_archive/mail_delete/mail_mark_read/mail_mark_unread
    must resolve a Gmail-internal message id for a message that is NOT
    currently in the account's INBOX label (e.g. already archived, or
    filtered directly to a category/label on arrival) - neither Pass 1's
    INBOX scan nor Pass 2's rfc822msgid: search (which only matches actual
    RFC 5322 Message-IDs, never a bare Gmail-internal hex id) can find such
    a message. Pass 3 checks each Gmail-authorized account directly via
    message_exists(), then mutates via modify_by_gmail_id() - never via the
    rfc822-id-oriented archive_messages()/delete_messages()/
    mark_read_messages()/mark_unread_messages(), which would re-run the same
    rfc822msgid search that already failed in Pass 2.
    """

    def test_archive_resolves_gmail_internal_id_not_in_inbox_via_direct_lookup(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        mgr._get_archive_info = lambda a: ("archive", FakeMailbox(FakeMessageCollection([]), name="Archive"))
        fake_gmail = FakeGmailForDirectLookup(
            authorized_accounts=["user@gmail.com"],
            ownership={"19c9fe37b3736a60": "user@gmail.com"},
        )
        mgr._gmail = fake_gmail

        result = mgr.archive_messages(["19c9fe37b3736a60"])

        assert fake_gmail.message_exists_calls == [("user@gmail.com", "19c9fe37b3736a60")]
        assert len(fake_gmail.modify_by_gmail_id_calls) == 1
        email, ids, add_ids, remove_ids = fake_gmail.modify_by_gmail_id_calls[0]
        assert email == "user@gmail.com"
        assert ids == ["19c9fe37b3736a60"]
        assert remove_ids == ["INBOX", "TRASH"]
        assert result["archived"] == 1
        assert result["not_found"] == 0
        assert result["results"][0]["destination"] == "All Mail"

    def test_delete_resolves_gmail_internal_id_not_in_inbox_via_direct_lookup(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        fake_gmail = FakeGmailForDirectLookup(
            authorized_accounts=["user@gmail.com"],
            ownership={"19c9fe37b3736a60": "user@gmail.com"},
        )
        mgr._gmail = fake_gmail

        result = mgr.delete_messages(["19c9fe37b3736a60"])

        assert len(fake_gmail.modify_by_gmail_id_calls) == 1
        email, ids, add_ids, remove_ids = fake_gmail.modify_by_gmail_id_calls[0]
        assert email == "user@gmail.com"
        assert ids == ["19c9fe37b3736a60"]
        assert add_ids == ["TRASH"]
        assert remove_ids == ["INBOX"]
        assert result["deleted"] == 1
        assert result["not_found"] == 0

    def test_mark_read_resolves_gmail_internal_id_not_in_inbox_via_direct_lookup(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        fake_gmail = FakeGmailForDirectLookup(
            authorized_accounts=["user@gmail.com"],
            ownership={"19c9fe37b3736a60": "user@gmail.com"},
        )
        mgr._gmail = fake_gmail

        result = mgr.mark_read_messages(["19c9fe37b3736a60"])

        assert len(fake_gmail.modify_by_gmail_id_calls) == 1
        email, ids, add_ids, remove_ids = fake_gmail.modify_by_gmail_id_calls[0]
        assert email == "user@gmail.com"
        assert ids == ["19c9fe37b3736a60"]
        assert remove_ids == ["UNREAD"]
        assert add_ids is None
        assert result["marked"] == 1
        assert result["not_found"] == 0
        assert result["results"][0]["status"] == "read"

    def test_mark_unread_resolves_gmail_internal_id_not_in_inbox_via_direct_lookup(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        fake_gmail = FakeGmailForDirectLookup(
            authorized_accounts=["user@gmail.com"],
            ownership={"19c9fe37b3736a60": "user@gmail.com"},
        )
        mgr._gmail = fake_gmail

        result = mgr.mark_unread_messages(["19c9fe37b3736a60"])

        assert len(fake_gmail.modify_by_gmail_id_calls) == 1
        email, ids, add_ids, remove_ids = fake_gmail.modify_by_gmail_id_calls[0]
        assert email == "user@gmail.com"
        assert ids == ["19c9fe37b3736a60"]
        assert add_ids == ["UNREAD"]
        assert remove_ids is None
        assert result["marked"] == 1
        assert result["not_found"] == 0
        assert result["results"][0]["status"] == "unread"

    def test_rfc822_style_id_never_tried_via_direct_lookup(self):
        """An id containing "@" is a genuinely missing non-Gmail message at
        this point (Pass 1/Pass 2 already exhausted INBOX and rfc822msgid:
        search) - message_exists() must never be called for it, matching
        read_message()'s same "@" heuristic for Gmail routing (issue #87)."""
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        mgr._get_archive_info = lambda a: ("archive", FakeMailbox(FakeMessageCollection([]), name="Archive"))
        fake_gmail = FakeGmailForDirectLookup(authorized_accounts=["user@gmail.com"])
        mgr._gmail = fake_gmail

        result = mgr.archive_messages(["<abc@mail.example.com>"])

        assert fake_gmail.message_exists_calls == []
        assert result["archived"] == 0
        assert result["not_found"] == 1
        assert (
            result["results"][0]["reason"]
            == "not found in any INBOX, rfc822 Message-ID search, or direct Gmail id lookup"
        )

    def test_multiple_ids_grouped_by_owning_account_one_call_per_account(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        fake_gmail = FakeGmailForDirectLookup(
            authorized_accounts=["a@gmail.com", "b@gmail.com"],
            ownership={"id1": "a@gmail.com", "id2": "a@gmail.com", "id3": "b@gmail.com"},
        )
        mgr._gmail = fake_gmail

        result = mgr.mark_read_messages(["id1", "id2", "id3"])

        assert len(fake_gmail.modify_by_gmail_id_calls) == 2
        calls_by_email = {c[0]: set(c[1]) for c in fake_gmail.modify_by_gmail_id_calls}
        assert calls_by_email == {"a@gmail.com": {"id1", "id2"}, "b@gmail.com": {"id3"}}
        assert result["marked"] == 3
        assert result["not_found"] == 0

    def test_id_not_owned_by_any_authorized_account_reported_not_found(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        fake_gmail = FakeGmailForDirectLookup(authorized_accounts=["user@gmail.com"], ownership={})
        mgr._gmail = fake_gmail

        result = mgr.mark_unread_messages(["ghost_gmail_id"])

        assert fake_gmail.message_exists_calls == [("user@gmail.com", "ghost_gmail_id")]
        assert fake_gmail.modify_by_gmail_id_calls == []
        assert result["marked"] == 0
        assert result["not_found"] == 1

    def test_dead_token_account_during_direct_lookup_does_not_abort_other_accounts(self):
        """A dead/expired token on one authorized account encountered during
        Pass 3's message_exists() loop must not abort resolution for an id
        that belongs to a different, healthy account (mirrors issue #83's
        Pass 2 dead-token handling)."""
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        fake_gmail = FakeGmailForDirectLookup(
            authorized_accounts=["dead@example.com", "healthy@gmail.com"],
            ownership={"id1": "healthy@gmail.com"},
            raises={"dead@example.com": RuntimeError("token revoked")},
        )
        mgr._gmail = fake_gmail

        result = mgr.mark_read_messages(["id1"])

        assert [c[0] for c in fake_gmail.message_exists_calls] == ["dead@example.com", "healthy@gmail.com"]
        assert result["marked"] == 1
        assert result["not_found"] == 0


class GmailApiErrorLike(Exception):
    """Stand-in for gmail.GmailError. _call_gmail_batch (mail.py) catches
    generic Exception, so tests don't need the real GmailError class - any
    exception raised from a fake account's batch call exercises the same
    catch-and-skip path a dead/expired token would.
    """


class FakeGmailForDeadToken:
    """One account whose batch calls always raise (simulating a dead/
    expired OAuth token, e.g. invalid_grant on refresh), alongside a
    healthy account whose batch calls succeed normally.

    Regression fixture for issue #83: a dead token for one Gmail-authorized
    account must not abort a bulk archive/delete/mark operation whose
    target ids belong to a different, healthy account.
    """

    def __init__(self, dead_email, healthy_email):
        self.dead_email = dead_email
        self.healthy_email = healthy_email
        self.calls = []

    def list_authorized_accounts(self):
        return [self.dead_email, self.healthy_email]

    def _dispatch(self, email, ids, ok_key):
        self.calls.append((email, list(ids)))
        if email == self.dead_email:
            raise GmailApiErrorLike(f"Token refresh failed for {email}: invalid_grant")
        result = {"message_id": None, ok_key: True, "account": email}
        return [{**result, "message_id": mid} for mid in ids]

    def archive_messages(self, email, ids):
        results = self._dispatch(email, ids, "archived")
        for r in results:
            r["destination"] = "All Mail"
        return results

    def delete_messages(self, email, ids):
        return self._dispatch(email, ids, "deleted")

    def mark_read_messages(self, email, ids):
        results = self._dispatch(email, ids, "marked")
        for r in results:
            r["status"] = "read"
        return results

    def mark_unread_messages(self, email, ids):
        results = self._dispatch(email, ids, "marked")
        for r in results:
            r["status"] = "unread"
        return results


class TestDeadTokenAccountDoesNotAbortBulkOps:
    """Issue #83: bulk operations scan every Gmail-authorized account's
    INBOX to resolve target message ids, and the Pass 2 rfc822msgid
    fallback loops every authorized account unconditionally regardless of
    which one the target ids actually belong to. Before this fix, a dead
    token anywhere in that loop raised and aborted the whole call - even
    when the target ids belonged to a completely different, healthy
    account. These tests put a dead-token account ahead of a healthy one
    in list_authorized_accounts() and assert the healthy account's
    operation still succeeds.
    """

    def test_archive_pass2_dead_token_account_does_not_abort_healthy_account(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        mgr._get_archive_info = lambda a: ("archive", FakeMailbox(FakeMessageCollection([]), name="Archive"))
        fake_gmail = FakeGmailForDeadToken("dead@methodicalcloud.com", "healthy@gmail.com")
        mgr._gmail = fake_gmail

        result = mgr.archive_messages(["x", "y"])

        assert result["archived"] == 2
        assert result["not_found"] == 0
        assert {email for email, _ in fake_gmail.calls} == {"dead@methodicalcloud.com", "healthy@gmail.com"}

    def test_delete_pass2_dead_token_account_does_not_abort_healthy_account(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        fake_gmail = FakeGmailForDeadToken("dead@methodicalcloud.com", "healthy@gmail.com")
        mgr._gmail = fake_gmail

        result = mgr.delete_messages(["x", "y"])

        assert result["deleted"] == 2
        assert result["not_found"] == 0

    def test_mark_read_pass2_dead_token_account_does_not_abort_healthy_account(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        fake_gmail = FakeGmailForDeadToken("dead@methodicalcloud.com", "healthy@gmail.com")
        mgr._gmail = fake_gmail

        result = mgr.mark_read_messages(["x", "y"])

        assert result["marked"] == 2
        assert result["not_found"] == 0

    def test_mark_unread_pass2_dead_token_account_does_not_abort_healthy_account(self):
        acct = FakeAccount("Gmail")
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "irrelevant@example.com"
        fake_gmail = FakeGmailForDeadToken("dead@methodicalcloud.com", "healthy@gmail.com")
        mgr._gmail = fake_gmail

        result = mgr.mark_unread_messages(["x", "y"])

        assert result["marked"] == 2
        assert result["not_found"] == 0

    def test_archive_pass1_dead_token_account_batch_reported_not_crashed(self):
        """A dead-token account whose own INBOX holds the target message:
        Pass 1's per-account Gmail call fails, and must be reported as a
        failed result rather than propagating and losing the batch.
        """
        acct = FakeAccount("Gmail")
        msgs = FakeMessageCollection([FakeMessage("a")])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "dead@methodicalcloud.com"
        mgr._get_archive_info = lambda a: ("archive", FakeMailbox(FakeMessageCollection([]), name="Archive"))

        class DeadOnlyGmail:
            def archive_messages(self, email, ids):
                raise GmailApiErrorLike("Token refresh failed: invalid_grant")

            def list_authorized_accounts(self):
                return []

        mgr._gmail = DeadOnlyGmail()

        result = mgr.archive_messages(["a"])

        assert result["archived"] == 0
        assert result["not_found"] == 0
        assert result["results"] == [
            {
                "message_id": "a",
                "archived": False,
                "account": "Gmail",
                "reason": "Gmail account error - see logs",
            }
        ]


class FakeGmailForMarkPass4Regression:
    """mark_read_messages/mark_unread_messages/message_exists that all
    report a Gmail id as unresolved, for issue #50 step 5's regression test
    (renumbered for issue #89's new Pass 3): proves Pass 4's ScriptingBridge
    mailbox scan is never reached for a Gmail-authorized account, even when
    Pass 1 (INBOX scan), Pass 2 (rfc822msgid search), and Pass 3 (direct-id
    lookup) all fail to resolve the id.
    """

    def __init__(self):
        self.mark_read_calls = []
        self.mark_unread_calls = []
        self.message_exists_calls = []

    def list_authorized_accounts(self):
        return ["user@gmail.com"]

    def mark_read_messages(self, email, ids):
        self.mark_read_calls.append((email, list(ids)))
        return [{"message_id": mid, "marked": False} for mid in ids]

    def mark_unread_messages(self, email, ids):
        self.mark_unread_calls.append((email, list(ids)))
        return [{"message_id": mid, "marked": False} for mid in ids]

    def message_exists(self, email, gmail_id):
        self.message_exists_calls.append((email, gmail_id))
        return False


class TestMarkMessagesPass4NeverReachedForGmailAccount:
    """Issue #50 step 5 (renumbered for issue #89's new Pass 3): the Pass 4
    ScriptingBridge mailbox-scan fallback in MailManager._mark_messages
    excludes Gmail-authorized accounts as a hard invariant, not an
    incidental scoping detail (see the docstring in mail.py). A Gmail
    message id unresolved by Pass 1 (INBOX scan), Pass 2 (rfc822msgid
    search), and Pass 3 (direct-id lookup) must be reported not_found -
    never handed to a ScriptingBridge mailbox scan.

    Pass 4's mailbox loop wraps `mb.messages()` in a bare `try/except
    Exception: continue` (for non-Gmail accounts whose mailbox listing can
    legitimately fail). A `.messages()` fake that raises to prove
    "never reached" is swallowed by that same handler, which makes the
    test pass whether or not the Gmail-account guard is present - a false
    positive. Instead, this test spies on `acct.mailboxes()` itself, which
    is called (and would raise via `mailboxes_calls`) before Pass 4 ever
    enters the try/except, and asserts it is never invoked for the
    Gmail-authorized account.
    """

    def test_unresolved_gmail_id_never_falls_through_to_scriptingbridge_mark_read(self):
        acct = FakeAccount("Gmail", emails=["user@gmail.com"])
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._get_accounts = lambda: [acct]
        mailboxes_calls = []

        def _mailboxes():
            mailboxes_calls.append(1)
            return [RaisingMailbox(FakeMessageCollection([]), name="Important")]

        acct.mailboxes = _mailboxes
        fake_gmail = FakeGmailForMarkPass4Regression()
        mgr._gmail = fake_gmail

        result = mgr.mark_read_messages(["ghost_gmail_id"])

        assert mailboxes_calls == [], "acct.mailboxes() must never be called for a Gmail-authorized account"
        assert len(fake_gmail.mark_read_calls) == 1
        assert fake_gmail.mark_read_calls[0] == ("user@gmail.com", ["ghost_gmail_id"])
        assert fake_gmail.message_exists_calls == [("user@gmail.com", "ghost_gmail_id")]
        assert result["marked"] == 0
        assert result["not_found"] == 1
        assert result["results"] == [
            {"message_id": "ghost_gmail_id", "marked": False, "status": "read", "reason": "not found"}
        ]

    def test_unresolved_gmail_id_never_falls_through_to_scriptingbridge_mark_unread(self):
        acct = FakeAccount("Gmail", emails=["user@gmail.com"])
        empty_inbox = FakeMessageCollection([])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(empty_inbox))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._get_accounts = lambda: [acct]
        mailboxes_calls = []

        def _mailboxes():
            mailboxes_calls.append(1)
            return [RaisingMailbox(FakeMessageCollection([]), name="Important")]

        acct.mailboxes = _mailboxes
        fake_gmail = FakeGmailForMarkPass4Regression()
        mgr._gmail = fake_gmail

        result = mgr.mark_unread_messages(["ghost_gmail_id"])

        assert mailboxes_calls == [], "acct.mailboxes() must never be called for a Gmail-authorized account"
        assert len(fake_gmail.mark_unread_calls) == 1
        assert fake_gmail.message_exists_calls == [("user@gmail.com", "ghost_gmail_id")]
        assert result["marked"] == 0
        assert result["not_found"] == 1
        assert result["results"] == [
            {"message_id": "ghost_gmail_id", "marked": False, "status": "unread", "reason": "not found"}
        ]


class FakeReadInboxMailbox(FakeMailbox):
    """A FakeMailbox whose messages() records every call, so tests can
    assert the unified-inbox fast path was (or wasn't) consulted."""

    def __init__(self, messages, name="INBOX"):
        super().__init__(messages, name=name)
        self.messages_calls = 0

    def messages(self):
        self.messages_calls += 1
        return self._messages


class FakeReadApp:
    """Minimal fake of SBApplication's Mail.app surface for
    read_message()'s unified-inbox fast path - only .inbox() is needed.
    """

    def __init__(self, inbox_mailbox):
        self._inbox_mailbox = inbox_mailbox

    def inbox(self):
        return self._inbox_mailbox


class FakeGmailForRead:
    """Records get_message() calls per account for read_message() tests
    (issue #87). authorized/results are keyed by email so a test can make
    one account fail/miss and another succeed.
    """

    def __init__(self, authorized_accounts, results=None, raises=None):
        self._authorized_accounts = authorized_accounts
        self._results = results or {}
        self._raises = raises or {}
        self.get_message_calls = []

    def list_authorized_accounts(self):
        return self._authorized_accounts

    def get_message(self, email, gmail_id):
        self.get_message_calls.append((email, gmail_id))
        if email in self._raises:
            raise self._raises[email]
        return self._results.get(email)


class TestReadMessageGmailRouting:
    """Issue #87: read_message() must resolve Gmail-internal ids (as
    returned by mail_list/mail_search/mail_bulk_action on a
    Gmail-authorized account) that ScriptingBridge's rfc822-Message-ID
    lookup can never match, without breaking non-Gmail accounts.
    """

    def test_scriptingbridge_message_found_via_unified_inbox_unaffected(self):
        """Baseline: a non-Gmail message still resolves via the existing
        fast unified-inbox path, with no Gmail involvement at all."""
        msg = FakeMessage("<abc@mail.example.com>", subject="Hi", sender="a@example.com", date_ts=0)
        inbox = FakeReadInboxMailbox(FakeMessageCollection([msg]))

        mgr = _manager()
        mgr._app = FakeReadApp(inbox)
        fake_gmail = FakeGmailForRead(authorized_accounts=[])
        mgr._gmail = fake_gmail

        result = mgr.read_message("<abc@mail.example.com>")

        assert result["message_id"] == "<abc@mail.example.com>"
        assert result["subject"] == "Hi"
        assert result["content"] == "Test"
        assert fake_gmail.get_message_calls == []

    def test_rfc822_style_id_never_tries_gmail_before_slow_scriptingbridge_scan(self):
        """An id containing "@" is never routed to the Gmail API - it goes
        straight from the fast unified-inbox miss to the slow
        all-mailboxes ScriptingBridge scan, exactly as before issue #87."""
        empty_inbox = FakeReadInboxMailbox(FakeMessageCollection([]))
        filed_msg = FakeMessage("<filed@mail.example.com>", subject="Filed away")
        acct = FakeAccount("iCloud", emails=["me@icloud.com"])
        acct.mailboxes = lambda: [FakeMailbox(FakeMessageCollection([filed_msg]), name="Archive")]

        mgr = _manager()
        mgr._app = FakeReadApp(empty_inbox)
        mgr._get_accounts = lambda: [acct]
        fake_gmail = FakeGmailForRead(authorized_accounts=["user@gmail.com"])
        mgr._gmail = fake_gmail

        result = mgr.read_message("<filed@mail.example.com>")

        assert result["message_id"] == "<filed@mail.example.com>"
        assert result["subject"] == "Filed away"
        assert fake_gmail.get_message_calls == []

    def test_gmail_style_id_routes_to_gmail_api_when_not_in_scriptingbridge_inbox(self):
        """A Gmail-internal id (no "@") that misses the unified-inbox fast
        path is resolved via the Gmail API before the slow ScriptingBridge
        scan runs at all."""
        empty_inbox = FakeReadInboxMailbox(FakeMessageCollection([]))

        def _slow_scan_must_not_run():
            raise AssertionError("slow ScriptingBridge scan must not run")

        mgr = _manager()
        mgr._app = FakeReadApp(empty_inbox)
        mgr._get_accounts = _slow_scan_must_not_run
        fake_gmail = FakeGmailForRead(
            authorized_accounts=["user@gmail.com"],
            results={
                "user@gmail.com": {
                    "message_id": "19ed085a50924449",
                    "subject": "Gmail message",
                    "from": "b@example.com",
                    "date": "2026-07-01T00:00:00-04:00",
                    "read": True,
                    "flagged": False,
                    "content": "Hello from Gmail API",
                }
            },
        )
        mgr._gmail = fake_gmail

        result = mgr.read_message("19ed085a50924449")

        assert fake_gmail.get_message_calls == [("user@gmail.com", "19ed085a50924449")]
        assert result["message_id"] == "19ed085a50924449"
        assert result["subject"] == "Gmail message"
        assert result["content"] == "Hello from Gmail API"
        assert result["truncated"] is False

    def test_gmail_lookup_skips_erroring_account_and_tries_next(self):
        """A dead token (or any other per-account failure) on one
        authorized account must not abort the lookup - the next authorized
        account is tried, mirroring _call_gmail_batch's log-and-skip
        pattern used elsewhere (Pass 2 of _mark_messages etc)."""
        empty_inbox = FakeReadInboxMailbox(FakeMessageCollection([]))

        mgr = _manager()
        mgr._app = FakeReadApp(empty_inbox)
        mgr._get_accounts = lambda: []
        fake_gmail = FakeGmailForRead(
            authorized_accounts=["dead@gmail.com", "live@gmail.com"],
            raises={"dead@gmail.com": RuntimeError("token revoked")},
            results={"live@gmail.com": {"message_id": "abc123", "subject": "Found", "content": "Body"}},
        )
        mgr._gmail = fake_gmail

        result = mgr.read_message("abc123")

        assert [c[0] for c in fake_gmail.get_message_calls] == ["dead@gmail.com", "live@gmail.com"]
        assert result["subject"] == "Found"
        assert result["content"] == "Body"

    def test_not_found_anywhere_raises_mail_error(self):
        empty_inbox = FakeReadInboxMailbox(FakeMessageCollection([]))

        mgr = _manager()
        mgr._app = FakeReadApp(empty_inbox)
        mgr._get_accounts = lambda: []
        fake_gmail = FakeGmailForRead(authorized_accounts=["user@gmail.com"], results={"user@gmail.com": None})
        mgr._gmail = fake_gmail

        with pytest.raises(MailError, match="Message not found: nope"):
            mgr.read_message("nope")

    def test_content_over_max_length_is_truncated_for_gmail_source(self):
        empty_inbox = FakeReadInboxMailbox(FakeMessageCollection([]))
        long_content = "x" * 5000

        mgr = _manager()
        mgr._app = FakeReadApp(empty_inbox)
        mgr._get_accounts = lambda: []
        fake_gmail = FakeGmailForRead(
            authorized_accounts=["user@gmail.com"],
            results={"user@gmail.com": {"message_id": "abc123", "subject": "Long", "content": long_content}},
        )
        mgr._gmail = fake_gmail

        result = mgr.read_message("abc123")

        assert result["truncated"] is True
        assert result["full_length"] == 5000
        assert len(result["content"]) == 4000


class FakeGmailForBulkAction:
    """Records search_ids/search_messages_preview/batch_modify calls for
    bulk_action tests. Deliberately does NOT implement any singular
    per-message method - a regression that falls back to per-message calls
    fails loudly with an AttributeError instead of silently passing.
    """

    def __init__(self, authorized=True, gmail_ids=None, preview=None, label_ids=None, find_label_ids=None):
        self._authorized = authorized
        self._gmail_ids = gmail_ids if gmail_ids is not None else ["a", "b", "c"]
        self._preview = preview if preview is not None else {"matched_count": 3, "sample": [{"message_id": "a"}]}
        # name -> (label_id, created) - what resolve_label_id() returns for
        # a given label name; defaults to always-created if unset.
        self._label_ids = label_ids or {}
        # name -> label_id - what find_label() resolves for a given name;
        # a name absent from this dict means "not found" (returns None),
        # matching find_label()'s real never-creates contract.
        self._find_label_ids = find_label_ids or {}
        self.search_ids_calls = []
        self.preview_calls = []
        self.batch_modify_calls = []
        self.resolve_label_id_calls = []
        self.find_label_calls = []

    def is_authorized(self, email):
        return self._authorized

    def search_ids(self, email, query, max_results=None):
        self.search_ids_calls.append((email, query))
        return list(self._gmail_ids)

    def search_messages_preview(self, email, query, sample_size=10):
        self.preview_calls.append((email, query))
        return self._preview

    def batch_modify(self, email, gmail_ids, add_label_ids=None, remove_label_ids=None):
        self.batch_modify_calls.append((email, list(gmail_ids), add_label_ids, remove_label_ids))
        return list(gmail_ids)

    def resolve_label_id(self, email, name):
        self.resolve_label_id_calls.append((email, name))
        return self._label_ids.get(name, (f"Label_{name}", True))

    def find_label(self, email, name):
        self.find_label_calls.append((email, name))
        if name in self._find_label_ids:
            return {"id": self._find_label_ids[name], "name": name}
        return None


class TestBulkAction:
    """Issue #45: mail_bulk_action searches Gmail directly (native query
    syntax) and bulk archives/marks-read matches, gated by an explicit
    confirm flag.
    """

    def test_dry_run_never_calls_batch_modify_regardless_of_match_count(self):
        """The single most important guarantee per the issue's acceptance
        criteria: confirm=False must never mutate anything, no matter how
        many messages match.
        """
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=[f"id{i}" for i in range(5000)])
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=False)

        assert fake_gmail.batch_modify_calls == []
        assert len(fake_gmail.preview_calls) == 1
        assert result["matched_count"] == 3
        assert result["sample"] == [{"message_id": "a"}]
        assert result["query"] == "is:unread"

    def test_confirm_true_reruns_search_fresh_not_reusing_dry_run(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=["a", "b", "c"])
        mgr._gmail = fake_gmail

        # A dry run first, then confirm=True - confirm must issue its own
        # fresh search_ids call rather than reusing the earlier preview.
        mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=False)
        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True)

        assert len(fake_gmail.search_ids_calls) == 1
        assert len(fake_gmail.batch_modify_calls) == 1
        assert result["matched_count"] == 3
        assert result["modified_count"] == 3
        assert result["action"] == "archive"

    def test_archive_removes_inbox_and_trash_labels(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True)

        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert add_labels is None
        assert remove_labels == ["INBOX", "TRASH"]

    def test_mark_read_removes_unread_label(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        mgr.bulk_action("user@gmail.com", "is:unread", "mark_read", confirm=True)

        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert add_labels is None
        assert remove_labels == ["UNREAD"]

    def test_archive_and_mark_read_combines_labels_in_one_call(self):
        """archive_and_mark_read must be ONE batch_modify call with both
        label sets combined, not two separate calls.
        """
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        mgr.bulk_action("user@gmail.com", "is:unread", "archive_and_mark_read", confirm=True)

        assert len(fake_gmail.batch_modify_calls) == 1
        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert add_labels is None
        assert set(remove_labels) == {"INBOX", "TRASH", "UNREAD"}

    def test_invalid_action_raises_clear_error(self):
        mgr = _manager()
        mgr._gmail = FakeGmailForBulkAction()

        with pytest.raises(MailError, match="Invalid action"):
            mgr.bulk_action("user@gmail.com", "is:unread", "delete", confirm=True)

    def test_unauthorized_account_raises_clear_error_no_fallback(self):
        mgr = _manager()
        mgr._gmail = FakeGmailForBulkAction(authorized=False)

        with pytest.raises(MailError, match="not authorized for Gmail API"):
            mgr.bulk_action("someone@icloud.com", "is:unread", "archive", confirm=True)

    def test_no_gmail_client_raises_clear_error(self):
        mgr = _manager()
        mgr._gmail = None

        with pytest.raises(MailError, match="not authorized for Gmail API"):
            mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True)

    def test_execute_with_no_matches_does_not_call_batch_modify(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=[])
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "from:nobody@example.com", "archive", confirm=True)

        assert fake_gmail.batch_modify_calls == [("user@gmail.com", [], None, ["INBOX", "TRASH"])]
        assert result["matched_count"] == 0
        assert result["modified_count"] == 0

    def test_execute_with_matches_triggers_mail_app_sync(self):
        import mail_tools.mail as mail_module

        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=["a", "b"])
        mgr._gmail = fake_gmail

        with patch.object(mail_module, "_sync_mail_app") as mock_sync:
            mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True)

        mock_sync.assert_called_once()

    def test_execute_with_no_matches_does_not_trigger_mail_app_sync(self):
        import mail_tools.mail as mail_module

        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=[])
        mgr._gmail = fake_gmail

        with patch.object(mail_module, "_sync_mail_app") as mock_sync:
            mgr.bulk_action("user@gmail.com", "from:nobody@example.com", "archive", confirm=True)

        mock_sync.assert_not_called()

    # ── issue #58: custom label support ──

    def test_hold_action_with_apply_label_only_adds_label_untouched_inbox_unread(self):
        """Definition-of-Done assertion: applying a label to a hold category
        must not archive or mark-read the matches - remove_label_ids must be
        empty/omitted, and add_label_ids must be exactly the resolved label id.
        """
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(label_ids={"financial-statements": ("Label_1", False)})
        mgr._gmail = fake_gmail

        result = mgr.bulk_action(
            "user@gmail.com", "is:unread", "hold", confirm=True, apply_label="financial-statements"
        )

        assert len(fake_gmail.batch_modify_calls) == 1
        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert add_labels == ["Label_1"]
        assert not remove_labels  # empty list or None - INBOX/UNREAD must be untouched
        assert result["apply_label"] == "financial-statements"
        assert result["label_created"] is False

    def test_hold_action_with_no_apply_label_raises(self):
        mgr = _manager()
        mgr._gmail = FakeGmailForBulkAction()

        with pytest.raises(MailError, match="hold"):
            mgr.bulk_action("user@gmail.com", "is:unread", "hold", confirm=True)

    def test_hold_action_with_whitespace_only_apply_label_raises(self):
        """A whitespace-only apply_label (" ") is truthy but not a usable
        label name - must be treated the same as apply_label being omitted,
        not passed through to create_label() as a whitespace-named label.
        """
        mgr = _manager()
        mgr._gmail = FakeGmailForBulkAction()

        with pytest.raises(MailError, match="hold"):
            mgr.bulk_action("user@gmail.com", "is:unread", "hold", confirm=True, apply_label="   ")

    def test_apply_label_is_stripped_before_resolving(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(label_ids={"Financial": ("Label_1", False)})
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "hold", confirm=True, apply_label="  Financial  ")

        assert result["apply_label"] == "Financial"
        assert fake_gmail.resolve_label_id_calls == [("user@gmail.com", "Financial")]

    def test_apply_label_combined_with_archive_labels_and_removes_in_one_call(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(label_ids={"receipts": ("Label_2", True)})
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True, apply_label="receipts")

        assert len(fake_gmail.batch_modify_calls) == 1
        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert add_labels == ["Label_2"]
        assert remove_labels == ["INBOX", "TRASH"]
        assert result["apply_label"] == "receipts"
        assert result["label_created"] is True

    def test_apply_label_resolved_once_per_call(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        mgr.bulk_action("user@gmail.com", "is:unread", "hold", confirm=True, apply_label="receipts")

        assert fake_gmail.resolve_label_id_calls == [("user@gmail.com", "receipts")]

    def test_dry_run_echoes_apply_label_without_resolving_it(self):
        """A dry run must not create/resolve the label at all - confirm=False
        makes zero mutation-adjacent calls, same guarantee as no batch_modify.
        """
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "hold", confirm=False, apply_label="receipts")

        assert result["apply_label"] == "receipts"
        assert fake_gmail.resolve_label_id_calls == []
        assert fake_gmail.batch_modify_calls == []

    def test_no_apply_label_never_calls_resolve_label_id(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True)

        assert fake_gmail.resolve_label_id_calls == []
        assert "apply_label" not in result
        assert "label_created" not in result

    # ── issue #85: remove_label support ──

    def test_remove_label_missing_is_safe_noop_not_error(self):
        """A remove_label naming a label that doesn't exist must not raise
        and must not call batch_modify with anything for it - only
        find_label is called, never resolve_label_id (no creation).
        """
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True, remove_label="ghost-label")

        assert fake_gmail.find_label_calls == [("user@gmail.com", "ghost-label")]
        assert fake_gmail.resolve_label_id_calls == []
        assert result["remove_label"] == "ghost-label"
        assert result["remove_label_found"] is False
        # The action's own removals still proceed; the label id just never
        # lands in remove_label_ids.
        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert remove_labels == ["INBOX", "TRASH"]

    def test_remove_label_found_lands_in_same_batch_modify_call(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(find_label_ids={"needs-review": "Label_5"})
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True, remove_label="needs-review")

        assert len(fake_gmail.batch_modify_calls) == 1
        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert set(remove_labels) == {"INBOX", "TRASH", "Label_5"}
        assert result["remove_label"] == "needs-review"
        assert result["remove_label_found"] is True

    def test_apply_label_and_remove_label_swap_in_one_batch_modify_call(self):
        """The swap case: apply_label and remove_label together must be a
        single batchModify call with both add_label_ids and
        remove_label_ids populated.
        """
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(
            label_ids={"financial-statements": ("Label_1", False)},
            find_label_ids={"needs-review": "Label_5"},
        )
        mgr._gmail = fake_gmail

        result = mgr.bulk_action(
            "user@gmail.com",
            "is:unread",
            "hold",
            confirm=True,
            apply_label="financial-statements",
            remove_label="needs-review",
        )

        assert len(fake_gmail.batch_modify_calls) == 1
        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert add_labels == ["Label_1"]
        assert remove_labels == ["Label_5"]
        assert result["apply_label"] == "financial-statements"
        assert result["remove_label"] == "needs-review"
        assert result["remove_label_found"] is True

    def test_hold_action_with_only_remove_label_does_not_raise(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(find_label_ids={"needs-review": "Label_5"})
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "hold", confirm=True, remove_label="needs-review")

        assert result["remove_label_found"] is True
        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert remove_labels == ["Label_5"]

    def test_hold_action_with_neither_apply_label_nor_remove_label_raises(self):
        mgr = _manager()
        mgr._gmail = FakeGmailForBulkAction()

        with pytest.raises(MailError, match="hold"):
            mgr.bulk_action("user@gmail.com", "is:unread", "hold", confirm=True)

    def test_remove_label_is_stripped_before_resolving(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(find_label_ids={"needs-review": "Label_5"})
        mgr._gmail = fake_gmail

        result = mgr.bulk_action(
            "user@gmail.com", "is:unread", "archive", confirm=True, remove_label="  needs-review  "
        )

        assert result["remove_label"] == "needs-review"
        assert fake_gmail.find_label_calls == [("user@gmail.com", "needs-review")]

    def test_whitespace_only_remove_label_treated_as_absent(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True, remove_label="   ")

        assert "remove_label" not in result
        assert fake_gmail.find_label_calls == []

    def test_dry_run_reports_remove_label_found_without_mutating(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(find_label_ids={"needs-review": "Label_5"})
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=False, remove_label="needs-review")

        assert result["remove_label"] == "needs-review"
        assert result["remove_label_found"] is True
        assert fake_gmail.batch_modify_calls == []

    def test_dry_run_with_missing_remove_label_reports_false(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=False, remove_label="ghost-label")

        assert result["remove_label_found"] is False

    def test_no_remove_label_never_calls_find_label_for_it(self):
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.bulk_action("user@gmail.com", "is:unread", "archive", confirm=True)

        assert fake_gmail.find_label_calls == []
        assert "remove_label" not in result
        assert "remove_label_found" not in result


class FakeGmailForLabelManagement:
    """Records find_label/list_labels/search_messages_preview/delete_label/
    rename_label calls for mail_labels/mail_label_delete/mail_label_rename
    tests (issue #85). Deliberately does NOT implement resolve_label_id or
    create_label - delete/rename must never create a label as a side
    effect of looking one up.
    """

    def __init__(self, authorized=True, labels=None, find_label_ids=None, preview=None):
        self._authorized = authorized
        self._labels = labels if labels is not None else []
        # name -> label_id - what find_label() resolves; a name absent
        # from this dict means "not found" (returns None).
        self._find_label_ids = find_label_ids or {}
        self._preview = preview if preview is not None else {"matched_count": 0, "sample": []}
        self.list_labels_calls = []
        self.find_label_calls = []
        self.preview_calls = []
        self.delete_label_calls = []
        self.rename_label_calls = []

    def is_authorized(self, email):
        return self._authorized

    def list_labels(self, email):
        self.list_labels_calls.append(email)
        return list(self._labels)

    def find_label(self, email, name):
        self.find_label_calls.append((email, name))
        if name in self._find_label_ids:
            return {"id": self._find_label_ids[name], "name": name}
        return None

    def search_messages_preview(self, email, query, sample_size=10):
        self.preview_calls.append((email, query))
        return self._preview

    def delete_label(self, email, name):
        self.delete_label_calls.append((email, name))
        return {"id": self._find_label_ids.get(name), "name": name}

    def rename_label(self, email, old_name, new_name):
        self.rename_label_calls.append((email, old_name, new_name))
        return {"id": self._find_label_ids.get(old_name), "name": new_name}


class TestListLabels:
    """Issue #85: mail_labels wraps GmailClient.list_labels() behind the
    same Gmail-API-only guard as bulk_action().
    """

    def test_returns_labels_from_gmail_client(self):
        mgr = _manager()
        labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "Label_1", "name": "financial-statements", "type": "user"},
        ]
        mgr._gmail = FakeGmailForLabelManagement(labels=labels)

        result = mgr.list_labels("user@gmail.com")

        assert result == labels

    def test_unauthorized_account_raises_clear_error(self):
        mgr = _manager()
        mgr._gmail = FakeGmailForLabelManagement(authorized=False)

        with pytest.raises(MailError, match="not authorized for Gmail API"):
            mgr.list_labels("someone@icloud.com")

    def test_no_gmail_client_raises_clear_error(self):
        mgr = _manager()
        mgr._gmail = None

        with pytest.raises(MailError, match="not authorized for Gmail API"):
            mgr.list_labels("user@gmail.com")


class TestDeleteLabel:
    """Issue #85: mail_label_delete previews (confirm=False) or deletes
    (confirm=True) a label, and is idempotent on an already-gone name in
    either mode.
    """

    def test_confirm_false_never_calls_gmail_delete(self):
        """The structural guarantee behind 'never touches INBOX/UNREAD':
        delete_label at the GmailClient level is the only thing capable of
        a real DELETE call, and confirm=False must never reach it.
        """
        mgr = _manager()
        fake_gmail = FakeGmailForLabelManagement(
            find_label_ids={"financial-statements": "Label_1"},
            preview={"matched_count": 7, "sample": []},
        )
        mgr._gmail = fake_gmail

        result = mgr.delete_label("user@gmail.com", "financial-statements", confirm=False)

        assert fake_gmail.delete_label_calls == []
        assert result == {"label": "financial-statements", "found": True, "messages_affected": 7}

    def test_confirm_true_deletes_after_computing_messages_affected(self):
        mgr = _manager()
        fake_gmail = FakeGmailForLabelManagement(
            find_label_ids={"financial-statements": "Label_1"},
            preview={"matched_count": 7, "sample": []},
        )
        mgr._gmail = fake_gmail

        result = mgr.delete_label("user@gmail.com", "financial-statements", confirm=True)

        assert fake_gmail.preview_calls == [("user@gmail.com", 'label:"financial-statements"')]
        assert fake_gmail.delete_label_calls == [("user@gmail.com", "financial-statements")]
        assert result == {"label": "financial-statements", "deleted": True, "messages_affected": 7}

    def test_multi_word_label_name_is_quoted_in_preview_query(self):
        """A regression guard for the code-review finding: an unquoted
        `label:Needs Review` query splits into label:Needs plus a free-text
        Review term, silently mis-reporting messages_affected for any label
        name containing a space.
        """
        mgr = _manager()
        fake_gmail = FakeGmailForLabelManagement(
            find_label_ids={"Needs Review": "Label_2"},
            preview={"matched_count": 3, "sample": []},
        )
        mgr._gmail = fake_gmail

        mgr.delete_label("user@gmail.com", "Needs Review", confirm=False)

        assert fake_gmail.preview_calls == [("user@gmail.com", 'label:"Needs Review"')]

    def test_name_is_stripped_before_resolving(self):
        mgr = _manager()
        fake_gmail = FakeGmailForLabelManagement(
            find_label_ids={"financial-statements": "Label_1"},
            preview={"matched_count": 1, "sample": []},
        )
        mgr._gmail = fake_gmail

        result = mgr.delete_label("user@gmail.com", "  financial-statements  ", confirm=False)

        assert fake_gmail.find_label_calls == [("user@gmail.com", "financial-statements")]
        assert result["label"] == "financial-statements"

    def test_already_gone_name_returns_found_false_in_both_modes_no_raise(self):
        mgr = _manager()
        fake_gmail = FakeGmailForLabelManagement()
        mgr._gmail = fake_gmail

        preview_result = mgr.delete_label("user@gmail.com", "ghost-label", confirm=False)
        confirm_result = mgr.delete_label("user@gmail.com", "ghost-label", confirm=True)

        assert preview_result == {"label": "ghost-label", "found": False}
        assert confirm_result == {"label": "ghost-label", "found": False}
        assert fake_gmail.delete_label_calls == []
        assert fake_gmail.preview_calls == []

    def test_unauthorized_account_raises_clear_error(self):
        mgr = _manager()
        mgr._gmail = FakeGmailForLabelManagement(authorized=False)

        with pytest.raises(MailError, match="not authorized for Gmail API"):
            mgr.delete_label("someone@icloud.com", "financial-statements", confirm=True)


class TestRenameLabel:
    """Issue #85: mail_label_rename raises on an unresolved old_name
    (unlike delete_label's tolerant no-op) since it names a single
    specific target.
    """

    def test_not_found_old_name_raises_mail_error(self):
        mgr = _manager()
        fake_gmail = FakeGmailForLabelManagement()
        mgr._gmail = fake_gmail

        with pytest.raises(MailError, match="not found"):
            mgr.rename_label("user@gmail.com", "ghost-label", "new-name")

        assert fake_gmail.rename_label_calls == []

    def test_found_old_name_calls_through_with_correct_args(self):
        mgr = _manager()
        fake_gmail = FakeGmailForLabelManagement(find_label_ids={"financial-statements": "Label_1"})
        mgr._gmail = fake_gmail

        result = mgr.rename_label("user@gmail.com", "financial-statements", "statements")

        assert fake_gmail.rename_label_calls == [("user@gmail.com", "financial-statements", "statements")]
        assert result == {"old_name": "financial-statements", "new_name": "statements", "renamed": True}

    def test_old_and_new_names_are_stripped_before_use(self):
        mgr = _manager()
        fake_gmail = FakeGmailForLabelManagement(find_label_ids={"financial-statements": "Label_1"})
        mgr._gmail = fake_gmail

        result = mgr.rename_label("user@gmail.com", "  financial-statements  ", "  statements  ")

        assert fake_gmail.find_label_calls == [("user@gmail.com", "financial-statements")]
        assert fake_gmail.rename_label_calls == [("user@gmail.com", "financial-statements", "statements")]
        assert result == {"old_name": "financial-statements", "new_name": "statements", "renamed": True}

    def test_unauthorized_account_raises_clear_error(self):
        mgr = _manager()
        mgr._gmail = FakeGmailForLabelManagement(authorized=False)

        with pytest.raises(MailError, match="not authorized for Gmail API"):
            mgr.rename_label("someone@icloud.com", "financial-statements", "statements")


def _seed_rules(monkeypatch, tmp_path, categories):
    """Point mail_tools.rules.RULES_FILE at a tmp file and seed it - lets
    apply_rules() exercise the real load_rules()/save_rules() round trip
    instead of mocking them out.
    """
    from mail_tools import rules as rules_module

    rules_file = tmp_path / "sender_rules.json"
    monkeypatch.setattr(rules_module, "RULES_FILE", rules_file)
    data = {"version": 1, "categories": categories}
    rules_module.save_rules(data)
    return rules_file


class TestApplyRules:
    """Issue #48: mail_apply_rules iterates persisted sender-rule categories
    and calls the EXISTING bulk_action() once per targeted active category -
    no query-building or batch-modify logic reimplemented here.
    """

    def _categories(self):
        return [
            {
                "id": "retail-shipping-receipts",
                "label": "Retail receipts",
                "senders": ["from:amazon.com"],
                "default_action": "archive_and_mark_read",
                "status": "active",
                "last_reviewed": "2026-01-01",
                "review_after_days": None,
            },
            {
                "id": "financial-statements",
                "label": "Financial statements",
                "senders": ["from:bankofamerica.com"],
                "default_action": "hold",
                "status": "leave_alone",
                "last_reviewed": "2026-01-01",
                "review_after_days": 90,
            },
        ]

    def test_dry_run_never_mutates_and_is_repeatable(self, tmp_path, monkeypatch):
        """Running twice in a row must produce identical results and never
        call batch_modify - the same structural guarantee as bulk_action.
        """
        _seed_rules(monkeypatch, tmp_path, self._categories())
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=["a", "b"], preview={"matched_count": 2, "sample": []})
        mgr._gmail = fake_gmail

        result1 = mgr.apply_rules("user@gmail.com", confirm=False)
        result2 = mgr.apply_rules("user@gmail.com", confirm=False)

        assert fake_gmail.batch_modify_calls == []
        assert result1 == result2
        assert result1["categories"][0]["id"] == "retail-shipping-receipts"
        assert result1["categories"][0]["matched_count"] == 2
        assert result1["total_matched_count"] == 2

    def test_dry_run_defaults_to_active_categories_only(self, tmp_path, monkeypatch):
        _seed_rules(monkeypatch, tmp_path, self._categories())
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(preview={"matched_count": 5, "sample": []})
        mgr._gmail = fake_gmail

        result = mgr.apply_rules("user@gmail.com", confirm=False)

        ids = [c["id"] for c in result["categories"]]
        assert ids == ["retail-shipping-receipts"]
        assert result["skipped"] == []

    def test_dry_run_includes_stale_reviews_for_all_categories(self, tmp_path, monkeypatch):
        categories = self._categories()
        categories[1]["last_reviewed"] = "2025-01-01"  # 90-day window long elapsed
        _seed_rules(monkeypatch, tmp_path, categories)
        mgr = _manager()
        mgr._gmail = FakeGmailForBulkAction(preview={"matched_count": 0, "sample": []})

        result = mgr.apply_rules("user@gmail.com", confirm=False)

        stale_ids = [c["id"] for c in result["stale_reviews"]]
        assert "financial-statements" in stale_ids

    def test_confirm_true_only_mutates_active_categories(self, tmp_path, monkeypatch):
        rules_file = _seed_rules(monkeypatch, tmp_path, self._categories())
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=["a", "b"])
        mgr._gmail = fake_gmail

        result = mgr.apply_rules("user@gmail.com", confirm=True)

        # Only the active category ever reaches batch_modify.
        assert len(fake_gmail.batch_modify_calls) == 1
        assert result["categories"][0]["id"] == "retail-shipping-receipts"
        assert result["categories"][0]["modified_count"] == 2
        assert result["total_modified_count"] == 2

        # last_reviewed updated only for the executed category.
        saved = json.loads(rules_file.read_text())
        by_id = {c["id"]: c for c in saved["categories"]}
        assert by_id["retail-shipping-receipts"]["last_reviewed"] == date.today().isoformat()
        assert by_id["financial-statements"]["last_reviewed"] == "2026-01-01"

    def test_held_back_category_explicitly_named_produces_zero_mutations_and_skip_note(self, tmp_path, monkeypatch):
        """Acceptance criterion: a leave_alone category (financial-statements,
        default_action=hold) explicitly included in category_ids under
        confirm=True must produce zero mutations for it and an explicit skip
        note - while the active category alongside it still executes.
        """
        rules_file = _seed_rules(monkeypatch, tmp_path, self._categories())
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=["a", "b"])
        mgr._gmail = fake_gmail

        result = mgr.apply_rules(
            "user@gmail.com",
            category_ids="retail-shipping-receipts,financial-statements",
            confirm=True,
        )

        executed_ids = [c["id"] for c in result["categories"]]
        assert executed_ids == ["retail-shipping-receipts"]

        skipped_ids = {s["id"] for s in result["skipped"]}
        assert "financial-statements" in skipped_ids
        skip_entry = next(s for s in result["skipped"] if s["id"] == "financial-statements")
        assert "reason" in skip_entry and skip_entry["reason"]

        # Only one batch_modify call total (for the active category).
        assert len(fake_gmail.batch_modify_calls) == 1

        # financial-statements' last_reviewed must be untouched.
        saved = json.loads(rules_file.read_text())
        by_id = {c["id"]: c for c in saved["categories"]}
        assert by_id["financial-statements"]["last_reviewed"] == "2026-01-01"

    def test_status_active_but_default_action_hold_is_still_skipped(self, tmp_path, monkeypatch):
        """A category could theoretically have status=='active' but
        default_action=='hold' - that combination must still be filtered
        out before any mutation, per issue #48's explicit hold-vocabulary
        requirement. Without an apply_label (issue #58), hold has nothing
        for bulk_action to do, so it must never reach bulk_action.
        """
        categories = [
            {
                "id": "weird-hold-active",
                "label": "weird",
                "senders": ["from:example.com"],
                "default_action": "hold",
                "status": "active",
                "last_reviewed": "2026-01-01",
                "review_after_days": None,
            }
        ]
        _seed_rules(monkeypatch, tmp_path, categories)
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.apply_rules("user@gmail.com", confirm=True)

        assert result["categories"] == []
        assert fake_gmail.batch_modify_calls == []
        skip_entry = result["skipped"][0]
        assert skip_entry["id"] == "weird-hold-active"
        assert "hold" in skip_entry["reason"]

    def test_unknown_category_id_reported_as_skipped(self, tmp_path, monkeypatch):
        _seed_rules(monkeypatch, tmp_path, self._categories())
        mgr = _manager()
        mgr._gmail = FakeGmailForBulkAction()

        result = mgr.apply_rules("user@gmail.com", category_ids="ghost-category", confirm=True)

        assert result["categories"] == []
        assert result["skipped"] == [{"id": "ghost-category", "reason": "unknown category id"}]

    def test_confirm_true_reruns_search_fresh_per_category(self, tmp_path, monkeypatch):
        """bulk_action already re-runs the search fresh under confirm=True -
        apply_rules must rely on that rather than caching/reusing any
        dry-run count.
        """
        _seed_rules(monkeypatch, tmp_path, self._categories())
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=["a", "b", "c"])
        mgr._gmail = fake_gmail

        mgr.apply_rules("user@gmail.com", confirm=False)
        mgr.apply_rules("user@gmail.com", confirm=True)

        assert len(fake_gmail.search_ids_calls) == 1
        assert len(fake_gmail.batch_modify_calls) == 1

    def test_empty_senders_category_skipped_never_reaches_gmail(self, tmp_path, monkeypatch):
        """An active category with an empty `senders` list must be filtered
        out before build_category_query/bulk_action are ever called - not
        raise, not silently target every unread message. Regression test
        for the degenerate "() is:unread" query bug.
        """
        categories = [
            {
                "id": "broken-empty-senders",
                "label": "Broken",
                "senders": [],
                "default_action": "archive_and_mark_read",
                "status": "active",
                "last_reviewed": "2026-01-01",
                "review_after_days": None,
            }
        ]
        rules_file = _seed_rules(monkeypatch, tmp_path, categories)
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.apply_rules("user@gmail.com", confirm=True)

        assert result["categories"] == []
        assert fake_gmail.search_ids_calls == []
        assert fake_gmail.batch_modify_calls == []
        skip_entry = result["skipped"][0]
        assert skip_entry["id"] == "broken-empty-senders"
        assert "senders" in skip_entry["reason"]

        # last_reviewed on disk must be untouched - the category was never
        # executed.
        saved = json.loads(rules_file.read_text())
        assert saved["categories"][0]["last_reviewed"] == "2026-01-01"

    def test_missing_senders_key_category_skipped(self, tmp_path, monkeypatch):
        """Same guard as the empty-list case, but for a category dict that
        omits `senders` entirely (a plausible hand-edit mistake).
        """
        categories = [
            {
                "id": "broken-missing-senders",
                "label": "Broken",
                "default_action": "archive_and_mark_read",
                "status": "active",
                "last_reviewed": "2026-01-01",
                "review_after_days": None,
            }
        ]
        _seed_rules(monkeypatch, tmp_path, categories)
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.apply_rules("user@gmail.com", confirm=True)

        assert result["categories"] == []
        assert fake_gmail.batch_modify_calls == []
        skip_entry = result["skipped"][0]
        assert skip_entry["id"] == "broken-missing-senders"
        assert "senders" in skip_entry["reason"]

    def test_confirm_true_bumps_last_reviewed_even_with_zero_matches(self, tmp_path, monkeypatch):
        """A category that runs but matches nothing (modified_count == 0)
        still had bulk_action(confirm=True) actually executed against
        Gmail this cycle - last_reviewed reflects "was checked", not "had
        at least one message to act on". This pins down the intentional
        reading rather than leaving it an untested accident.
        """
        categories = [
            {
                "id": "zero-match-category",
                "label": "Zero match",
                "senders": ["from:never-matches.example.com"],
                "default_action": "archive_and_mark_read",
                "status": "active",
                "last_reviewed": "2026-01-01",
                "review_after_days": None,
            }
        ]
        rules_file = _seed_rules(monkeypatch, tmp_path, categories)
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=[])
        mgr._gmail = fake_gmail

        result = mgr.apply_rules("user@gmail.com", confirm=True)

        assert result["categories"][0]["modified_count"] == 0
        assert result["total_modified_count"] == 0

        saved = json.loads(rules_file.read_text())
        assert saved["categories"][0]["last_reviewed"] == date.today().isoformat()

    def test_mid_loop_exception_does_not_lose_earlier_categorys_last_reviewed(self, tmp_path, monkeypatch):
        """If bulk_action raises partway through the targeted list (e.g. a
        transient Gmail API error on category 2 of 2), category 1's
        already-executed mutation and last_reviewed bump must already be
        persisted to disk - not lost because save_rules() only ran once
        after the full loop.
        """
        categories = [
            {
                "id": "first-category",
                "label": "First",
                "senders": ["from:first.example.com"],
                "default_action": "archive_and_mark_read",
                "status": "active",
                "last_reviewed": "2026-01-01",
                "review_after_days": None,
            },
            {
                "id": "second-category",
                "label": "Second",
                "senders": ["from:second.example.com"],
                "default_action": "archive_and_mark_read",
                "status": "active",
                "last_reviewed": "2026-01-01",
                "review_after_days": None,
            },
        ]
        rules_file = _seed_rules(monkeypatch, tmp_path, categories)
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=["a"])
        mgr._gmail = fake_gmail

        original_batch_modify = fake_gmail.batch_modify
        call_count = {"n": 0}

        def _batch_modify_second_raises(email, gmail_ids, add_label_ids=None, remove_label_ids=None):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated transient Gmail API failure")
            return original_batch_modify(
                email, gmail_ids, add_label_ids=add_label_ids, remove_label_ids=remove_label_ids
            )

        fake_gmail.batch_modify = _batch_modify_second_raises

        with pytest.raises(RuntimeError, match="simulated transient Gmail API failure"):
            mgr.apply_rules("user@gmail.com", confirm=True)

        saved = json.loads(rules_file.read_text())
        by_id = {c["id"]: c for c in saved["categories"]}
        # First category's mutation already happened in Gmail (irreversible)
        # before the second category raised - its last_reviewed bump must
        # already be on disk despite the exception.
        assert by_id["first-category"]["last_reviewed"] == date.today().isoformat()
        # Second category never completed, so it's untouched.
        assert by_id["second-category"]["last_reviewed"] == "2026-01-01"

    # ── issue #58: hold + apply_label categories ──

    def test_hold_category_with_no_apply_label_is_still_skipped(self, tmp_path, monkeypatch):
        """Regression guard: existing hold behavior (no apply_label) must
        not change - a hold category with nothing to label is still a
        no-op and must never reach bulk_action.
        """
        categories = [
            {
                "id": "financial-statements",
                "label": "Financial statements",
                "senders": ["from:bankofamerica.com"],
                "default_action": "hold",
                "status": "active",
                "last_reviewed": "2026-01-01",
                "review_after_days": None,
            }
        ]
        _seed_rules(monkeypatch, tmp_path, categories)
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction()
        mgr._gmail = fake_gmail

        result = mgr.apply_rules("user@gmail.com", confirm=True)

        assert result["categories"] == []
        assert fake_gmail.batch_modify_calls == []
        skip_entry = result["skipped"][0]
        assert skip_entry["id"] == "financial-statements"
        assert "hold" in skip_entry["reason"]

    def test_hold_category_with_apply_label_is_targeted_not_skipped(self, tmp_path, monkeypatch):
        """A hold category that also carries apply_label is now a deliberate
        label-only target: it must reach bulk_action (and thus batch_modify
        with only add_label_ids set), not be filtered into `skipped`.
        """
        categories = [
            {
                "id": "financial-statements",
                "label": "Financial statements",
                "senders": ["from:bankofamerica.com"],
                "default_action": "hold",
                "status": "active",
                "apply_label": "financial-statements",
                "last_reviewed": "2026-01-01",
                "review_after_days": None,
            }
        ]
        rules_file = _seed_rules(monkeypatch, tmp_path, categories)
        mgr = _manager()
        fake_gmail = FakeGmailForBulkAction(gmail_ids=["a", "b"])
        mgr._gmail = fake_gmail

        result = mgr.apply_rules("user@gmail.com", confirm=True)

        assert result["skipped"] == []
        assert [c["id"] for c in result["categories"]] == ["financial-statements"]
        assert len(fake_gmail.batch_modify_calls) == 1
        email, ids, add_labels, remove_labels = fake_gmail.batch_modify_calls[0]
        assert add_labels == ["Label_financial-statements"]
        assert not remove_labels

        saved = json.loads(rules_file.read_text())
        by_id = {c["id"]: c for c in saved["categories"]}
        assert by_id["financial-statements"]["last_reviewed"] == date.today().isoformat()


# ── issue #50: Gmail-API-only compose/reply routing ──


class FakeGmailForSend:
    """Records send_message/create_draft/resolve_thread calls for
    compose/reply routing tests.

    Deliberately implements only these three methods - mirrors
    FakeGmailForBatching/FakeGmailForBulkAction's "raise loudly on the
    wrong call" pattern. `thread_result` controls what resolve_thread()
    returns: the default dict, or an explicit None to exercise the
    unthreaded-fallback path.
    """

    _DEFAULT_THREAD = {"gmail_id": "gid1", "thread_id": "thread1"}

    def __init__(self, thread_result=_DEFAULT_THREAD):
        self.send_calls = []
        self.draft_calls = []
        self.resolve_thread_calls = []
        self._thread_result = thread_result

    def send_message(
        self, email, to, subject, body, cc=None, bcc=None, in_reply_to=None, references=None, thread_id=None
    ):
        self.send_calls.append(
            dict(
                email=email,
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
                in_reply_to=in_reply_to,
                references=references,
                thread_id=thread_id,
            )
        )
        return {"to": to, "subject": subject, "status": "sent", "gmail_id": "gid-sent"}

    def create_draft(
        self, email, to, subject, body, cc=None, bcc=None, in_reply_to=None, references=None, thread_id=None
    ):
        self.draft_calls.append(
            dict(
                email=email,
                to=to,
                subject=subject,
                body=body,
                cc=cc,
                bcc=bcc,
                in_reply_to=in_reply_to,
                references=references,
                thread_id=thread_id,
            )
        )
        return {"to": to, "subject": subject, "status": "draft", "gmail_draft_id": "gid-draft"}

    def resolve_thread(self, email, rfc_message_id):
        self.resolve_thread_calls.append((email, rfc_message_id))
        return self._thread_result


class _AllocInit:
    """Mimics an ObjC scripting class object's alloc().initWithProperties_()
    chain, so FakeMailApp.classForScriptingClass_() can stand in for
    mail.classForScriptingClass_() without any real PyObjC/ScriptingBridge
    object.
    """

    def __init__(self, factory):
        self._factory = factory

    def alloc(self):
        return self

    def initWithProperties_(self, props):
        return self._factory(props)


class FakeRecipient:
    def __init__(self, address):
        self._address = address

    def address(self):
        return self._address


class FakeRecipientList(list):
    def addObject_(self, obj):
        self.append(obj)


class FakeOutgoingMessage:
    def __init__(self, subject, content, visible):
        self.subject_value = subject
        self.content_value = content
        self.visible = visible
        self._to = FakeRecipientList()
        self._cc = FakeRecipientList()
        self._bcc = FakeRecipientList()
        self.sender = None
        self.sent = False

    def toRecipients(self):
        return self._to

    def ccRecipients(self):
        return self._cc

    def bccRecipients(self):
        return self._bcc

    def setSender_(self, address):
        self.sender = address

    def send(self):
        self.sent = True


class FakeOutgoingMessagesCollection(list):
    def addObject_(self, obj):
        self.append(obj)


class FakeMailApp:
    """Minimal fake of SBApplication's Mail.app surface for
    compose_message()'s ScriptingBridge path - only what mail.py's
    compose_message actually calls (classForScriptingClass_,
    outgoingMessages().addObject_).
    """

    def __init__(self):
        self.outgoing = FakeOutgoingMessagesCollection()

    def classForScriptingClass_(self, name):
        if name == "outgoing message":
            return _AllocInit(
                lambda props: FakeOutgoingMessage(
                    subject=props.get("subject"), content=props.get("content"), visible=props.get("visible")
                )
            )
        if name in ("to recipient", "cc recipient", "bcc recipient"):
            return _AllocInit(lambda props: FakeRecipient(props.get("address")))
        raise AssertionError(f"unexpected scripting class: {name}")

    def outgoingMessages(self):
        return self.outgoing


class TestComposeMessageGmailRouting:
    """Issue #50: compose_message routes to the Gmail API when from_account
    resolves to a Gmail-authorized account.
    """

    def test_send_true_routes_to_gmail_send_message(self):
        mgr = _manager()
        mgr._find_account = lambda name: object()
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForSend()
        mgr._gmail = fake_gmail

        result = mgr.compose_message("to@example.com", "Subject", "Body", from_account="Gmail", send=True)

        assert len(fake_gmail.send_calls) == 1
        assert fake_gmail.draft_calls == []
        call = fake_gmail.send_calls[0]
        assert call["email"] == "user@gmail.com"
        assert call["to"] == "to@example.com"
        assert call["subject"] == "Subject"
        assert result == {"to": "to@example.com", "subject": "Subject", "status": "sent", "gmail_id": "gid-sent"}
        assert mgr._app is None  # ScriptingBridge outgoing-message path never touched

    def test_send_false_routes_to_gmail_create_draft(self):
        mgr = _manager()
        mgr._find_account = lambda name: object()
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForSend()
        mgr._gmail = fake_gmail

        result = mgr.compose_message("to@example.com", "Subject", "Body", from_account="Gmail", send=False)

        assert len(fake_gmail.draft_calls) == 1
        assert fake_gmail.send_calls == []
        assert result == {
            "to": "to@example.com",
            "subject": "Subject",
            "status": "draft",
            "gmail_draft_id": "gid-draft",
        }
        assert mgr._app is None

    def test_cc_and_bcc_passed_through_to_gmail(self):
        mgr = _manager()
        mgr._find_account = lambda name: object()
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForSend()
        mgr._gmail = fake_gmail

        mgr.compose_message(
            "to@example.com", "Subject", "Body", cc="cc@example.com", bcc="bcc@example.com", from_account="Gmail"
        )

        call = fake_gmail.draft_calls[0]
        assert call["cc"] == "cc@example.com"
        assert call["bcc"] == "bcc@example.com"

    def test_from_account_omitted_never_checks_gmail_routing(self):
        """Without from_account there is no way to know Mail.app's implicit
        default account, so Gmail routing (and account resolution) must be
        skipped entirely - the documented exception from the plan.
        """
        mgr = _manager()
        fake_app = FakeMailApp()
        mgr._app = fake_app
        mgr._find_account = lambda name: (_ for _ in ()).throw(AssertionError("must not resolve an account"))
        mgr._gmail = FakeGmailForSend()

        result = mgr.compose_message("to@example.com", "Subject", "Body", send=True)

        assert result["status"] == "sent"
        assert mgr._gmail.send_calls == []
        assert mgr._gmail.draft_calls == []


class TestComposeMessageUnresolvedFromAccount:
    """Issue #50 review finding: an explicit-but-unresolvable from_account
    must raise, not silently fall through to Mail.app's default-identity
    ScriptingBridge send.
    """

    def test_unresolvable_from_account_raises(self):
        mgr = _manager()
        mgr._find_account = lambda name: None
        mgr._gmail = FakeGmailForSend()
        fake_app = FakeMailApp()
        mgr._app = fake_app

        with pytest.raises(MailError, match="Account not found: NotARealAccount"):
            mgr.compose_message("to@example.com", "Subject", "Body", from_account="NotARealAccount")

        # Must fail before ever touching either send path.
        assert mgr._gmail.send_calls == []
        assert mgr._gmail.draft_calls == []
        assert fake_app.outgoing == []

    # Regression guard for the from_account=None path (documented
    # ScriptingBridge-default-account exception, which this fix must not
    # touch) is already covered by
    # TestComposeMessageGmailRouting.test_from_account_omitted_never_checks_gmail_routing
    # above.


class TestReplyToMessageGmailRouting:
    """Issue #50: reply_to_message routes to the Gmail API (with threading
    via resolve_thread) when the original message's account is
    Gmail-authorized.
    """

    def test_reply_to_gmail_message_uses_resolve_thread_and_send(self):
        acct = FakeAccount("Gmail")
        msg = FakeMessage("rfc-id-1", subject="Hello", sender="Alice <alice@example.com>")
        msgs = FakeMessageCollection([msg])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForSend()
        mgr._gmail = fake_gmail

        result = mgr.reply_to_message("rfc-id-1", "Reply body", send=True)

        assert fake_gmail.resolve_thread_calls == [("user@gmail.com", "rfc-id-1")]
        assert len(fake_gmail.send_calls) == 1
        call = fake_gmail.send_calls[0]
        assert call["to"] == "alice@example.com"
        assert call["subject"] == "Re: Hello"
        assert call["in_reply_to"] == "rfc-id-1"
        assert call["references"] == "rfc-id-1"
        assert call["thread_id"] == "thread1"
        assert result["status"] == "sent"
        assert mgr._app is None  # ScriptingBridge compose path never touched

    def test_reply_draft_when_send_false(self):
        acct = FakeAccount("Gmail")
        msg = FakeMessage("rfc-id-1", subject="Hello", sender="Alice <alice@example.com>")
        msgs = FakeMessageCollection([msg])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForSend()
        mgr._gmail = fake_gmail

        result = mgr.reply_to_message("rfc-id-1", "Reply body", send=False)

        assert len(fake_gmail.draft_calls) == 1
        assert fake_gmail.send_calls == []
        assert result["status"] == "draft"

    def test_falls_back_unthreaded_when_resolve_thread_returns_none(self):
        acct = FakeAccount("Gmail")
        msg = FakeMessage("rfc-id-1", subject="Hello", sender="Alice <alice@example.com>")
        msgs = FakeMessageCollection([msg])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        fake_gmail = FakeGmailForSend(thread_result=None)
        mgr._gmail = fake_gmail

        result = mgr.reply_to_message("rfc-id-1", "Reply body", send=False)

        assert len(fake_gmail.draft_calls) == 1
        call = fake_gmail.draft_calls[0]
        assert call["thread_id"] is None
        assert call["in_reply_to"] == "rfc-id-1"
        assert result["status"] == "draft"

    def test_message_not_found_raises(self):
        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: []
        mgr._get_accounts = lambda: []
        mgr._gmail = FakeGmailForSend()

        with pytest.raises(MailError, match="Message not found"):
            mgr.reply_to_message("ghost", "Reply body")


class TestICloudStaysScriptingBridgeOnly:
    """Issue #50: compose/reply routing changes must not affect non-Gmail
    (e.g. iCloud) accounts - they still go through the exact same
    ScriptingBridge outgoing-message flow as before, and never touch the
    Gmail client.
    """

    def test_compose_message_no_from_account_uses_scriptingbridge(self):
        mgr = _manager()
        fake_app = FakeMailApp()
        mgr._app = fake_app
        mgr._gmail = FakeGmailForSend()

        result = mgr.compose_message("to@example.com", "Subject", "Body", send=True)

        assert result["status"] == "sent"
        assert len(fake_app.outgoing) == 1
        assert fake_app.outgoing[0].sent is True
        assert mgr._gmail.send_calls == []
        assert mgr._gmail.draft_calls == []

    def test_compose_message_icloud_from_account_uses_scriptingbridge(self):
        icloud_acct = FakeAccount("iCloud", emails=["me@icloud.com"])

        mgr = _manager()
        fake_app = FakeMailApp()
        mgr._app = fake_app
        mgr._get_accounts = lambda: [icloud_acct]
        mgr._is_gmail_api_account = lambda a: False
        mgr._gmail = FakeGmailForSend()

        result = mgr.compose_message("to@example.com", "Subject", "Body", from_account="iCloud", send=False)

        assert result["status"] == "draft"
        assert len(fake_app.outgoing) == 1
        assert fake_app.outgoing[0].sender == "me@icloud.com"
        assert mgr._gmail.send_calls == []
        assert mgr._gmail.draft_calls == []

    def test_reply_to_message_icloud_account_uses_scriptingbridge(self):
        acct = FakeAccount("iCloud")
        msg = FakeMessage("rfc-id-2", subject="Hi", sender="Bob <bob@example.com>")
        msgs = FakeMessageCollection([msg])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: False
        fake_app = FakeMailApp()
        mgr._app = fake_app
        mgr._gmail = FakeGmailForSend()

        result = mgr.reply_to_message("rfc-id-2", "Reply body", send=True)

        assert result["status"] == "sent"
        assert len(fake_app.outgoing) == 1
        assert mgr._gmail.send_calls == []
        assert mgr._gmail.draft_calls == []

    def test_list_messages_icloud_account_uses_scriptingbridge(self):
        icloud_acct = FakeAccount("iCloud", emails=["me@icloud.com"])
        msgs = FakeMessageCollection([FakeMessage("m1", subject="Hi", date_ts=100)])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(icloud_acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: False
        mgr._gmail = FakeGmailForListSearch()

        results = mgr.list_messages(mailbox="INBOX", account=None, limit=20)

        assert [r["message_id"] for r in results] == ["m1"]
        assert results[0]["account"] == "iCloud"
        assert mgr._gmail.list_calls == []

    def test_search_messages_icloud_account_uses_scriptingbridge(self):
        icloud_acct = FakeAccount("iCloud", emails=["me@icloud.com"])
        msgs = FakeMessageCollection([FakeMessage("m1", subject="needle here")])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(icloud_acct, FakeMailbox(msgs))]
        mgr._is_gmail_api_account = lambda a: False
        mgr._gmail = FakeGmailForListSearch()

        results = mgr.search_messages("needle", mailbox="INBOX", account=None, limit=20)

        assert [r["message_id"] for r in results] == ["m1"]
        assert results[0]["account"] == "iCloud"
        assert mgr._gmail.search_calls == []


class FakeGmailForListSearch:
    """Records list_messages/search_messages calls for mail_list/mail_search
    Gmail routing tests (issue #50).

    Deliberately implements only these two methods - same "raise loudly on
    the wrong call" pattern as FakeGmailForBatching/FakeGmailForSend: if
    regressed code calls some other GmailClient method here, the test fails
    with an AttributeError instead of silently passing.
    """

    def __init__(self, list_results=None, search_results=None):
        self.list_calls = []
        self.search_calls = []
        self._list_results = list_results if list_results is not None else []
        self._search_results = search_results if search_results is not None else []

    def list_messages(self, email, mailbox="INBOX", limit=20):
        self.list_calls.append({"email": email, "mailbox": mailbox, "limit": limit})
        return [dict(r) for r in self._list_results]

    def search_messages(self, email, query, limit=20):
        self.search_calls.append({"email": email, "query": query, "limit": limit})
        return [dict(r) for r in self._search_results]


class RaisingMailbox(FakeMailbox):
    """A FakeMailbox whose .messages() raises if invoked.

    Used in Gmail-routing tests to prove a Gmail-authorized account's
    inbox_mb is never touched via ScriptingBridge: a regression back to
    inbox_mb.messages() fails the test loudly (AssertionError) instead of
    silently passing just because the fake Gmail results happen to look
    the same shape as a ScriptingBridge fetch would have produced.
    """

    def messages(self):
        raise AssertionError("ScriptingBridge .messages() must not be called for a Gmail-authorized account")


class SlowMailbox(FakeMailbox):
    """A FakeMailbox whose .messages() sleeps past a per-account timeout.

    Used to prove list_messages()'s and search_messages()'s unified
    (all-accounts) branches don't let one hung account fetch wipe out the
    other accounts' results - see _call_with_timeout in mail.py.
    """

    def __init__(self, messages, delay, name="INBOX"):
        super().__init__(messages, name=name)
        self._delay = delay

    def messages(self):
        time.sleep(self._delay)
        return super().messages()


class TestListMessagesGmailRouting:
    """Issue #50: list_messages() routes Gmail-authorized accounts through
    GmailClient.list_messages() instead of ScriptingBridge, for the unified
    inbox, single-account INBOX, and explicit-account arbitrary-mailbox cases.
    """

    def test_unified_inbox_gmail_account_uses_api_not_scriptingbridge(self, caplog):
        acct = FakeAccount("Gmail")
        fake_gmail = FakeGmailForListSearch(
            list_results=[
                {
                    "message_id": "g1",
                    "subject": "Hi",
                    "from": "a@gmail.com",
                    "date": "2026-01-01T00:00:00",
                    "read": False,
                    "flagged": False,
                }
            ]
        )

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, RaisingMailbox(FakeMessageCollection([])))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        with caplog.at_level("WARNING", logger="mail_tools.mail"):
            results = mgr.list_messages(mailbox="INBOX", account=None, limit=20)

        assert len(fake_gmail.list_calls) == 1
        assert fake_gmail.list_calls[0] == {"email": "user@gmail.com", "mailbox": "INBOX", "limit": 20}
        assert [r["message_id"] for r in results] == ["g1"]
        assert results[0]["account"] == "Gmail"
        # A regression back to RaisingMailbox.messages() is caught and
        # logged as a per-account warning by the unified branch's timeout
        # helper, not re-raised - so a bare "did the test pass" check alone
        # wouldn't catch that regression loudly. Assert no such warning was
        # logged, so a regression fails clearly here instead of silently.
        assert not caplog.records, f"unexpected warning(s) logged: {[r.message for r in caplog.records]}"

    def test_account_scoped_inbox_gmail_account_uses_api(self):
        acct = FakeAccount("Gmail")
        fake_gmail = FakeGmailForListSearch(
            list_results=[
                {
                    "message_id": "g1",
                    "subject": "Hi",
                    "from": "a@gmail.com",
                    "date": "2026-01-01T00:00:00",
                    "read": True,
                    "flagged": False,
                }
            ]
        )

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, RaisingMailbox(FakeMessageCollection([])))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        results = mgr.list_messages(mailbox="INBOX", account="Gmail", limit=20)

        assert len(fake_gmail.list_calls) == 1
        assert results[0]["account"] == "Gmail"

    def test_arbitrary_mailbox_gmail_account_uses_api(self):
        acct = FakeAccount("Gmail", emails=["user@gmail.com"])
        fake_gmail = FakeGmailForListSearch(
            list_results=[
                {
                    "message_id": "g1",
                    "subject": "Hi",
                    "from": "a@gmail.com",
                    "date": "2026-01-01T00:00:00",
                    "read": True,
                    "flagged": False,
                }
            ]
        )

        mgr = _manager()
        mgr._find_account = lambda name: acct
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._get_mailbox_messages = lambda mailbox, account: (_ for _ in ()).throw(
            AssertionError("ScriptingBridge mailbox lookup must not be called for a Gmail-authorized account")
        )
        mgr._gmail = fake_gmail

        results = mgr.list_messages(mailbox="Important", account="Gmail", limit=20)

        assert fake_gmail.list_calls[0]["mailbox"] == "Important"
        assert results[0]["account"] == "Gmail"

    def test_unread_only_pads_gmail_fetch_limit(self):
        acct = FakeAccount("Gmail")
        fake_gmail = FakeGmailForListSearch(list_results=[])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, RaisingMailbox(FakeMessageCollection([])))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        mgr.list_messages(mailbox="INBOX", account=None, limit=10, unread_only=True)

        assert fake_gmail.list_calls[0]["limit"] == 50  # min(10 * 5, 200) pad

    def test_unread_only_false_passes_limit_through_unpadded(self):
        acct = FakeAccount("Gmail")
        fake_gmail = FakeGmailForListSearch(list_results=[])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, RaisingMailbox(FakeMessageCollection([])))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        mgr.list_messages(mailbox="INBOX", account=None, limit=10, unread_only=False)

        assert fake_gmail.list_calls[0]["limit"] == 10


class TestSearchMessagesGmailRouting:
    """Issue #50: search_messages() routes Gmail-authorized accounts through
    GmailClient.search_messages() for the INBOX case - Gmail's own
    full-text query engine, not the ScriptingBridge substring match.
    """

    def test_unified_inbox_gmail_account_uses_api_not_scriptingbridge(self, caplog):
        acct = FakeAccount("Gmail")
        fake_gmail = FakeGmailForListSearch(
            search_results=[
                {
                    "message_id": "g1",
                    "subject": "Found via body text",
                    "from": "a@gmail.com",
                    "date": "2026-01-01T00:00:00",
                    "read": False,
                    "flagged": False,
                }
            ]
        )

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, RaisingMailbox(FakeMessageCollection([])))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        with caplog.at_level("WARNING", logger="mail_tools.mail"):
            results = mgr.search_messages("needle", mailbox="INBOX", account=None, limit=20)

        assert len(fake_gmail.search_calls) == 1
        assert fake_gmail.search_calls[0] == {"email": "user@gmail.com", "query": "needle", "limit": 20}
        assert [r["message_id"] for r in results] == ["g1"]
        assert results[0]["account"] == "Gmail"
        # Same rationale as the equivalent list_messages() test above: the
        # unified branch's timeout helper catches-and-logs a per-account
        # exception rather than propagating it, so a regression back to
        # RaisingMailbox.messages() would otherwise be swallowed quietly.
        assert not caplog.records, f"unexpected warning(s) logged: {[r.message for r in caplog.records]}"

    def test_account_scoped_inbox_gmail_account_uses_api(self):
        acct = FakeAccount("Gmail")
        fake_gmail = FakeGmailForListSearch(
            search_results=[
                {
                    "message_id": "g1",
                    "subject": "x",
                    "from": "a@gmail.com",
                    "date": None,
                    "read": True,
                    "flagged": False,
                }
            ]
        )

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, RaisingMailbox(FakeMessageCollection([])))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        results = mgr.search_messages("needle", mailbox="INBOX", account="Gmail", limit=20)

        assert len(fake_gmail.search_calls) == 1
        assert results[0]["account"] == "Gmail"

    def test_account_scoped_inbox_not_found_raises(self):
        """Mirrors list_messages()'s test_account_scoped_inbox_not_found_raises
        (TestListMessagesFilterAfterFetch): the error must name the missing
        account, not just repeat "INBOX" (which obviously exists as a
        concept and isn't itself the missing thing).
        """
        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: []

        try:
            mgr.search_messages("needle", mailbox="INBOX", account="Ghost")
        except Exception as e:
            assert "Ghost" in str(e)
        else:
            raise AssertionError("expected MailError for missing account INBOX")

    def test_non_inbox_mailbox_gmail_account_still_uses_scriptingbridge(self):
        """GmailClient.search_messages has no mailbox-scoping param, so a
        non-INBOX mailbox search on a Gmail account stays on ScriptingBridge
        - a documented scope limitation (issue #50), not a bug.
        """
        msgs = FakeMessageCollection([FakeMessage("m1", subject="needle here")])
        mb = FakeMailbox(msgs, name="Sent")

        mgr = _manager()
        mgr._find_mailbox = lambda mailbox, account: mb
        mgr._is_gmail_api_account = lambda a: True
        mgr._gmail = FakeGmailForListSearch()

        results = mgr.search_messages("needle", mailbox="Sent", account="Gmail", limit=20)

        assert mgr._gmail.search_calls == []
        assert [r["message_id"] for r in results] == ["m1"]

    def test_unified_inbox_results_sorted_by_date_descending_then_sliced_to_limit(self):
        """Mirrors TestListMessagesFilterAfterFetch's equivalent list_messages()
        test: a multi-account unified search must re-rank by date before
        slicing to `limit`, not just concatenate per-account results in
        account-iteration order.
        """
        import datetime as dt

        ts_2025 = dt.datetime(2025, 1, 1).timestamp()  # oldest
        ts_2027 = dt.datetime(2027, 1, 1).timestamp()  # newest

        sb_acct = FakeAccount("iCloud")
        sb_inbox = FakeMessageCollection(
            [
                FakeMessage("sb_old", subject="needle", date_ts=ts_2025),
                FakeMessage("sb_new", subject="needle", date_ts=ts_2027),
            ]
        )

        gmail_acct = FakeAccount("Gmail")
        fake_gmail = FakeGmailForListSearch(
            search_results=[
                {
                    "message_id": "g_mid",
                    "subject": "needle",
                    "from": "a@gmail.com",
                    "date": "2026-01-01T00:00:00",  # between sb_old and sb_new
                    "read": False,
                    "flagged": False,
                },
            ]
        )

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [
            (sb_acct, FakeMailbox(sb_inbox)),
            (gmail_acct, RaisingMailbox(FakeMessageCollection([]))),
        ]

        def _is_gmail(a):
            return a is gmail_acct

        mgr._is_gmail_api_account = _is_gmail
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        results = mgr.search_messages("needle", mailbox="INBOX", account=None, limit=2)

        # sb_new (2027) newest, then g_mid (2026-01-01); sb_old (2025)
        # dropped by the limit=2 slice.
        assert [r["message_id"] for r in results] == ["sb_new", "g_mid"]

    def test_query_forwarded_unchanged_no_substring_wrapping(self):
        """The query string is passed straight through to Gmail's own
        search - not turned into a subject/sender substring filter like the
        ScriptingBridge path does.
        """
        acct = FakeAccount("Gmail")
        fake_gmail = FakeGmailForListSearch(search_results=[])

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [(acct, RaisingMailbox(FakeMessageCollection([])))]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        mgr.search_messages("from:boss@example.com has:attachment", mailbox="INBOX", limit=5)

        assert fake_gmail.search_calls[0]["query"] == "from:boss@example.com has:attachment"


class FakeGmailForUnreadCounts:
    """Records get_unread_counts()/is_authorized() calls for mail_unread
    Gmail routing tests (issue #101, reopened 2026-07-09).

    Deliberately implements only these two methods - same "raise loudly on
    the wrong call" pattern as FakeGmailForListSearch: if regressed code
    calls some other GmailClient method here, the test fails with an
    AttributeError instead of silently passing.

    `authorized_emails` backs is_authorized() - the fast path added for the
    reopened fix checks this directly (mirroring gmail_tokens.json) before
    ever touching self._get_accounts()/ScriptingBridge. Defaults to empty,
    so existing account-name-filter tests (which pass a Mail.app display
    name, not an email) correctly fall through to the ScriptingBridge loop
    unchanged.
    """

    def __init__(self, results=None, raises=None, authorized_emails=None):
        self.calls = []
        self._results = results if results is not None else []
        self._raises = raises
        self._authorized_emails = set(authorized_emails or [])

    def is_authorized(self, email):
        return email in self._authorized_emails

    def get_unread_counts(self, email):
        self.calls.append(email)
        if self._raises is not None:
            raise self._raises
        return [dict(r) for r in self._results]


class TestGetUnreadCountGmailRouting:
    """Issue #101: get_unread_count() routes Gmail-authorized accounts
    through GmailClient.get_unread_counts() instead of ScriptingBridge's
    mb.unreadCount(), which reads Mail.app's locally IMAP-synced mailbox
    state and can lag behind Gmail's actual server-side unread state.
    """

    def test_gmail_account_uses_api_not_scriptingbridge(self):
        acct = FakeAccount("Gmail", emails=["user@gmail.com"])
        acct.mailboxes = lambda: (_ for _ in ()).throw(
            AssertionError("ScriptingBridge mailboxes() must not be called for a Gmail-authorized account")
        )
        fake_gmail = FakeGmailForUnreadCounts(results=[{"mailbox": "INBOX", "unread": 2}])

        mgr = _manager()
        mgr._get_accounts = lambda: [acct]
        mgr._is_gmail_api_account = lambda a: True
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        results = mgr.get_unread_count()

        assert fake_gmail.calls == ["user@gmail.com"]
        assert results == [
            {"account": "user@gmail.com", "total_unread": 2, "mailboxes": [{"mailbox": "INBOX", "unread": 2}]}
        ]

    def test_icloud_account_uses_scriptingbridge(self):
        acct = FakeAccount("iCloud", emails=["me@icloud.com"])
        acct.mailboxes = lambda: [FakeMailboxWithUnread("INBOX", 3), FakeMailboxWithUnread("Sent", 0)]
        fake_gmail = FakeGmailForUnreadCounts()

        mgr = _manager()
        mgr._get_accounts = lambda: [acct]
        mgr._is_gmail_api_account = lambda a: False
        mgr._gmail = fake_gmail

        results = mgr.get_unread_count()

        assert fake_gmail.calls == []
        assert results == [{"account": "iCloud", "total_unread": 3, "mailboxes": [{"mailbox": "INBOX", "unread": 3}]}]

    def test_gmail_account_error_is_logged_and_skipped(self):
        """A dead/expired token (or any other per-account failure) is
        swallowed via _call_gmail_batch, same as archive/delete/mark, so
        one broken Gmail account doesn't abort results for other accounts.
        """
        gmail_acct = FakeAccount("Gmail", emails=["user@gmail.com"])
        gmail_acct.mailboxes = lambda: (_ for _ in ()).throw(AssertionError("must not fall back to ScriptingBridge"))
        icloud_acct = FakeAccount("iCloud", emails=["me@icloud.com"])
        icloud_acct.mailboxes = lambda: [FakeMailboxWithUnread("INBOX", 7)]
        fake_gmail = FakeGmailForUnreadCounts(raises=RuntimeError("token revoked"))

        mgr = _manager()
        mgr._get_accounts = lambda: [gmail_acct, icloud_acct]

        def _is_gmail(a):
            return a is gmail_acct

        mgr._is_gmail_api_account = _is_gmail
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        results = mgr.get_unread_count()

        by_account = {r["account"]: r for r in results}
        assert by_account["user@gmail.com"]["total_unread"] == 0
        assert by_account["user@gmail.com"]["mailboxes"] == []
        assert by_account["iCloud"]["total_unread"] == 7

    def test_account_filter_scopes_to_one_account(self):
        gmail_acct = FakeAccount("Gmail", emails=["user@gmail.com"])
        gmail_acct.mailboxes = lambda: (_ for _ in ()).throw(AssertionError("must not use ScriptingBridge"))
        icloud_acct = FakeAccount("iCloud", emails=["me@icloud.com"])
        icloud_acct.mailboxes = lambda: [FakeMailboxWithUnread("INBOX", 9)]
        fake_gmail = FakeGmailForUnreadCounts(results=[{"mailbox": "INBOX", "unread": 4}])

        mgr = _manager()
        mgr._get_accounts = lambda: [gmail_acct, icloud_acct]

        def _is_gmail(a):
            return a is gmail_acct

        mgr._is_gmail_api_account = _is_gmail
        mgr._get_account_email = lambda a: "user@gmail.com"
        mgr._gmail = fake_gmail

        results = mgr.get_unread_count(account="Gmail")

        assert len(results) == 1
        assert results[0]["account"] == "user@gmail.com"
        assert fake_gmail.calls == ["user@gmail.com"]


class TestGetUnreadCountGmailEmailFilterNeverTouchesScriptingBridge:
    """Issue #101, reopened 2026-07-09: the first fix (cdb61e3) still
    called self._get_accounts() - a ScriptingBridge property access that
    auto-launches Mail.app - unconditionally, before ever checking whether
    the requested account was Gmail-authorized. When `account` is passed
    and is itself a Gmail-authorized email (checked directly against
    gmail_tokens.json via GmailClient.is_authorized()), get_unread_count()
    must resolve entirely through GmailClient and never call
    self._get_accounts()/self.app (ScriptingBridge/Mail.app) at all.
    """

    def test_email_filter_never_calls_get_accounts_or_scriptingbridge(self):
        fake_gmail = FakeGmailForUnreadCounts(
            results=[{"mailbox": "IMPORTANT", "unread": 77}],
            authorized_emails=["user@gmail.com"],
        )

        mgr = _manager()
        mgr._gmail = fake_gmail
        mgr._get_accounts = lambda: (_ for _ in ()).throw(
            AssertionError("self._get_accounts()/ScriptingBridge must not run for a Gmail-authorized email filter")
        )

        with patch("mail_tools.mail.SBApplication") as mock_sb_application:
            results = mgr.get_unread_count(account="user@gmail.com")

        mock_sb_application.applicationWithBundleIdentifier_.assert_not_called()
        assert fake_gmail.calls == ["user@gmail.com"]
        assert results == [
            {"account": "user@gmail.com", "total_unread": 77, "mailboxes": [{"mailbox": "IMPORTANT", "unread": 77}]}
        ]

    def test_email_filter_zero_unread_returns_empty_list(self):
        fake_gmail = FakeGmailForUnreadCounts(results=[], authorized_emails=["user@gmail.com"])

        mgr = _manager()
        mgr._gmail = fake_gmail
        mgr._get_accounts = lambda: (_ for _ in ()).throw(AssertionError("must not use ScriptingBridge"))

        results = mgr.get_unread_count(account="user@gmail.com")

        assert results == []

    def test_email_filter_dead_token_degrades_to_empty_not_raise(self):
        """A dead/expired Gmail token is logged and skipped via
        _call_gmail_batch, same as every other Gmail-routed operation in
        this file - it must not raise or fall back to ScriptingBridge.
        """
        fake_gmail = FakeGmailForUnreadCounts(
            raises=RuntimeError("token revoked"), authorized_emails=["user@gmail.com"]
        )

        mgr = _manager()
        mgr._gmail = fake_gmail
        mgr._get_accounts = lambda: (_ for _ in ()).throw(AssertionError("must not fall back to ScriptingBridge"))

        results = mgr.get_unread_count(account="user@gmail.com")

        assert results == []

    def test_non_gmail_email_filter_still_falls_back_to_scriptingbridge(self):
        """An `account` filter that isn't a Gmail-authorized email (e.g. an
        iCloud address, or a Mail.app display name) can't be resolved
        without ScriptingBridge in the first place - unaffected by this
        fast path, unchanged from prior behavior.
        """
        icloud_acct = FakeAccount("iCloud", emails=["me@icloud.com"])
        icloud_acct.mailboxes = lambda: [FakeMailboxWithUnread("INBOX", 5)]
        fake_gmail = FakeGmailForUnreadCounts(authorized_emails=["user@gmail.com"])

        mgr = _manager()
        mgr._get_accounts = lambda: [icloud_acct]
        mgr._is_gmail_api_account = lambda a: False
        mgr._gmail = fake_gmail

        results = mgr.get_unread_count(account="me@icloud.com")

        assert fake_gmail.calls == []
        assert results == [{"account": "iCloud", "total_unread": 5, "mailboxes": [{"mailbox": "INBOX", "unread": 5}]}]


class TestUnifiedBranchTimeoutProtection:
    """Issue #50 code review fix: list_messages()'s and search_messages()'s
    unified (all-accounts) branches both route through the shared
    _call_with_timeout() helper so one hung/slow account fetch (e.g. a
    Gmail account mid-sync hanging on arrayByApplyingSelector_) can't wipe
    out the other accounts' results, and - since _call_with_timeout()
    shuts its executor down with wait=False - can't block the caller past
    the configured timeout either. Each test asserts on elapsed wall-clock
    time as well as on final results/logging, so a regression back to a
    blocking executor.shutdown(wait=True) fails these tests, not just a
    slower manual repro.

    Forces a tiny real timeout via _fast_timeout() so these tests stay fast
    while still exercising the real ThreadPoolExecutor/timeout code path.

    Timing margins matter here. The per-account timeout is 0.05s, so a correct
    implementation returns at ~0.05s plus scheduler overhead, while a regression
    to shutdown(wait=True) blocks for the hung account's full sleep. These two
    outcomes must stay far enough apart that a loaded CI runner cannot blur them:
    an earlier version slept 0.3s and asserted < 0.2s, which failed on a macOS
    runner at 0.211s -- 11ms of jitter, not a regression. Sleeping HANG_DELAY and
    asserting at half of it keeps the correct path ~2x under the bar even with
    the worst jitter observed, while a regression overshoots by 2x. Widening the
    gap costs nothing: the hung worker sleeps on a background thread the caller
    has already abandoned, so it does not extend the test's own runtime.
    """

    # Sleep of the deliberately-hung account, and the bar the caller must beat.
    HANG_DELAY = 1.0
    RETURNS_EARLY_UNDER = HANG_DELAY / 2

    @staticmethod
    def _fast_timeout(mgr, timeout=0.05):
        """Monkeypatch mgr._call_with_timeout to always use a tiny timeout,
        regardless of what list_messages()/search_messages() pass - keeps
        the real timeout/logging behavior, but fast enough for a unit test.
        """
        real = MailManager._call_with_timeout

        def _patched(self, fn, acct_name, timeout=timeout, action="fetching"):
            return real(self, fn, acct_name, timeout=timeout, action=action)

        mgr._call_with_timeout = types.MethodType(_patched, mgr)

    def test_list_messages_hung_account_does_not_wipe_out_other_results(self, caplog):
        slow_acct = FakeAccount("Slow")
        slow_inbox = SlowMailbox(FakeMessageCollection([FakeMessage("slow1")]), delay=self.HANG_DELAY)

        fast_acct = FakeAccount("Fast")
        fast_inbox = FakeMailbox(FakeMessageCollection([FakeMessage("fast1", date_ts=100)]))

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [
            (slow_acct, slow_inbox),
            (fast_acct, fast_inbox),
        ]
        self._fast_timeout(mgr)

        with caplog.at_level("WARNING", logger="mail_tools.mail"):
            start = time.monotonic()
            results = mgr.list_messages(mailbox="INBOX", account=None, limit=20)
            elapsed = time.monotonic() - start

        assert [r["message_id"] for r in results] == ["fast1"]
        assert any("Timeout fetching INBOX for Slow" in r.message for r in caplog.records)
        # Proves the caller actually returns once the timeout elapses, rather
        # than blocking on ThreadPoolExecutor.__exit__'s wait=True shutdown
        # until the hung worker thread's HANG_DELAY sleep finishes.
        assert elapsed < self.RETURNS_EARLY_UNDER, (
            f"call blocked for {elapsed:.3f}s - did not return before the hung account finished "
            f"(timeout is 0.05s, hung account sleeps {self.HANG_DELAY}s)"
        )

    def test_search_messages_hung_account_does_not_wipe_out_other_results(self, caplog):
        slow_acct = FakeAccount("Slow")
        slow_inbox = SlowMailbox(FakeMessageCollection([FakeMessage("slow1", subject="needle")]), delay=self.HANG_DELAY)

        fast_acct = FakeAccount("Fast")
        fast_inbox = FakeMailbox(FakeMessageCollection([FakeMessage("fast1", subject="needle", date_ts=100)]))

        mgr = _manager()
        mgr._get_account_inboxes = lambda account_name=None: [
            (slow_acct, slow_inbox),
            (fast_acct, fast_inbox),
        ]
        self._fast_timeout(mgr)

        with caplog.at_level("WARNING", logger="mail_tools.mail"):
            start = time.monotonic()
            results = mgr.search_messages("needle", mailbox="INBOX", account=None, limit=20)
            elapsed = time.monotonic() - start

        assert [r["message_id"] for r in results] == ["fast1"]
        assert any("Timeout searching INBOX for Slow" in r.message for r in caplog.records)
        # Proves the caller actually returns once the timeout elapses, rather
        # than blocking on ThreadPoolExecutor.__exit__'s wait=True shutdown
        # until the hung worker thread's HANG_DELAY sleep finishes.
        assert elapsed < self.RETURNS_EARLY_UNDER, (
            f"call blocked for {elapsed:.3f}s - did not return before the hung account finished "
            f"(timeout is 0.05s, hung account sleeps {self.HANG_DELAY}s)"
        )
