"""Contacts framework bridge - read Apple Contacts via PyObjC.

Uses the native CNContactStore framework for fast, direct access to contacts.
Does NOT require Contacts.app to be running. macOS will prompt for Contacts
access on first use (TCC permission).
"""

import hashlib
import json as _json
import logging
import threading
import time as _time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import Contacts

logger = logging.getLogger(__name__)

# Keys to fetch for list/search results
_SUMMARY_KEYS = [
    Contacts.CNContactIdentifierKey,
    Contacts.CNContactGivenNameKey,
    Contacts.CNContactFamilyNameKey,
    Contacts.CNContactOrganizationNameKey,
    Contacts.CNContactJobTitleKey,
    Contacts.CNContactEmailAddressesKey,
    Contacts.CNContactPhoneNumbersKey,
]

# Additional keys for full detail
_DETAIL_KEYS = _SUMMARY_KEYS + [
    Contacts.CNContactPostalAddressesKey,
    Contacts.CNContactBirthdayKey,
    Contacts.CNContactUrlAddressesKey,
    Contacts.CNContactSocialProfilesKey,
    Contacts.CNContactInstantMessageAddressesKey,
    Contacts.CNContactRelationsKey,
    Contacts.CNContactDatesKey,
    Contacts.CNContactDepartmentNameKey,
    Contacts.CNContactImageDataAvailableKey,
]

# Note key requires entitlement on macOS 13+ - kept separate to allow fallback
_NOTE_KEY = Contacts.CNContactNoteKey


class ContactsError(Exception):
    """Raised when Contacts operations fail."""


class _EnumerationDone(Exception):
    """Sentinel raised from enum_cb to halt CNContactStore enumeration early (stop param is unusable, see issue #80)."""


