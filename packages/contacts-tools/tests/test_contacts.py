"""Tests for ContactsManager enumeration early-stop behavior.

These exercise ContactsManager.list_contacts and .search_contacts directly,
with the CNContactStore mocked out, to prove the enumeration callback halts
at `limit` rather than walking the entire contact store.
"""

from unittest.mock import MagicMock, patch

from contacts_tools.contacts import ContactsManager

TOTAL_CONTACTS = 150


def _make_fake_store(enum_cb_holder):
    """Mock CNContactStore driving enum_cb over fakes; stop=None always, matching real PyObjC (see issue #80)."""
    store = MagicMock()

    def fake_enumerate(request, error, enum_cb):
        enum_cb_holder["call_count"] = 0
        for i in range(TOTAL_CONTACTS):
            enum_cb_holder["call_count"] += 1
            contact = MagicMock()
            contact.identifier.return_value = f"id-{i}"
            enum_cb(contact, None)

    store.enumerateContactsWithFetchRequest_error_usingBlock_.side_effect = fake_enumerate
    return store


@patch("contacts_tools.contacts._serialize_contact")
def test_list_contacts_stops_enumeration_at_limit(mock_serialize):
    """contact_list(limit=10) against 150 contacts must not enumerate them all."""
    mock_serialize.side_effect = lambda c, **kw: {"id": c.identifier(), "name": c.identifier()}

    call_tracker = {}
    mgr = ContactsManager()
    mgr._store = _make_fake_store(call_tracker)

    results = mgr.list_contacts(limit=10)

    assert len(results) == 10
    assert call_tracker["call_count"] <= 11, (
        f"enum_cb was invoked {call_tracker['call_count']} times; "
        "expected early stop near the limit, not a full-store walk"
    )
    assert call_tracker["call_count"] < TOTAL_CONTACTS


@patch("contacts_tools.contacts._serialize_contact")
def test_search_contacts_stops_enumeration_at_limit(mock_serialize):
    """Sibling function: search_contacts already early-stops (regression guard)."""
    mock_serialize.side_effect = lambda c, **kw: {"id": c.identifier(), "name": c.identifier()}

    call_tracker = {}
    mgr = ContactsManager()
    store = _make_fake_store(call_tracker)
    # No name-predicate matches, forcing the email/phone enumeration fallback.
    store.unifiedContactsMatchingPredicate_keysToFetch_error_.return_value = (None, None)
    mgr._store = store

    # Every fake contact's organization matches the query, so every enum_cb
    # invocation appends a result and the limit is reached well before
    # TOTAL_CONTACTS is enumerated.
    def fake_enumerate(request, error, enum_cb):
        call_tracker["call_count"] = 0
        for i in range(TOTAL_CONTACTS):
            call_tracker["call_count"] += 1
            contact = MagicMock()
            contact.identifier.return_value = f"id-{i}"
            contact.organizationName.return_value = "megacorp"
            enum_cb(contact, None)

    store.enumerateContactsWithFetchRequest_error_usingBlock_.side_effect = fake_enumerate

    results = mgr.search_contacts("megacorp", limit=10)

    assert len(results) == 10
    assert call_tracker["call_count"] <= 11
    assert call_tracker["call_count"] < TOTAL_CONTACTS
