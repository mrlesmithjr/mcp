"""Tests for RemindersManager EventKit fetch timeout handling (issue #65).

Simulates a hung fetchRemindersMatchingPredicate_completion_ call (the
completion callback never fires, as happens when the CalendarAgent daemon
stalls) and confirms each of the 4 call sites raises RemindersError instead
of silently returning an empty/partial result. threading.Event.wait is
patched to return False immediately so the test does not block for the
real 30-second timeout.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from apple_eventkit_tools.reminders import RemindersError, RemindersManager


def _make_hung_store():
    """Build a mock EKEventStore whose fetch call never invokes its callback,
    simulating a hung EventKit predicate fetch.
    """
    store = MagicMock()
    store.fetchRemindersMatchingPredicate_completion_.side_effect = lambda predicate, callback: None
    store.calendarsForEntityType_.return_value = []
    return store


@pytest.fixture(autouse=True)
def _no_real_wait():
    """Patch threading.Event.wait to return False immediately instead of
    blocking for the real 30-second timeout used by each call site.
    """
    with patch.object(threading.Event, "wait", return_value=False):
        yield


class TestFetchTimeout:
    """Each of the 4 predicate-fetch call sites must raise RemindersError
    when the completion callback never fires, rather than returning an
    empty/partial result silently.
    """

    def test_list_reminders_raises_on_timeout(self):
        mgr = RemindersManager()
        mgr._store = _make_hung_store()

        with pytest.raises(RemindersError, match="timed out"):
            mgr.list_reminders()

    def test_find_reminder_fresh_raises_on_timeout(self):
        mgr = RemindersManager()
        mgr._store = _make_hung_store()

        with pytest.raises(RemindersError, match="timed out"):
            mgr._find_reminder_fresh("some-reminder-id")

    def test_overdue_reminders_raises_on_timeout(self):
        mgr = RemindersManager()
        mgr._store = _make_hung_store()

        with pytest.raises(RemindersError, match="timed out"):
            mgr.overdue_reminders()

    def test_search_reminders_raises_on_timeout(self):
        mgr = RemindersManager()
        mgr._store = _make_hung_store()

        with pytest.raises(RemindersError, match="timed out"):
            mgr.search_reminders("groceries")