class ContactsManager:
    """Manages Apple Contacts access through CNContactStore."""

    def __init__(self):
        self._store = None
        self._gravatar_remaining = None  # From X-RateLimit-Remaining header
        self._gravatar_reset = None  # From X-RateLimit-Reset header (unix timestamp)

    @property
    def store(self):
        """Lazy-init the contact store with permission request."""
        if self._store is None:
            store = Contacts.CNContactStore.alloc().init()
            self._request_access(store)
            self._store = store
        return self._store

    def _request_access(self, store=None):
        """Request contacts access (blocks until user responds)."""
        target = store if store is not None else self._store
        granted_flag = threading.Event()
        access_result = {"granted": False, "error": None}

        def callback(granted, error):
            access_result["granted"] = granted
            access_result["error"] = error
            granted_flag.set()

        target.requestAccessForEntityType_completionHandler_(
            0,
            callback,  # 0 = CNEntityTypeContacts
        )

        granted_flag.wait(timeout=30)

        if not access_result["granted"]:
            err = access_result["error"]
            msg = str(err) if err else "Contacts access denied"
            raise ContactsError(f"{msg}. Grant access in System Settings > Privacy & Security > Contacts.")

        logger.info("Contacts access granted")

    def list_groups(self) -> list[dict]:
        """Return all contact groups."""
        groups, error = self.store.groupsMatchingPredicate_error_(None, None)
        if error:
            raise ContactsError(f"Failed to fetch groups: {error}")

        results = []
        for g in groups or []:
            results.append(
                {
                    "id": g.identifier(),
                    "name": g.name(),
                }
            )
        return sorted(results, key=lambda g: g["name"] or "")

    def search_contacts(self, query: str, limit: int = 20) -> list[dict]:
        """Search contacts by name, email, or phone.

        Uses CNContactStore's predicate search for name matching (fast),
        then falls back to iterating for email/phone matches.
        """
        results = []

        # First: name-based search using native predicate (very fast)
        predicate = Contacts.CNContact.predicateForContactsMatchingName_(query)
        try:
            name_matches, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
                predicate, _SUMMARY_KEYS, None
            )
            if name_matches:
                for contact in name_matches:
                    if len(results) >= limit:
                        break
                    results.append(_serialize_contact(contact))
        except Exception:
            pass

        # If we have enough results from name search, return early
        if len(results) >= limit:
            return results

        # Second: search email/phone by iterating (slower but comprehensive)
        query_lower = query.lower()
        query_digits = "".join(c for c in query if c.isdigit())
        seen_ids = {r["id"] for r in results}

        request = Contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(_SUMMARY_KEYS)

        def enum_cb(contact, stop):
            if len(results) >= limit:
                raise _EnumerationDone()
            cid = contact.identifier()
            if cid in seen_ids:
                return
            # Check organization
            org = (contact.organizationName() or "").lower()
            if query_lower in org:
                results.append(_serialize_contact(contact))
                seen_ids.add(cid)
                return
            # Check emails
            for labeled in contact.emailAddresses():
                val = labeled.value()
                if val and query_lower in val.lower():
                    results.append(_serialize_contact(contact))
                    seen_ids.add(cid)
                    return
            # Check phone numbers
            if query_digits and len(query_digits) >= 3:
                for labeled in contact.phoneNumbers():
                    val = labeled.value().stringValue()
                    if val:
                        phone_digits = "".join(c for c in val if c.isdigit())
                        if query_digits in phone_digits:
                            results.append(_serialize_contact(contact))
                            seen_ids.add(cid)
                            return

        try:
            self.store.enumerateContactsWithFetchRequest_error_usingBlock_(request, None, enum_cb)
        except _EnumerationDone:
            pass

        return results

    def get_contact(self, contact_id: str) -> dict:
        """Get a single contact by ID with full details."""
        predicate = Contacts.CNContact.predicateForContactsWithIdentifiers_([contact_id])

        # Try with notes key first, fall back without (requires entitlement on macOS 13+)
        keys_with_notes = _DETAIL_KEYS + [_NOTE_KEY]
        contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
            predicate, keys_with_notes, None
        )
        has_notes_access = True
        if error:
            # Retry without notes
            has_notes_access = False
            contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
                predicate, _DETAIL_KEYS, None
            )

        if error:
            raise ContactsError(f"Failed to fetch contact: {error}")
        if not contacts or len(contacts) == 0:
            raise ContactsError(f"Contact not found: {contact_id}")

        return _serialize_contact(contacts[0], include_details=True, has_notes_access=has_notes_access)

    def list_contacts(self, group: str | None = None, limit: int = 50) -> list[dict]:
        """List contacts, optionally filtered to a group."""
        if group:
            # Find the group first
            groups, _ = self.store.groupsMatchingPredicate_error_(None, None)
            target_group = None
            for g in groups or []:
                if g.name().lower() == group.lower():
                    target_group = g
                    break
            if target_group is None:
                raise ContactsError(f"Group not found: {group}")

            predicate = Contacts.CNContact.predicateForContactsInGroupWithIdentifier_(target_group.identifier())
            contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
                predicate, _SUMMARY_KEYS, None
            )
            if error:
                raise ContactsError(f"Failed to fetch contacts: {error}")

            results = []
            for c in (contacts or [])[:limit]:
                results.append(_serialize_contact(c))
        else:
            request = Contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(_SUMMARY_KEYS)
            results = []

            def enum_cb(contact, stop):
                if len(results) >= limit:
                    raise _EnumerationDone()
                results.append(_serialize_contact(contact))

            try:
                self.store.enumerateContactsWithFetchRequest_error_usingBlock_(request, None, enum_cb)
            except _EnumerationDone:
                pass

        return sorted(results, key=lambda c: c.get("name") or "")

    def create_contact(
        self,
        first_name: str | None = None,
        last_name: str | None = None,
        organization: str | None = None,
        job_title: str | None = None,
        email: str | None = None,
        email_label: str = "Work",
        phone: str | None = None,
        phone_label: str = "Main",
        address: dict | None = None,
        notes: str | None = None,
        group: str | None = None,
    ) -> dict:
        """Create a new contact.

        Checks for duplicates by name and email before creating. If a matching
        contact already exists, raises ContactsError with the existing contact ID.

        Args:
            first_name: First name
            last_name: Last name
            organization: Company/organization name
            job_title: Job title
            email: Email address
            email_label: Label for email (Home, Work, etc.)
            phone: Phone number
            phone_label: Label for phone (Mobile, Home, Work, Main, etc.)
            address: Street address as a dict or string
            notes: Notes
            group: Group name to add the contact to
        """

        # Check for duplicate by name
        full_name = f"{first_name or ''} {last_name or ''}".strip()
        if full_name:
            predicate = Contacts.CNContact.predicateForContactsMatchingName_(full_name)
            try:
                matches, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
                    predicate, _SUMMARY_KEYS, None
                )
                if matches and len(matches) > 0:
                    existing = _serialize_contact(matches[0])
                    raise ContactsError(
                        f"Duplicate contact found: '{existing.get('name')}' "
                        f"(id: {existing['id']}). Use contact_update to modify "
                        f"the existing contact instead."
                    )
            except ContactsError:
                raise
            except Exception:
                pass  # If search fails, proceed with creation

        # Check for duplicate by email
        if email:
            email_lower = email.lower()
            dupes = self.search_contacts(email_lower, limit=1)
            if dupes:
                for d in dupes:
                    for e in d.get("emails", []):
                        if e.get("value", "").lower() == email_lower:
                            raise ContactsError(
                                f"Duplicate contact found with email '{email}': "
                                f"'{d.get('name')}' (id: {d['id']}). Use "
                                f"contact_update to modify the existing contact instead."
                            )

        contact = Contacts.CNMutableContact.alloc().init()

        if first_name:
            contact.setGivenName_(first_name)
        if last_name:
            contact.setFamilyName_(last_name)
        if organization:
            contact.setOrganizationName_(organization)
        if job_title:
            contact.setJobTitle_(job_title)
        has_notes = False
        if notes:
            contact.setNote_(notes)
            has_notes = True

        if email:
            label = _to_cn_label(email_label)
            email_value = Contacts.CNLabeledValue.labeledValueWithLabel_value_(label, email)
            contact.setEmailAddresses_([email_value])

        if phone:
            label = _to_cn_label(phone_label)
            phone_value = Contacts.CNLabeledValue.labeledValueWithLabel_value_(
                label, Contacts.CNPhoneNumber.phoneNumberWithStringValue_(phone)
            )
            contact.setPhoneNumbers_([phone_value])

        if address:
            addr = Contacts.CNMutablePostalAddress.alloc().init()
            if isinstance(address, dict):
                if address.get("street"):
                    addr.setStreet_(address["street"])
                if address.get("city"):
                    addr.setCity_(address["city"])
                if address.get("state"):
                    addr.setState_(address["state"])
                if address.get("postal_code"):
                    addr.setPostalCode_(address["postal_code"])
                if address.get("country"):
                    addr.setCountry_(address["country"])
                label = _to_cn_label(address.get("label", "Work"))
            else:
                addr.setStreet_(address)
                label = Contacts.CNLabelHome
            addr_value = Contacts.CNLabeledValue.labeledValueWithLabel_value_(label, addr)
            contact.setPostalAddresses_([addr_value])

        # Save the contact - retry without notes if it fails (macOS 13+
        # requires com.apple.developer.contacts.notes entitlement)
        save_request = Contacts.CNSaveRequest.alloc().init()
        save_request.addContact_toContainerWithIdentifier_(contact, None)

        success, error = self.store.executeSaveRequest_error_(save_request, None)
        if not success and has_notes:
            # Retry without notes
            contact.setNote_(None)
            save_request = Contacts.CNSaveRequest.alloc().init()
            save_request.addContact_toContainerWithIdentifier_(contact, None)
            success, error = self.store.executeSaveRequest_error_(save_request, None)

        if not success:
            raise ContactsError(f"Failed to create contact: {error}")

        # Add to group if specified
        group_added = False
        if group:
            groups, _ = self.store.groupsMatchingPredicate_error_(None, None)
            group_found = False
            for g in groups or []:
                if g.name().lower() == group.lower():
                    group_found = True
                    group_request = Contacts.CNSaveRequest.alloc().init()
                    group_request.addMember_toGroup_(contact, g)
                    ok, err = self.store.executeSaveRequest_error_(group_request, None)
                    if ok:
                        group_added = True
                    else:
                        logger.warning("Failed to add contact to group '%s': %s", group, err)
                    break
            if not group_found:
                logger.warning("Group '%s' not found; contact created without group", group)

        result = _serialize_contact(contact)
        result["group_added"] = group_added
        return result

    def add_to_group(self, contact_id: str, group_name: str) -> dict:
        """Add an existing contact to a group.

        Args:
            contact_id: Contact identifier string
            group_name: Group name (case-insensitive)
        """
        # Fetch the contact as a mutable copy
        predicate = Contacts.CNContact.predicateForContactsWithIdentifiers_([contact_id])
        contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
            predicate, [Contacts.CNContactIdentifierKey], None
        )
        if error or not contacts or len(contacts) == 0:
            raise ContactsError(f"Contact not found: {contact_id}")

        contact = contacts[0]

        # Find the group
        groups, _ = self.store.groupsMatchingPredicate_error_(None, None)
        target_group = None
        for g in groups or []:
            if g.name().lower() == group_name.lower():
                target_group = g
                break
        if target_group is None:
            raise ContactsError(f"Group not found: {group_name}")

        save_request = Contacts.CNSaveRequest.alloc().init()
        save_request.addMember_toGroup_(contact, target_group)
        success, error = self.store.executeSaveRequest_error_(save_request, None)
        if not success:
            raise ContactsError(f"Failed to add contact to group: {error}")

        return {"contact_id": contact_id, "group": group_name, "added": True}

    def update_contact(
        self,
        contact_id: str,
        first_name: str | None = None,
        last_name: str | None = None,
        organization: str | None = None,
        job_title: str | None = None,
        phones: list | None = None,
        emails: list | None = None,
        addresses: list | None = None,
        urls: list | None = None,
        social_profiles: list | None = None,
        department: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Update an existing contact's fields.

        Args:
            contact_id: Contact identifier string
            first_name: New first name (or None to leave unchanged)
            last_name: New last name (or None to leave unchanged)
            organization: New organization (or None to leave unchanged)
            job_title: New job title (or None to leave unchanged)
            phones: List of {"label": str, "value": str} dicts to replace
                    all phone numbers (or None to leave unchanged)
            emails: List of {"label": str, "value": str} dicts to replace
                    all email addresses (or None to leave unchanged)
            addresses: List of dicts with keys: label, street, city, state,
                       postal_code, country (or None to leave unchanged)
            notes: New notes text (or None to leave unchanged)
        """
        keys = list(_DETAIL_KEYS) + [_NOTE_KEY]
        predicate = Contacts.CNContact.predicateForContactsWithIdentifiers_([contact_id])
        contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(predicate, keys, None)
        if error:
            # Retry without notes key
            keys = list(_DETAIL_KEYS)
            contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(predicate, keys, None)
        if error or not contacts or len(contacts) == 0:
            raise ContactsError(f"Contact not found: {contact_id}")

        mutable = contacts[0].mutableCopy()

        if first_name is not None:
            mutable.setGivenName_(first_name)
        if last_name is not None:
            mutable.setFamilyName_(last_name)
        if organization is not None:
            mutable.setOrganizationName_(organization)
        if job_title is not None:
            mutable.setJobTitle_(job_title)
        if phones is not None:
            phone_values = []
            for p in phones:
                label = _to_cn_label(p.get("label"))
                phone_obj = Contacts.CNPhoneNumber.phoneNumberWithStringValue_(p["value"])
                lv = Contacts.CNLabeledValue.labeledValueWithLabel_value_(label, phone_obj)
                phone_values.append(lv)
            mutable.setPhoneNumbers_(phone_values)
        if emails is not None:
            email_values = []
            for e in emails:
                label = _to_cn_label(e.get("label"))
                lv = Contacts.CNLabeledValue.labeledValueWithLabel_value_(label, e["value"])
                email_values.append(lv)
            mutable.setEmailAddresses_(email_values)
        if addresses is not None:
            addr_values = []
            for a in addresses:
                addr = Contacts.CNMutablePostalAddress.alloc().init()
                if a.get("street"):
                    addr.setStreet_(a["street"])
                if a.get("city"):
                    addr.setCity_(a["city"])
                if a.get("state"):
                    addr.setState_(a["state"])
                if a.get("postal_code"):
                    addr.setPostalCode_(a["postal_code"])
                if a.get("country"):
                    addr.setCountry_(a["country"])
                label = _to_cn_label(a.get("label", "Work"))
                lv = Contacts.CNLabeledValue.labeledValueWithLabel_value_(label, addr)
                addr_values.append(lv)
            mutable.setPostalAddresses_(addr_values)

        if urls is not None:
            url_values = []
            for u in urls:
                label = _to_cn_label(u.get("label", "Other"))
                lv = Contacts.CNLabeledValue.labeledValueWithLabel_value_(label, u["value"])
                url_values.append(lv)
            mutable.setUrlAddresses_(url_values)
        if social_profiles is not None:
            profile_values = []
            for sp in social_profiles:
                profile = Contacts.CNSocialProfile.alloc().initWithUrlString_username_userIdentifier_service_(
                    sp.get("url", ""),
                    sp.get("username", ""),
                    "",  # userIdentifier
                    sp.get("service", ""),
                )
                label = _to_cn_label(sp.get("label", "Other"))
                lv = Contacts.CNLabeledValue.labeledValueWithLabel_value_(label, profile)
                profile_values.append(lv)
            mutable.setSocialProfiles_(profile_values)
        if department is not None:
            mutable.setDepartmentName_(department)

        has_notes = False
        if notes is not None:
            mutable.setNote_(notes)
            has_notes = True

        save_request = Contacts.CNSaveRequest.alloc().init()
        save_request.updateContact_(mutable)
        success, error = self.store.executeSaveRequest_error_(save_request, None)

        if not success and has_notes:
            # Retry without notes (macOS 13+ entitlement issue)
            logger.warning("Notes entitlement not available; contact %s saved without notes", contact_id)
            mutable.setNote_(None)
            save_request = Contacts.CNSaveRequest.alloc().init()
            save_request.updateContact_(mutable)
            success, error = self.store.executeSaveRequest_error_(save_request, None)

        if not success:
            raise ContactsError(f"Failed to update contact: {error}")

        return self.get_contact(contact_id)

    def find_duplicates(self, limit: int = 50) -> list[dict]:
        """Find duplicate contacts by name, email, or phone.

        Returns groups of contacts that appear to be duplicates.
        """
        request = Contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(_SUMMARY_KEYS)
        all_contacts = []

        def enum_cb(contact, stop):
            all_contacts.append(contact)

        self.store.enumerateContactsWithFetchRequest_error_usingBlock_(request, None, enum_cb)

        # Index contacts by normalized name, email, and phone
        by_name = {}
        by_email = {}
        by_phone = {}

        for contact in all_contacts:
            serialized = _serialize_contact(contact)

            # Name key
            name = (serialized.get("name") or "").strip().lower()
            if name:
                by_name.setdefault(name, []).append(serialized)

            # Email keys
            for e in serialized.get("emails", []):
                email_val = e.get("value", "").lower().strip()
                if email_val:
                    by_email.setdefault(email_val, []).append(serialized)

            # Phone keys (digits only)
            for p in serialized.get("phones", []):
                digits = "".join(c for c in p.get("value", "") if c.isdigit())
                # Use last 10 digits to normalize
                if len(digits) >= 7:
                    key = digits[-10:] if len(digits) >= 10 else digits
                    by_phone.setdefault(key, []).append(serialized)

        # Collect duplicate groups, deduplicating across match types
        results = []
        seen_groups = set()  # Track by frozenset of IDs to avoid reporting same group twice

        for index, match_type in [
            (by_name, "name"),
            (by_email, "email"),
            (by_phone, "phone"),
        ]:
            for key, contacts in index.items():
                if len(contacts) < 2:
                    continue
                # Deduplicate contacts within the group by ID
                unique = {}
                for c in contacts:
                    unique[c["id"]] = c
                if len(unique) < 2:
                    continue
                group_key = frozenset(unique.keys())
                if group_key in seen_groups:
                    continue
                seen_groups.add(group_key)
                results.append(
                    {
                        "match_type": match_type,
                        "match_value": key,
                        "contacts": list(unique.values()),
                    }
                )
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        return results

    def merge_contacts(self, keep_id: str, merge_ids: list[str]) -> dict:
        """Merge duplicate contacts into one.

        Keeps the contact specified by keep_id and pulls in data from
        merge_ids contacts, then deletes the merged contacts.
        """
        # Fetch the primary contact
        predicate = Contacts.CNContact.predicateForContactsWithIdentifiers_([keep_id])
        contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(predicate, _DETAIL_KEYS, None)
        if error or not contacts or len(contacts) == 0:
            raise ContactsError(f"Primary contact not found: {keep_id}")

        primary = contacts[0].mutableCopy()
        primary_data = _serialize_contact(contacts[0], include_details=True)

        # Collect existing values from primary to avoid duplicating
        existing_emails = {e["value"].lower() for e in primary_data.get("emails", [])}
        existing_phones = {"".join(c for c in p["value"] if c.isdigit())[-10:] for p in primary_data.get("phones", [])}

        new_phones = list(primary.phoneNumbers() or [])
        new_emails = list(primary.emailAddresses() or [])
        new_addresses = list(primary.postalAddresses() or [])

        merge_mutables = []  # (merge_id, mutable_copy) for batch deletion

        for merge_id in merge_ids:
            pred = Contacts.CNContact.predicateForContactsWithIdentifiers_([merge_id])
            merge_contacts, err = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
                pred, _DETAIL_KEYS, None
            )
            if err or not merge_contacts or len(merge_contacts) == 0:
                logger.warning(f"Merge contact not found: {merge_id}, skipping")
                continue

            merge_c = merge_contacts[0]

            # Fill blank fields on primary from merge source
            if not (primary.givenName() or "") and merge_c.givenName():
                primary.setGivenName_(merge_c.givenName())
            if not (primary.familyName() or "") and merge_c.familyName():
                primary.setFamilyName_(merge_c.familyName())
            if not (primary.organizationName() or "") and merge_c.organizationName():
                primary.setOrganizationName_(merge_c.organizationName())
            if not (primary.jobTitle() or "") and merge_c.jobTitle():
                primary.setJobTitle_(merge_c.jobTitle())

            # Merge emails (add non-duplicates)
            for labeled in merge_c.emailAddresses() or []:
                val = labeled.value()
                if val and val.lower() not in existing_emails:
                    new_emails.append(labeled)
                    existing_emails.add(val.lower())

            # Merge phones (add non-duplicates by last 10 digits)
            for labeled in merge_c.phoneNumbers() or []:
                val = labeled.value().stringValue()
                if val:
                    digits = "".join(c for c in val if c.isdigit())
                    normalized = digits[-10:] if len(digits) >= 10 else digits
                    if normalized not in existing_phones:
                        new_phones.append(labeled)
                        existing_phones.add(normalized)

            # Merge addresses
            for labeled in merge_c.postalAddresses() or []:
                new_addresses.append(labeled)

            merge_mutables.append((merge_id, merge_c.mutableCopy()))

        # Apply merged data to primary
        primary.setPhoneNumbers_(new_phones)
        primary.setEmailAddresses_(new_emails)
        if new_addresses:
            primary.setPostalAddresses_(new_addresses)

        if not merge_mutables:
            raise ContactsError("No valid contacts to merge - check that all merge_ids exist")

        # Commit primary update + all deletions atomically
        save_request = Contacts.CNSaveRequest.alloc().init()
        save_request.updateContact_(primary)
        for _, merge_mutable in merge_mutables:
            save_request.deleteContact_(merge_mutable)
        success, error = self.store.executeSaveRequest_error_(save_request, None)
        if not success:
            raise ContactsError(f"Failed to save merged contact: {error}")

        deleted_ids = [merge_id for merge_id, _ in merge_mutables]

        return {
            "contact": _serialize_contact(primary, include_details=True),
            "merged": len(deleted_ids),
            "deleted": deleted_ids,
        }

    def enrich_contact(
        self, contact_id: str, apply: bool = False, pdl_api_key: str | None = None, gravatar_api_key: str | None = None
    ) -> dict:
        """Enrich a contact with data from online sources.

        Looks up the contact's email against Gravatar and optionally
        People Data Labs to find additional information.

        Args:
            contact_id: Contact identifier
            apply: If True, update the contact with found data
            pdl_api_key: Optional People Data Labs API key
            gravatar_api_key: Optional Gravatar API key (1000 vs 100 req/hr)
        """
        # Fetch the contact
        contact = self.get_contact(contact_id)
        emails = contact.get("emails", [])
        if not emails:
            raise ContactsError("Contact has no email addresses to enrich from")

        email = emails[0]["value"]
        sources = []

        # 1. Gravatar
        gravatar_data = self._enrich_gravatar(email, api_key=gravatar_api_key)
        if gravatar_data:
            sources.append({"source": "gravatar", "data": gravatar_data})

        # 2. People Data Labs (if API key provided)
        if pdl_api_key:
            pdl_data = self._enrich_pdl(email, pdl_api_key)
            if pdl_data:
                sources.append({"source": "peopledatalabs", "data": pdl_data})

        # Apply enrichment if requested
        applied = False
        if apply and sources:
            update_kwargs = self._build_enrichment_update(contact, sources)
            # Handle avatar photo separately (binary data, not a regular field)
            avatar_url = update_kwargs.pop("_avatar_url", None)
            if update_kwargs:
                self.update_contact(contact_id, **update_kwargs)
                applied = True
            if avatar_url:
                if self._download_and_set_photo(contact_id, avatar_url):
                    applied = True

        return {
            "contact_id": contact_id,
            "email": email,
            "sources": sources,
            "applied": applied,
        }

    def _gravatar_has_avatar(self, email):
        """Quick check if an email has a Gravatar avatar. Does NOT count toward API rate limit.

        Returns the avatar URL if found, None otherwise.
        """
        md5_hash = hashlib.md5(email.strip().lower().encode()).hexdigest()
        avatar_url = f"https://www.gravatar.com/avatar/{md5_hash}?d=404&s=200"
        try:
            req = Request(avatar_url, method="HEAD")
            req.add_header("User-Agent", "contacts-tools/1.0")
            resp = urlopen(req, timeout=5)
            if resp.status == 200:
                return f"https://www.gravatar.com/avatar/{md5_hash}?s=400"
        except (HTTPError, URLError):
            pass
        return None

    def _enrich_gravatar(self, email, api_key=None, skip_avatar_check=False):
        """Look up Gravatar profile for an email using the v3 API (SHA256).

        Args:
            email: Email address to look up
            api_key: Optional Gravatar API key (GRAVATAR_API_KEY env var).
                     Without key: 100 req/hour. With key: 1,000 req/hour.
            skip_avatar_check: If True, skip the avatar HEAD check (already done).
        """
        sha256_hash = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        md5_hash = hashlib.md5(email.strip().lower().encode()).hexdigest()

        result = {}

        # Check for avatar (uses MD5, doesn't count toward rate limit)
        if not skip_avatar_check:
            avatar = self._gravatar_has_avatar(email)
            if avatar:
                result["avatar_url"] = avatar
        else:
            # Caller already confirmed avatar exists
            result["avatar_url"] = f"https://www.gravatar.com/avatar/{md5_hash}?s=400"

        # If rate limit is exhausted, return immediately rather than blocking the MCP server
        if self._gravatar_remaining is not None and self._gravatar_remaining <= 0:
            reset_in = max(0, (self._gravatar_reset or 0) - _time.time())
            logger.warning("Gravatar rate limit exhausted; resets in %.0fs", reset_in)
            return None

        # Get profile data via v3 API (uses SHA256) with retry on 429
        profile_url = f"https://api.gravatar.com/v3/profiles/{sha256_hash}"
        resp = None
        for attempt in range(3):
            try:
                req = Request(profile_url)
                req.add_header("User-Agent", "contacts-tools/1.0")
                if api_key:
                    req.add_header("Authorization", f"Bearer {api_key}")
                resp = urlopen(req, timeout=5)
                # Track rate limit from response headers
                remaining = resp.headers.get("X-RateLimit-Remaining")
                reset = resp.headers.get("X-RateLimit-Reset")
                if remaining is not None:
                    self._gravatar_remaining = int(remaining)
                if reset is not None:
                    self._gravatar_reset = int(reset)
                break
            except HTTPError as e:
                # Track rate limit from error response headers too
                remaining = e.headers.get("X-RateLimit-Remaining") if hasattr(e, "headers") else None
                reset = e.headers.get("X-RateLimit-Reset") if hasattr(e, "headers") else None
                if remaining is not None:
                    self._gravatar_remaining = int(remaining)
                if reset is not None:
                    self._gravatar_reset = int(reset)

                if e.code == 429:
                    logger.warning("Gravatar rate limited for %s; aborting", email)
                    return result if result else None
                elif e.code == 404:
                    return result if result else None
                raise
        try:
            if resp and resp.status == 200:
                data = _json.loads(resp.read().decode())

                if data.get("display_name"):
                    result["display_name"] = data["display_name"]
                if data.get("description"):
                    result["about"] = data["description"]
                if data.get("location"):
                    result["location"] = data["location"]
                if data.get("job_title"):
                    result["job_title"] = data["job_title"]
                if data.get("company"):
                    result["company"] = data["company"]
                if data.get("pronunciation"):
                    result["pronunciation"] = data["pronunciation"]
                if data.get("pronouns"):
                    result["pronouns"] = data["pronouns"]
                if data.get("profile_url"):
                    result["profile_url"] = data["profile_url"]

                # Verified accounts as social profiles
                profiles = []
                for account in data.get("verified_accounts", []):
                    url = account.get("url", "")
                    # Extract username from URL (last path segment)
                    username = ""
                    if url:
                        path = url.rstrip("/").split("/")[-1]
                        # Strip leading @ for Mastodon-style URLs
                        username = path.lstrip("@") if path else ""
                    profiles.append(
                        {
                            "service": account.get("service_label", ""),
                            "username": username,
                            "url": url,
                        }
                    )
                if profiles:
                    result["social_profiles"] = profiles

                # Links
                urls = []
                for link in data.get("links", []):
                    urls.append(
                        {
                            "title": link.get("label", ""),
                            "value": link.get("url", ""),
                        }
                    )
                if urls:
                    result["urls"] = urls

        except (HTTPError, URLError):
            pass

        return result if result else None

    def _enrich_pdl(self, email, api_key):
        """Look up People Data Labs profile for an email."""
        from urllib.parse import quote

        url = f"https://api.peopledatalabs.com/v5/person/enrich?email={quote(email)}"
        req = Request(url)
        req.add_header("X-Api-Key", api_key)
        req.add_header("Accept", "application/json")

        try:
            resp = urlopen(req, timeout=10)
            if resp.status == 200:
                data = _json.loads(resp.read().decode())
                if data.get("status") != 200:
                    return None

                result = {}
                if data.get("full_name"):
                    result["full_name"] = data["full_name"]
                if data.get("job_title"):
                    result["job_title"] = data["job_title"]
                if data.get("job_company_name"):
                    result["company"] = data["job_company_name"]
                if data.get("industry"):
                    result["industry"] = data["industry"]
                if data.get("location_name"):
                    result["location"] = data["location_name"]

                # Social profiles
                profiles = []
                if data.get("linkedin_url"):
                    profiles.append({"service": "LinkedIn", "url": data["linkedin_url"]})
                if data.get("twitter_url"):
                    profiles.append({"service": "Twitter", "url": data["twitter_url"]})
                if data.get("facebook_url"):
                    profiles.append({"service": "Facebook", "url": data["facebook_url"]})
                if data.get("github_url"):
                    profiles.append({"service": "GitHub", "url": data["github_url"]})
                if profiles:
                    result["social_profiles"] = profiles

                if data.get("phone_numbers"):
                    result["phone_numbers"] = data["phone_numbers"]

                return result if result else None

        except (HTTPError, URLError) as e:
            logger.warning(f"PDL enrichment failed: {e}")

        return None

    def _build_enrichment_update(self, existing_contact, sources):
        """Build update kwargs from enrichment data, only filling gaps."""
        kwargs = {}

        # Collect all enrichment data
        all_socials = []
        all_urls = []

        for source in sources:
            data = source["data"]

            # Job title (only if contact doesn't have one)
            if not existing_contact.get("job_title") and data.get("job_title"):
                kwargs["job_title"] = data["job_title"]

            # Organization (only if contact doesn't have one)
            if not existing_contact.get("organization") and data.get("company"):
                kwargs["organization"] = data["company"]

            # Social profiles
            for sp in data.get("social_profiles", []):
                all_socials.append(sp)

            # URLs
            if data.get("profile_url"):
                all_urls.append({"label": "Other", "value": data["profile_url"]})
            for u in data.get("urls", []):
                all_urls.append({"label": "Other", "value": u.get("value", u.get("url", ""))})

        # Add social profiles (overwrite if enrichment found new ones)
        if all_socials:
            kwargs["social_profiles"] = all_socials

        # Add URLs only if contact doesn't already have any
        if all_urls and not existing_contact.get("urls"):
            kwargs["urls"] = all_urls

        # Avatar photo
        for source in sources:
            avatar_url = source["data"].get("avatar_url")
            if avatar_url and not existing_contact.get("has_photo"):
                kwargs["_avatar_url"] = avatar_url
                break

        return kwargs

    def _download_and_set_photo(self, contact_id, avatar_url):
        """Download an avatar image and set it as the contact's photo."""
        try:
            req = Request(avatar_url)
            req.add_header("User-Agent", "contacts-tools/1.0")
            resp = urlopen(req, timeout=10)
            if resp.status == 200:
                image_data = resp.read()
                import Foundation

                ns_data = Foundation.NSData.dataWithBytes_length_(image_data, len(image_data))

                # Fetch contact and set image
                predicate = Contacts.CNContact.predicateForContactsWithIdentifiers_([contact_id])
                keys = [Contacts.CNContactIdentifierKey, Contacts.CNContactImageDataKey]
                contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(predicate, keys, None)
                if error or not contacts or len(contacts) == 0:
                    return False

                mutable = contacts[0].mutableCopy()
                mutable.setImageData_(ns_data)

                save_request = Contacts.CNSaveRequest.alloc().init()
                save_request.updateContact_(mutable)
                success, error = self.store.executeSaveRequest_error_(save_request, None)
                if success:
                    logger.info(f"Set photo for contact {contact_id}")
                    return True
                else:
                    logger.warning(f"Failed to set photo: {error}")
        except (HTTPError, URLError) as e:
            logger.warning(f"Failed to download avatar: {e}")
        return False

    def enrich_all(
        self, apply: bool = False, limit: int = 50, pdl_api_key: str | None = None, gravatar_api_key: str | None = None
    ) -> dict:
        """Scan all contacts with emails for enrichment data.

        Uses a two-pass approach to minimize API rate limit usage:
        1. Fast avatar-only sweep (unlimited, no rate limit)
        2. Full profile lookup only for contacts that have a Gravatar avatar
        """
        contacts = self.list_contacts(limit=5000)
        with_email = [c for c in contacts if c.get("emails")][:limit]

        # Pass 1: Fast avatar check (doesn't count toward rate limit)
        avatar_hits = {}
        for c in with_email:
            email = c["emails"][0]["value"]
            avatar = self._gravatar_has_avatar(email)
            if avatar:
                avatar_hits[c["id"]] = email
        logger.info(f"Avatar sweep: {len(avatar_hits)} hits out of {len(with_email)} contacts")

        # Pass 2: Full profile lookup only for avatar hits
        hits = []
        misses = 0
        applied_count = 0

        for c in with_email:
            email = c["emails"][0]["value"]
            sources = []

            if c["id"] in avatar_hits:
                # This contact has a Gravatar - do the full profile lookup
                gravatar_data = self._enrich_gravatar(email, api_key=gravatar_api_key, skip_avatar_check=True)
            else:
                gravatar_data = None
            if gravatar_data:
                sources.append({"source": "gravatar", "data": gravatar_data})

            if pdl_api_key:
                pdl_data = self._enrich_pdl(email, pdl_api_key)
                if pdl_data:
                    sources.append({"source": "peopledatalabs", "data": pdl_data})

            if sources:
                hit = {
                    "contact_id": c["id"],
                    "name": c.get("name"),
                    "email": email,
                    "sources": sources,
                }

                if apply:
                    try:
                        existing = self.get_contact(c["id"])
                        update_kwargs = self._build_enrichment_update(existing, sources)
                        avatar_url = update_kwargs.pop("_avatar_url", None)
                        contact_applied = False
                        if update_kwargs:
                            self.update_contact(c["id"], **update_kwargs)
                            contact_applied = True
                        if avatar_url:
                            if self._download_and_set_photo(c["id"], avatar_url):
                                contact_applied = True
                        hit["applied"] = contact_applied
                        if contact_applied:
                            applied_count += 1
                    except Exception as e:
                        hit["applied"] = False
                        hit["error"] = str(e)

                hits.append(hit)
            else:
                misses += 1

        return {
            "scanned": len(with_email),
            "hits": hits,
            "hit_count": len(hits),
            "misses": misses,
            "applied": applied_count,
        }

    def find_incomplete(self, missing: str = "any", limit: int = 50) -> dict:
        """Find contacts missing key information.

        Args:
            missing: "phone", "email", "address", "photo", or "any"
            limit: Maximum results
        """
        # Fetch all contacts with detail keys for address/photo check
        request = Contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(_DETAIL_KEYS)
        all_contacts = []

        def enum_cb(contact, stop):
            all_contacts.append(contact)

        self.store.enumerateContactsWithFetchRequest_error_usingBlock_(request, None, enum_cb)

        results = []
        for contact in all_contacts:
            serialized = _serialize_contact(contact, include_details=True)
            name = serialized.get("name")
            if not name:
                continue  # Skip unnamed contacts

            missing_fields = []

            has_phone = bool(serialized.get("phones"))
            has_email = bool(serialized.get("emails"))
            has_address = bool(serialized.get("addresses"))
            has_photo = bool(serialized.get("has_photo"))

            if not has_phone:
                missing_fields.append("phone")
            if not has_email:
                missing_fields.append("email")
            if not has_address:
                missing_fields.append("address")
            if not has_photo:
                missing_fields.append("photo")

            if not missing_fields:
                continue

            # Filter by requested type
            if missing != "any" and missing not in missing_fields:
                continue

            results.append(
                {
                    "id": serialized["id"],
                    "name": name,
                    "organization": serialized.get("organization"),
                    "has_phone": has_phone,
                    "has_email": has_email,
                    "has_address": has_address,
                    "has_photo": has_photo,
                    "missing": missing_fields,
                }
            )

            if len(results) >= limit:
                break

        return {
            "contacts": results,
            "count": len(results),
            "filter": missing,
        }

    def find_last_interaction(self, contact_id: str | None = None, name: str | None = None, limit: int = 10) -> dict:
        """Find last email interaction for contacts.

        Cross-references with Mail.app's SQLite database to find
        last sent/received dates.

        Issues exactly 2 grouped queries against the Envelope Index (one
        per direction - received/sent), regardless of how many target
        contacts there are, instead of 2 queries PER CONTACT (issue #68).
        Each query groups by LOWER(address) across the whole messages
        table in one pass, mirroring find_unknown_senders's existing
        pattern, then results are matched back to contacts in Python.
        """
        import os
        import sqlite3
        from datetime import datetime, timedelta

        # The main DB
        mail_db_main = os.path.expanduser("~/Library/Mail/V10/MailData/Envelope Index")

        if not os.path.exists(mail_db_main):
            # Try other versions
            for v in ["V11", "V9", "V8"]:
                alt = os.path.expanduser(f"~/Library/Mail/{v}/MailData/Envelope Index")
                if os.path.exists(alt):
                    mail_db_main = alt
                    break

        if not os.path.exists(mail_db_main):
            raise ContactsError("Mail.app database not found")

        # Get target contacts
        if contact_id:
            contact = self.get_contact(contact_id)
            targets = [contact]
        elif name:
            targets = []
            search_results = self.search_contacts(name, limit=limit)
            for sr in search_results:
                targets.append(self.get_contact(sr["id"]))
        else:
            # Get all contacts with emails
            all_contacts = self.list_contacts(limit=5000)
            targets = [c for c in all_contacts if c.get("emails")]

        cd_epoch = datetime(2001, 1, 1)

        def _to_datetime(ts):
            try:
                return cd_epoch + timedelta(seconds=ts)
            except (OSError, OverflowError, TypeError, ValueError):
                return None

        conn = sqlite3.connect(f"file:{mail_db_main}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            # Last email received FROM each address, across all contacts at once.
            received_rows = conn.execute(
                """
                SELECT LOWER(a.address) AS email, MAX(m.date_sent) AS last_date
                FROM messages m
                JOIN addresses a ON m.sender = a.ROWID
                WHERE a.address IS NOT NULL AND a.address != ''
                GROUP BY LOWER(a.address)
                """
            ).fetchall()
            # Last email sent TO each address, across all contacts at once.
            sent_rows = conn.execute(
                """
                SELECT LOWER(a.address) AS email, MAX(m.date_sent) AS last_date
                FROM messages m
                JOIN recipients r ON m.ROWID = r.message_id
                JOIN addresses a ON r.address_id = a.ROWID
                WHERE a.address IS NOT NULL AND a.address != ''
                GROUP BY LOWER(a.address)
                """
            ).fetchall()
        finally:
            conn.close()

        received_by_email = {row["email"]: _to_datetime(row["last_date"]) for row in received_rows if row["last_date"]}
        sent_by_email = {row["email"]: _to_datetime(row["last_date"]) for row in sent_rows if row["last_date"]}

        results = []
        for contact in targets:
            emails = contact.get("emails", [])
            if not emails:
                continue

            best_sent = None
            best_received = None

            for email_entry in emails:
                addr = (email_entry.get("value") or "").strip().lower()
                if not addr:
                    continue

                received_dt = received_by_email.get(addr)
                if received_dt and (best_received is None or received_dt > best_received):
                    best_received = received_dt

                sent_dt = sent_by_email.get(addr)
                if sent_dt and (best_sent is None or sent_dt > best_sent):
                    best_sent = sent_dt

            last_interaction = max(filter(None, [best_sent, best_received]), default=None)

            days_ago = None
            if last_interaction:
                days_ago = (datetime.now() - last_interaction).days

            entry = {
                "id": contact.get("id"),
                "name": contact.get("name"),
                "email": emails[0]["value"] if emails else None,
                "last_sent": best_sent.isoformat() if best_sent else None,
                "last_received": best_received.isoformat() if best_received else None,
                "last_interaction": last_interaction.isoformat() if last_interaction else None,
                "days_ago": days_ago,
            }
            results.append(entry)

        # Sort by last interaction (most recent first), None at the end
        results.sort(key=lambda x: x.get("last_interaction") or "", reverse=True)

        if not contact_id and not name:
            results = results[:limit]

        return {"contacts": results, "count": len(results)}

    def find_stale_contacts(self, days: int = 365, limit: int = 50) -> dict:
        """Find contacts with no email interaction in the given number of days."""
        from datetime import datetime, timedelta

        all_result = self.find_last_interaction(limit=500)
        cutoff = datetime.now() - timedelta(days=days)

        stale = []
        for contact in all_result["contacts"]:
            if contact["last_interaction"] is None:
                contact["days_ago"] = None
                stale.append(contact)
            else:
                interaction_dt = datetime.fromisoformat(contact["last_interaction"])
                if interaction_dt < cutoff:
                    stale.append(contact)

            if len(stale) >= limit:
                break

        # Sort: never-contacted first, then oldest interaction
        def _parse_interaction(val: str | None) -> datetime:
            if not val:
                return datetime.min
            try:
                return datetime.fromisoformat(val)
            except (TypeError, ValueError):
                return datetime.min

        stale.sort(key=lambda x: _parse_interaction(x.get("last_interaction")))

        return {
            "contacts": stale,
            "count": len(stale),
            "threshold_days": days,
        }

    def find_unknown_senders(self, days: int = 90, min_count: int = 2, limit: int = 30) -> dict:
        """Find email senders not in Apple Contacts.

        Scans Mail.app's SQLite database for sender addresses that don't
        match any contact email, sorted by frequency.
        """
        import os
        import re
        import sqlite3
        from datetime import datetime, timedelta

        # --- Locate Mail.app database ---
        mail_db_main = os.path.expanduser("~/Library/Mail/V10/MailData/Envelope Index")
        if not os.path.exists(mail_db_main):
            for v in ["V11", "V9", "V8"]:
                alt = os.path.expanduser(f"~/Library/Mail/{v}/MailData/Envelope Index")
                if os.path.exists(alt):
                    mail_db_main = alt
                    break

        if not os.path.exists(mail_db_main):
            raise ContactsError("Mail.app database not found")

        # --- Build set of known contact emails ---
        all_contacts = self.list_contacts(limit=5000)
        known_emails = set()
        for c in all_contacts:
            for e in c.get("emails", []):
                addr = e.get("value", "").strip().lower()
                if addr:
                    known_emails.add(addr)

        # --- Automated / no-reply patterns to exclude ---
        noreply_patterns = re.compile(
            r"^("
            r"no[-_.]?reply|"
            r"do[-_.]?not[-_.]?reply|"
            r"notifications?|"
            r"noreply|"
            r"mailer[-_.]?daemon|"
            r"postmaster|"
            r"bounce[s]?|"
            r"auto[-_.]?confirm|"
            r"alerts?|"
            r"news(letter)?|"
            r"updates?|"
            r"info@|"
            r"support@|"
            r"feedback@|"
            r"daemon@|"
            r"root@"
            r")",
            re.IGNORECASE,
        )

        # --- Query Mail.app for senders in the date range ---
        _cd_epoch = datetime(2001, 1, 1)
        cutoff_ts = (datetime.now() - timedelta(days=days) - _cd_epoch).total_seconds()

        conn = sqlite3.connect(f"file:{mail_db_main}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        try:
            rows = conn.execute(
                """
                SELECT
                    a.address AS email,
                    a.comment AS name,
                    COUNT(*) AS cnt,
                    MAX(m.date_sent) AS last_ts,
                    MIN(m.date_sent) AS first_ts
                FROM messages m
                JOIN addresses a ON m.sender = a.ROWID
                WHERE m.date_sent >= ?
                  AND a.address IS NOT NULL
                  AND a.address != ''
                GROUP BY LOWER(a.address)
                HAVING COUNT(*) >= ?
                ORDER BY cnt DESC
            """,
                (cutoff_ts, min_count),
            ).fetchall()
        finally:
            conn.close()

        # --- Filter against known contacts and noreply patterns ---
        senders = []
        for row in rows:
            email = (row["email"] or "").strip().lower()
            if not email:
                continue
            if email in known_emails:
                continue
            if noreply_patterns.search(email):
                continue

            # Parse timestamps (Core Data epoch: seconds since 2001-01-01)
            last_date = None
            first_date = None
            try:
                if row["last_ts"]:
                    last_date = (_cd_epoch + timedelta(seconds=row["last_ts"])).isoformat()
            except (OSError, ValueError):
                pass
            try:
                if row["first_ts"]:
                    first_date = (_cd_epoch + timedelta(seconds=row["first_ts"])).isoformat()
            except (OSError, ValueError):
                pass

            senders.append(
                {
                    "email": email,
                    "name": (row["name"] or "").strip() or None,
                    "count": row["cnt"],
                    "last_date": last_date,
                    "first_date": first_date,
                }
            )

            if len(senders) >= limit:
                break

        return {
            "senders": senders,
            "count": len(senders),
            "scanned_days": days,
        }

    def delete_contact(self, contact_id: str) -> dict:
        """Delete a contact by ID."""
        predicate = Contacts.CNContact.predicateForContactsWithIdentifiers_([contact_id])
        contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
            predicate, [Contacts.CNContactIdentifierKey], None
        )
        if error or not contacts or len(contacts) == 0:
            raise ContactsError(f"Contact not found: {contact_id}")

        mutable = contacts[0].mutableCopy()
        save_request = Contacts.CNSaveRequest.alloc().init()
        save_request.deleteContact_(mutable)
        success, error = self.store.executeSaveRequest_error_(save_request, None)
        if not success:
            raise ContactsError(f"Failed to delete contact: {error}")

        return {"contact_id": contact_id, "deleted": True}

    def export_contacts(
        self, group: str | None = None, contact_ids: list | None = None, output_path: str | None = None
    ) -> dict:
        """Export contacts to vCard (.vcf) format.

        Args:
            group: Group name to filter (case-insensitive)
            contact_ids: List of specific contact IDs to export
            output_path: File path to write to (default: ~/Desktop/contacts-export.vcf)
        """
        import os

        if output_path is None:
            output_path = os.path.expanduser("~/Desktop/contacts-export.vcf")
        else:
            output_path = os.path.expanduser(output_path)

        # vCard serialization needs all properties it might touch.
        # Use detail keys + image data, but NOT notes (entitlement issue on macOS 13+)
        keys_to_fetch = list(_DETAIL_KEYS) + [
            Contacts.CNContactImageDataKey,
            Contacts.CNContactThumbnailImageDataKey,
            Contacts.CNContactMiddleNameKey,
            Contacts.CNContactNamePrefixKey,
            Contacts.CNContactNameSuffixKey,
            Contacts.CNContactNicknameKey,
            Contacts.CNContactPhoneticGivenNameKey,
            Contacts.CNContactPhoneticFamilyNameKey,
            Contacts.CNContactPhoneticMiddleNameKey,
            Contacts.CNContactTypeKey,
        ]

        contacts_list = []

        if contact_ids:
            predicate = Contacts.CNContact.predicateForContactsWithIdentifiers_(contact_ids)
            contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
                predicate, keys_to_fetch, None
            )
            if error:
                raise ContactsError(f"Failed to fetch contacts: {error}")
            contacts_list = list(contacts or [])
        elif group:
            groups, _ = self.store.groupsMatchingPredicate_error_(None, None)
            target_group = None
            for g in groups or []:
                if g.name().lower() == group.lower():
                    target_group = g
                    break
            if target_group is None:
                raise ContactsError(f"Group not found: {group}")

            predicate = Contacts.CNContact.predicateForContactsInGroupWithIdentifier_(target_group.identifier())
            contacts, error = self.store.unifiedContactsMatchingPredicate_keysToFetch_error_(
                predicate, keys_to_fetch, None
            )
            if error:
                raise ContactsError(f"Failed to fetch contacts: {error}")
            contacts_list = list(contacts or [])
        else:
            request = Contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(keys_to_fetch)

            def enum_cb(contact, stop):
                contacts_list.append(contact)

            self.store.enumerateContactsWithFetchRequest_error_usingBlock_(request, None, enum_cb)

        if not contacts_list:
            raise ContactsError("No contacts found to export")

        # Build fresh CNMutableContact copies for vCard serialization.
        # Direct serialization of fetched contacts fails on macOS 13+ when
        # the notes entitlement is missing - the serializer throws
        # CNPropertyNotFetchedException even if notes weren't fetched.
        export_list = []
        for src in contacts_list:
            c = Contacts.CNMutableContact.alloc().init()
            c.setGivenName_(src.givenName() or "")
            c.setFamilyName_(src.familyName() or "")
            c.setMiddleName_(src.middleName() or "")
            c.setNamePrefix_(src.namePrefix() or "")
            c.setNameSuffix_(src.nameSuffix() or "")
            c.setNickname_(src.nickname() or "")
            c.setOrganizationName_(src.organizationName() or "")
            c.setJobTitle_(src.jobTitle() or "")
            c.setDepartmentName_(src.departmentName() or "")
            c.setEmailAddresses_(list(src.emailAddresses() or []))
            c.setPhoneNumbers_(list(src.phoneNumbers() or []))
            c.setPostalAddresses_(list(src.postalAddresses() or []))
            c.setUrlAddresses_(list(src.urlAddresses() or []))
            c.setSocialProfiles_(list(src.socialProfiles() or []))
            c.setInstantMessageAddresses_(list(src.instantMessageAddresses() or []))
            c.setContactRelations_(list(src.contactRelations() or []))
            c.setDates_(list(src.dates() or []))
            try:
                bday = src.birthday()
                if bday:
                    c.setBirthday_(bday)
            except Exception:
                pass
            try:
                if src.imageDataAvailable():
                    img = src.imageData()
                    if img:
                        c.setImageData_(img)
            except Exception:
                pass
            try:
                c.setNote_(src.note() or "")
            except Exception:
                c.setNote_("")
            export_list.append(c)

        vcard_data, error = Contacts.CNContactVCardSerialization.dataWithContacts_error_(export_list, None)
        if error or vcard_data is None:
            raise ContactsError(f"Failed to serialize contacts to vCard: {error}")

        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)

        # Write vCard data to file
        with open(output_path, "wb") as f:
            f.write(vcard_data.bytes().tobytes())

        return {
            "path": output_path,
            "count": len(contacts_list),
            "exported": True,
        }


# ── Helpers ──


def _to_cn_label(label_str):
    """Convert a human-readable label to a CNLabel constant or pass through custom labels."""
    label_map = {
        "home": Contacts.CNLabelHome,
        "work": Contacts.CNLabelWork,
        "other": Contacts.CNLabelOther,
        "mobile": Contacts.CNLabelPhoneNumberMobile,
        "main": Contacts.CNLabelPhoneNumberMain,
        "iphone": Contacts.CNLabelPhoneNumberiPhone,
        "fax": Contacts.CNLabelPhoneNumberWorkFax,
        "work fax": Contacts.CNLabelPhoneNumberWorkFax,
        "home fax": Contacts.CNLabelPhoneNumberHomeFax,
        "other fax": Contacts.CNLabelPhoneNumberOtherFax,
        "pager": Contacts.CNLabelPhoneNumberPager,
    }
    if not label_str:
        return Contacts.CNLabelOther
    return label_map.get(label_str.lower(), label_str)


def _serialize_contact(contact, include_details=False, has_notes_access=False):
    """Convert a CNContact to a serializable dict."""
    first = contact.givenName() or ""
    last = contact.familyName() or ""
    name = f"{first} {last}".strip()

    result = {
        "id": contact.identifier(),
        "name": name or None,
        "first_name": first or None,
        "last_name": last or None,
        "organization": contact.organizationName() or None,
        "job_title": contact.jobTitle() or None,
    }

    # Emails
    emails = []
    for labeled in contact.emailAddresses():
        val = labeled.value()
        label = _clean_label(labeled.label())
        if val:
            emails.append({"label": label, "value": str(val)})
    if emails:
        result["emails"] = emails

    # Phones
    phones = []
    for labeled in contact.phoneNumbers():
        val = labeled.value().stringValue()
        label = _clean_label(labeled.label())
        if val:
            phones.append({"label": label, "value": val})
    if phones:
        result["phones"] = phones

    if include_details:
        # Addresses
        addresses = []
        for labeled in contact.postalAddresses():
            addr = labeled.value()
            label = _clean_label(labeled.label())
            formatted = Contacts.CNPostalAddressFormatter.stringFromPostalAddress_style_(addr, 0)
            if formatted:
                addresses.append({"label": label, "value": str(formatted)})
        if addresses:
            result["addresses"] = addresses

        # Birthday
        bday = contact.birthday()
        if bday:
            month = bday.month()
            day = bday.day()
            year = bday.year()
            if year and year < 9999:
                result["birthday"] = f"{year:04d}-{month:02d}-{day:02d}"
            else:
                result["birthday"] = f"{month:02d}-{day:02d}"

        # Notes (requires entitlement on macOS 13+)
        if has_notes_access:
            try:
                result["notes"] = contact.note() or None
            except Exception:
                result["notes"] = None
        else:
            result["notes"] = None

        # Department
        dept = contact.departmentName()
        if dept:
            result["department"] = str(dept)

        # URLs
        urls = []
        for labeled in contact.urlAddresses():
            val = labeled.value()
            if val:
                urls.append({"label": _clean_label(labeled.label()), "value": str(val)})
        if urls:
            result["urls"] = urls

        # Social profiles
        socials = []
        try:
            for labeled in contact.socialProfiles():
                profile = labeled.value()
                if profile:
                    entry = {
                        "service": str(profile.service() or ""),
                        "username": str(profile.username() or ""),
                    }
                    url = profile.urlString()
                    if url:
                        entry["url"] = str(url)
                    if entry["service"] or entry["username"]:
                        socials.append(entry)
        except Exception:
            pass
        if socials:
            result["social_profiles"] = socials

        # Instant messaging
        ims = []
        try:
            for labeled in contact.instantMessageAddresses():
                im = labeled.value()
                if im:
                    entry = {
                        "service": str(im.service() or ""),
                        "username": str(im.username() or ""),
                    }
                    if entry["service"] or entry["username"]:
                        ims.append(entry)
        except Exception:
            pass
        if ims:
            result["instant_messages"] = ims

        # Related names (spouse, assistant, etc.)
        relations = []
        try:
            for labeled in contact.contactRelations():
                rel = labeled.value()
                label = _clean_label(labeled.label())
                if rel:
                    relations.append(
                        {
                            "label": label,
                            "name": str(rel.name() or ""),
                        }
                    )
        except Exception:
            pass
        if relations:
            result["relations"] = relations

        # Dates (anniversary, etc.)
        dates = []
        try:
            for labeled in contact.dates():
                val = labeled.value()
                label = _clean_label(labeled.label())
                if val:
                    month = val.month()
                    day = val.day()
                    year = val.year()
                    if year and year < 9999:
                        date_str = f"{year:04d}-{month:02d}-{day:02d}"
                    else:
                        date_str = f"{month:02d}-{day:02d}"
                    dates.append({"label": label, "value": date_str})
        except Exception:
            pass
        if dates:
            result["dates"] = dates

        # Has photo
        try:
            if contact.imageDataAvailable():
                result["has_photo"] = True
        except Exception:
            pass

    return result


def _clean_label(label):
    """Clean up Apple's internal label format."""
    if not label:
        return None
    label = label.replace("_$!<", "").replace(">!$_", "")
    return label or None
