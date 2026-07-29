"""Google People API client (issue #60) - full REST replacement for
ContactsManager (contacts.py's CNContactStore-based PyObjC bridge) once
contact data has migrated to Google (personal-ops step, tracked outside
this repo).

Implements the same public method surface and return dict shapes as
ContactsManager so contacts_tools/mcp_server.py's tool functions need no
changes beyond swapping which manager class _contacts() instantiates.

Known shape deviation from ContactsManager: "addresses" is a structured
dict (label/street/city/state/postal_code/country) rather than
ContactsManager's single CNPostalAddressFormatter-formatted display
string - Google's People API returns structured fields natively, and
update_contact's addresses param already expected structured input, so
this is a strict improvement in usefulness even though it isn't a
byte-for-byte match of the old EventKit-era shape.
"""

from __future__ import annotations

import base64
import hashlib
import json as _json
import logging
import os
import re
import sqlite3
import time as _time
from datetime import datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from mcp_common.config import load_layered_config
from mcp_common.google_oauth import GoogleOAuthClient, GoogleOAuthError

logger = logging.getLogger(__name__)

PEOPLE_API = "https://people.googleapis.com/v1"

# Two-tier field mask, mirroring contacts.py's _SUMMARY_KEYS/_DETAIL_KEYS
# split - the People API silently omits any field not named in
# personFields (no error), so list/search only request what's needed for
# list-row display, and get_contact/update_contact request everything.
_SUMMARY_FIELDS = "names,emailAddresses,phoneNumbers,organizations"
_DETAIL_FIELDS = _SUMMARY_FIELDS + ",addresses,birthdays,urls,biographies,memberships,photos"

_ENV_MAP = {"account": "GOOGLE_PEOPLE_ACCOUNT"}


class ContactsError(Exception):
    """Raised when Google People API contact operations fail.

    Same name/role as contacts.py's ContactsError (issue #60 is a full
    replacement, not a dual-path) so mcp_server.py's broad
    `except Exception` handling needs no change.
    """


def _strip_prefix(resource_id: str) -> str:
    """ "people/{id}" or "contactGroups/{id}" -> bare id. A bare id passes through unchanged."""
    if "/" in resource_id:
        return resource_id.rsplit("/", 1)[-1]
    return resource_id


def _add_prefix(contact_id: str) -> str:
    """bare id -> "people/{id}". An already-prefixed id passes through unchanged."""
    if contact_id.startswith("people/"):
        return contact_id
    return f"people/{contact_id}"


def _add_group_prefix(group_id: str) -> str:
    if group_id.startswith("contactGroups/"):
        return group_id
    return f"contactGroups/{group_id}"


def _person_in_group(person: dict, group_resource_name: str) -> bool:
    for m in person.get("memberships", []) or []:
        cg = m.get("contactGroupMembership", {})
        if cg.get("contactGroupResourceName") == group_resource_name:
            return True
    return False


def _address_to_people(address: dict | str) -> dict:
    if isinstance(address, str):
        return {"streetAddress": address, "type": "home"}
    return {
        "streetAddress": address.get("street"),
        "city": address.get("city"),
        "region": address.get("state"),
        "postalCode": address.get("postal_code"),
        "country": address.get("country"),
        "type": (address.get("label") or "work").lower(),
    }


class GooglePeopleClient(GoogleOAuthClient):
    """Google People API client, same method surface as ContactsManager."""

    SCOPES = ["https://www.googleapis.com/auth/contacts"]

    def __init__(self):
        super().__init__(tool_name="contacts-tools", scopes=self.SCOPES)
        cfg = load_layered_config("contacts-tools", _ENV_MAP)
        self.account = cfg.get("account") or self._sole_authorized_account()
        self._gravatar_remaining = None
        self._gravatar_reset = None

    def _sole_authorized_account(self) -> str | None:
        """Account to use when none is configured.

        There is no meaningful hardcoded default here - the account is
        whichever Google account the installing user authorized. When exactly
        one is on file, use it (the overwhelmingly common single-account
        case). With zero or several, leave it unset so request() raises a
        clear "not authorized" error naming the account, rather than silently
        reading someone else's contacts. Override with GOOGLE_PEOPLE_ACCOUNT
        or config.json's "account" key.
        """
        accounts = self.list_authorized_accounts()
        return accounts[0] if len(accounts) == 1 else None

    # ── Groups ──

    def list_groups(self) -> list[dict]:
        """Return all contact groups (metadata only, not members) - always
        listed live, never hardcoded (system groups like myContacts/starred
        vary per account).
        """
        groups = []
        page_token = None
        while True:
            params = {"pageSize": "200"}
            if page_token:
                params["pageToken"] = page_token
            url = f"{PEOPLE_API}/contactGroups?{urlencode(params)}"
            result = self.request(self.account, "GET", url)
            for g in result.get("contactGroups", []):
                groups.append(
                    {"id": _strip_prefix(g.get("resourceName", "")), "name": g.get("formattedName") or g.get("name")}
                )
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        return sorted(groups, key=lambda g: g["name"] or "")

    def _resolve_group_resource_name(self, group_name: str) -> str | None:
        for g in self.list_groups():
            if (g["name"] or "").lower() == group_name.lower():
                return _add_group_prefix(g["id"])
        return None

    def add_to_group(self, contact_id: str, group_name: str) -> dict:
        """Add an existing contact to a group via contactGroups.members.modify."""
        group_resource_name = self._resolve_group_resource_name(group_name)
        if group_resource_name is None:
            raise ContactsError(f"Group not found: {group_name}")
        resource = _add_prefix(_strip_prefix(contact_id))
        body = {"resourceNamesToAdd": [resource]}
        self.request(self.account, "POST", f"{PEOPLE_API}/{group_resource_name}/members:modify", body=body)
        return {"contact_id": contact_id, "group": group_name, "added": True}

    # ── Listing/search ──

    def list_contacts(self, group: str | None = None, limit: int = 50) -> list[dict]:
        """List contacts, optionally filtered to a group.

        group has no native connections.list param - filtered client-side
        via memberships[].contactGroupMembership.contactGroupResourceName,
        cross-referenced against contactGroups.list.
        """
        group_resource_name = None
        person_fields = _SUMMARY_FIELDS
        if group:
            group_resource_name = self._resolve_group_resource_name(group)
            if group_resource_name is None:
                raise ContactsError(f"Group not found: {group}")
            person_fields = _SUMMARY_FIELDS + ",memberships"

        contacts = []
        page_token = None
        while True:
            params = {"personFields": person_fields, "pageSize": "1000"}
            if page_token:
                params["pageToken"] = page_token
            url = f"{PEOPLE_API}/people/me/connections?{urlencode(params)}"
            result = self.request(self.account, "GET", url)
            for person in result.get("connections", []):
                if group_resource_name and not _person_in_group(person, group_resource_name):
                    continue
                contacts.append(_serialize_person(person))
                if len(contacts) >= limit:
                    break
            if len(contacts) >= limit:
                break
            page_token = result.get("nextPageToken")
            if not page_token:
                break

        return sorted(contacts, key=lambda c: c.get("name") or "")

    def search_contacts(self, query: str, limit: int = 20) -> list[dict]:
        """Search contacts by name, email, or phone.

        searchContacts is prefix-matches only (max pageSize 30) - a
        client-side fallback scan over list_contacts() output covers
        non-prefix substring matches (phone/org), mirroring
        ContactsManager.search_contacts's two-pass structure.

        Gotcha: a cold searchContacts cache can silently miss recent
        contacts, so a warmup request with an empty query is sent first to
        refresh it before the real query.
        """
        results = []
        seen_ids = set()

        try:
            self.request(self.account, "GET", f"{PEOPLE_API}/people:searchContacts?query=&readMask={_SUMMARY_FIELDS}")
        except GoogleOAuthError:
            pass  # Warmup failures are not fatal - the real query below still runs.

        try:
            params = {"query": query, "readMask": _SUMMARY_FIELDS, "pageSize": str(min(limit, 30))}
            url = f"{PEOPLE_API}/people:searchContacts?{urlencode(params)}"
            result = self.request(self.account, "GET", url)
            for entry in result.get("results", []):
                person = entry.get("person", {})
                serialized = _serialize_person(person)
                if serialized["id"] not in seen_ids:
                    results.append(serialized)
                    seen_ids.add(serialized["id"])
        except GoogleOAuthError:
            pass

        if len(results) >= limit:
            return results[:limit]

        query_lower = query.lower()
        query_digits = "".join(c for c in query if c.isdigit())
        for c in self.list_contacts(limit=1000):
            if c["id"] in seen_ids or len(results) >= limit:
                continue
            matched = query_lower in (c.get("organization") or "").lower()
            if not matched:
                for e in c.get("emails", []):
                    if query_lower in (e.get("value") or "").lower():
                        matched = True
                        break
            if not matched and query_digits and len(query_digits) >= 3:
                for p in c.get("phones", []):
                    digits = "".join(ch for ch in (p.get("value") or "") if ch.isdigit())
                    if query_digits in digits:
                        matched = True
                        break
            if matched:
                results.append(c)
                seen_ids.add(c["id"])

        return results[:limit]

    def get_contact(self, contact_id: str) -> dict:
        """Get a single contact by ID with full details."""
        resource = _add_prefix(_strip_prefix(contact_id))
        params = {"personFields": _DETAIL_FIELDS}
        url = f"{PEOPLE_API}/{resource}?{urlencode(params)}"
        person = self.request(self.account, "GET", url)
        if not person or not person.get("resourceName"):
            raise ContactsError(f"Contact not found: {contact_id}")
        return _serialize_person(person, include_details=True)

    # ── Create/update/delete ──

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
        """Create a new contact via people:createContact.

        Checks for duplicates by name and email first, same contract as
        ContactsManager.create_contact.
        """
        full_name = f"{first_name or ''} {last_name or ''}".strip()
        if full_name:
            dupes = self.search_contacts(full_name, limit=1)
            if dupes:
                existing = dupes[0]
                raise ContactsError(
                    f"Duplicate contact found (id: {existing['id']}). "
                    f"Use contact_search or contact_detail to review it, then contact_update to modify it instead."
                )

        if email:
            email_lower = email.lower()
            for d in self.search_contacts(email_lower, limit=5):
                for e in d.get("emails", []):
                    if (e.get("value") or "").lower() == email_lower:
                        raise ContactsError(
                            f"Duplicate contact found with email '{email}' (id: {d['id']}). "
                            f"Use contact_search or contact_detail to review it, then contact_update "
                            f"to modify it instead."
                        )

        # 400 error if singleton fields (names, birthdays, genders,
        # biographies) get multiple values - each is a single-entry list.
        body = {}
        if first_name or last_name:
            body["names"] = [{"givenName": first_name or "", "familyName": last_name or ""}]
        if organization or job_title:
            body["organizations"] = [{"name": organization or "", "title": job_title or ""}]
        if email:
            body["emailAddresses"] = [{"value": email, "type": email_label}]
        if phone:
            body["phoneNumbers"] = [{"value": phone, "type": phone_label}]
        if address:
            body["addresses"] = [_address_to_people(address)]
        if notes:
            body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]

        person = self.request(self.account, "POST", f"{PEOPLE_API}/people:createContact", body=body)

        # createContact's response is NOT reliable for building the return
        # value: like updateContact's response, it is not guaranteed to
        # reflect every field without an explicit personFields mask on this
        # call (createContact accepts no personFields param at all), so a
        # naive _serialize_person(person, include_details=True) here can
        # silently return a near-empty contact even though the create
        # succeeded. Always re-fetch via get_contact() with the full detail
        # mask - the same "never trust the mutate response" pattern
        # update_contact() already uses for its own PATCH response.
        created_id = _strip_prefix(person.get("resourceName", ""))
        if not created_id:
            raise ContactsError(f"createContact response missing resourceName: {person}")
        result = self.get_contact(created_id)

        group_added = False
        if group:
            try:
                self.add_to_group(result["id"], group)
                group_added = True
            except ContactsError:
                logger.warning("Group '%s' not found; contact created without group", group)
        result["group_added"] = group_added
        return result

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
        """Update an existing contact's fields via people:updateContact.

        Requires the etag from a prior get (optimistic-concurrency check -
        a stale/missing etag fails the write), so this always re-fetches
        the contact first. updatePersonFields excludes photos - see
        enrich_contact/_download_and_set_photo for the separate
        updateContactPhoto path. notes maps to biographies.

        social_profiles has no dedicated People API field - it is folded
        into `urls` (type set to the social service name), the closest
        writable equivalent; this is a real shape simplification versus
        ContactsManager's native CNSocialProfile support.
        """
        resource = _add_prefix(_strip_prefix(contact_id))
        existing = self.request(self.account, "GET", f"{PEOPLE_API}/{resource}?personFields={_DETAIL_FIELDS}")
        if not existing or not existing.get("resourceName"):
            raise ContactsError(f"Contact not found: {contact_id}")

        body = {"resourceName": resource, "etag": existing.get("etag")}
        update_fields = []

        if first_name is not None or last_name is not None:
            current_name = (existing.get("names") or [{}])[0]
            body["names"] = [
                {
                    "givenName": first_name if first_name is not None else current_name.get("givenName", ""),
                    "familyName": last_name if last_name is not None else current_name.get("familyName", ""),
                }
            ]
            update_fields.append("names")

        if organization is not None or job_title is not None or department is not None:
            current_org = (existing.get("organizations") or [{}])[0]
            body["organizations"] = [
                {
                    "name": organization if organization is not None else current_org.get("name", ""),
                    "title": job_title if job_title is not None else current_org.get("title", ""),
                    "department": department if department is not None else current_org.get("department", ""),
                }
            ]
            update_fields.append("organizations")

        if phones is not None:
            body["phoneNumbers"] = [{"value": p["value"], "type": p.get("label", "Other")} for p in phones]
            update_fields.append("phoneNumbers")

        if emails is not None:
            body["emailAddresses"] = [{"value": e["value"], "type": e.get("label", "Other")} for e in emails]
            update_fields.append("emailAddresses")

        if addresses is not None:
            body["addresses"] = [_address_to_people(a) for a in addresses]
            update_fields.append("addresses")

        url_entries = list(body.get("urls", []))
        if urls is not None:
            url_entries.extend({"value": u["value"], "type": u.get("label", "other")} for u in urls)
        if social_profiles is not None:
            url_entries.extend(
                {"value": sp.get("url", ""), "type": sp.get("service", "other")} for sp in social_profiles
            )
        if url_entries:
            body["urls"] = url_entries
            update_fields.append("urls")

        if notes is not None:
            body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]
            update_fields.append("biographies")

        if not update_fields:
            return self.get_contact(contact_id)

        params = {"updatePersonFields": ",".join(update_fields)}
        patch_url = f"{PEOPLE_API}/{resource}:updateContact?{urlencode(params)}"
        self.request(self.account, "PATCH", patch_url, body=body)
        return self.get_contact(contact_id)

    def delete_contact(self, contact_id: str) -> dict:
        """Delete a contact via people:deleteContact."""
        resource = _add_prefix(_strip_prefix(contact_id))
        self.request(self.account, "DELETE", f"{PEOPLE_API}/{resource}:deleteContact")
        return {"contact_id": contact_id, "deleted": True}

    # ── Duplicates / merge ──

    def find_duplicates(self, limit: int = 50) -> list[dict]:
        """Find duplicate contacts by name, email, or phone - pure Python
        scan over list_contacts() output, ported unchanged from
        ContactsManager.find_duplicates.
        """
        all_contacts = self.list_contacts(limit=2000)

        by_name, by_email, by_phone = {}, {}, {}
        for c in all_contacts:
            name = (c.get("name") or "").strip().lower()
            if name:
                by_name.setdefault(name, []).append(c)
            for e in c.get("emails", []):
                email_val = (e.get("value") or "").lower().strip()
                if email_val:
                    by_email.setdefault(email_val, []).append(c)
            for p in c.get("phones", []):
                digits = "".join(ch for ch in (p.get("value") or "") if ch.isdigit())
                if len(digits) >= 7:
                    key = digits[-10:] if len(digits) >= 10 else digits
                    by_phone.setdefault(key, []).append(c)

        results = []
        seen_groups = set()
        for index, match_type in [(by_name, "name"), (by_email, "email"), (by_phone, "phone")]:
            for key, contacts in index.items():
                if len(contacts) < 2:
                    continue
                unique = {c["id"]: c for c in contacts}
                if len(unique) < 2:
                    continue
                group_key = frozenset(unique.keys())
                if group_key in seen_groups:
                    continue
                seen_groups.add(group_key)
                results.append({"match_type": match_type, "match_value": key, "contacts": list(unique.values())})
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
        return results

    def merge_contacts(self, keep_id: str, merge_ids: list) -> dict:
        """Merge duplicate contacts into one via get x2 -> combine fields in
        Python -> updateContact (primary) -> deleteContact (each merged id).
        No native People API merge endpoint.
        """
        primary = self.get_contact(keep_id)

        existing_emails = {e["value"].lower() for e in primary.get("emails", [])}
        existing_phones = {"".join(ch for ch in p["value"] if ch.isdigit())[-10:] for p in primary.get("phones", [])}
        new_emails = list(primary.get("emails", []))
        new_phones = list(primary.get("phones", []))
        new_addresses = list(primary.get("addresses", []))

        update_kwargs: dict = {}
        merged_ids = []

        for merge_id in merge_ids:
            try:
                merge_contact = self.get_contact(merge_id)
            except ContactsError:
                logger.warning("Merge contact not found: %s, skipping", merge_id)
                continue

            if not primary.get("first_name") and merge_contact.get("first_name"):
                update_kwargs["first_name"] = primary["first_name"] = merge_contact["first_name"]
            if not primary.get("last_name") and merge_contact.get("last_name"):
                update_kwargs["last_name"] = primary["last_name"] = merge_contact["last_name"]
            if not primary.get("organization") and merge_contact.get("organization"):
                update_kwargs["organization"] = primary["organization"] = merge_contact["organization"]
            if not primary.get("job_title") and merge_contact.get("job_title"):
                update_kwargs["job_title"] = primary["job_title"] = merge_contact["job_title"]

            for e in merge_contact.get("emails", []):
                v = (e.get("value") or "").lower()
                if v and v not in existing_emails:
                    new_emails.append(e)
                    existing_emails.add(v)
            for p in merge_contact.get("phones", []):
                digits = "".join(ch for ch in (p.get("value") or "") if ch.isdigit())
                normalized = digits[-10:] if len(digits) >= 10 else digits
                if normalized and normalized not in existing_phones:
                    new_phones.append(p)
                    existing_phones.add(normalized)
            new_addresses.extend(merge_contact.get("addresses", []))

            merged_ids.append(merge_id)

        if not merged_ids:
            raise ContactsError("No valid contacts to merge - check that all merge_ids exist")

        update_kwargs["emails"] = new_emails
        update_kwargs["phones"] = new_phones
        if new_addresses:
            update_kwargs["addresses"] = new_addresses

        merged_contact = self.update_contact(keep_id, **update_kwargs)

        deleted_ids = []
        for merge_id in merged_ids:
            try:
                self.delete_contact(merge_id)
                deleted_ids.append(merge_id)
            except ContactsError:
                logger.warning("Failed to delete merged contact: %s", merge_id)

        return {"contact": merged_contact, "merged": len(deleted_ids), "deleted": deleted_ids}

    # ── Enrichment (Gravatar/PDL HTTP lookups are backend-agnostic; ported
    # unchanged from ContactsManager. Only the avatar-set path differs -
    # see _download_and_set_photo.) ──

    def enrich_contact(
        self, contact_id: str, apply: bool = False, pdl_api_key: str | None = None, gravatar_api_key: str | None = None
    ) -> dict:
        contact = self.get_contact(contact_id)
        emails = contact.get("emails", [])
        if not emails:
            raise ContactsError("Contact has no email addresses to enrich from")

        email = emails[0]["value"]
        sources = []

        gravatar_data = self._enrich_gravatar(email, api_key=gravatar_api_key)
        if gravatar_data:
            sources.append({"source": "gravatar", "data": gravatar_data})

        if pdl_api_key:
            pdl_data = self._enrich_pdl(email, pdl_api_key)
            if pdl_data:
                sources.append({"source": "peopledatalabs", "data": pdl_data})

        applied = False
        if apply and sources:
            update_kwargs = self._build_enrichment_update(contact, sources)
            avatar_url = update_kwargs.pop("_avatar_url", None)
            if update_kwargs:
                self.update_contact(contact_id, **update_kwargs)
                applied = True
            if avatar_url and self._download_and_set_photo(contact_id, avatar_url):
                applied = True

        return {"contact_id": contact_id, "email": email, "sources": sources, "applied": applied}

    def _gravatar_has_avatar(self, email):
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
        sha256_hash = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        md5_hash = hashlib.md5(email.strip().lower().encode()).hexdigest()

        result = {}

        if not skip_avatar_check:
            avatar = self._gravatar_has_avatar(email)
            if avatar:
                result["avatar_url"] = avatar
        else:
            result["avatar_url"] = f"https://www.gravatar.com/avatar/{md5_hash}?s=400"

        if self._gravatar_remaining is not None and self._gravatar_remaining <= 0:
            reset_in = max(0, (self._gravatar_reset or 0) - _time.time())
            logger.warning("Gravatar rate limit exhausted; resets in %.0fs", reset_in)
            return None

        profile_url = f"https://api.gravatar.com/v3/profiles/{sha256_hash}"
        resp = None
        for _attempt in range(3):
            try:
                req = Request(profile_url)
                req.add_header("User-Agent", "contacts-tools/1.0")
                if api_key:
                    req.add_header("Authorization", f"Bearer {api_key}")
                resp = urlopen(req, timeout=5)
                remaining = resp.headers.get("X-RateLimit-Remaining")
                reset = resp.headers.get("X-RateLimit-Reset")
                if remaining is not None:
                    self._gravatar_remaining = int(remaining)
                if reset is not None:
                    self._gravatar_reset = int(reset)
                break
            except HTTPError as e:
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

                for src_key, dst_key in (
                    ("display_name", "display_name"),
                    ("description", "about"),
                    ("location", "location"),
                    ("job_title", "job_title"),
                    ("company", "company"),
                    ("pronunciation", "pronunciation"),
                    ("pronouns", "pronouns"),
                    ("profile_url", "profile_url"),
                ):
                    if data.get(src_key):
                        result[dst_key] = data[src_key]

                profiles = []
                for account in data.get("verified_accounts", []):
                    url = account.get("url", "")
                    username = ""
                    if url:
                        path = url.rstrip("/").split("/")[-1]
                        username = path.lstrip("@") if path else ""
                    profiles.append({"service": account.get("service_label", ""), "username": username, "url": url})
                if profiles:
                    result["social_profiles"] = profiles

                urls = []
                for link in data.get("links", []):
                    urls.append({"title": link.get("label", ""), "value": link.get("url", "")})
                if urls:
                    result["urls"] = urls

        except (HTTPError, URLError):
            pass

        return result if result else None

    def _enrich_pdl(self, email, api_key):
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
                for src_key, dst_key in (
                    ("full_name", "full_name"),
                    ("job_title", "job_title"),
                    ("job_company_name", "company"),
                    ("industry", "industry"),
                    ("location_name", "location"),
                ):
                    if data.get(src_key):
                        result[dst_key] = data[src_key]

                profiles = []
                for field, service in (
                    ("linkedin_url", "LinkedIn"),
                    ("twitter_url", "Twitter"),
                    ("facebook_url", "Facebook"),
                    ("github_url", "GitHub"),
                ):
                    if data.get(field):
                        profiles.append({"service": service, "url": data[field]})
                if profiles:
                    result["social_profiles"] = profiles

                if data.get("phone_numbers"):
                    result["phone_numbers"] = data["phone_numbers"]

                return result if result else None

        except (HTTPError, URLError) as e:
            logger.warning(f"PDL enrichment failed: {e}")

        return None

    def _build_enrichment_update(self, existing_contact, sources):
        kwargs = {}
        all_socials = []
        all_urls = []

        for source in sources:
            data = source["data"]
            if not existing_contact.get("job_title") and data.get("job_title"):
                kwargs["job_title"] = data["job_title"]
            if not existing_contact.get("organization") and data.get("company"):
                kwargs["organization"] = data["company"]
            for sp in data.get("social_profiles", []):
                all_socials.append(sp)
            if data.get("profile_url"):
                all_urls.append({"label": "Other", "value": data["profile_url"]})
            for u in data.get("urls", []):
                all_urls.append({"label": "Other", "value": u.get("value", u.get("url", ""))})

        if all_socials:
            kwargs["social_profiles"] = all_socials
        if all_urls and not existing_contact.get("urls"):
            kwargs["urls"] = all_urls

        for source in sources:
            avatar_url = source["data"].get("avatar_url")
            if avatar_url and not existing_contact.get("has_photo"):
                kwargs["_avatar_url"] = avatar_url
                break

        return kwargs

    def _download_and_set_photo(self, contact_id, avatar_url) -> bool:
        """Download an avatar image and set it via People API's separate
        updateContactPhoto endpoint (updatePersonFields excludes photos
        from the regular updateContact call).
        """
        try:
            req = Request(avatar_url)
            req.add_header("User-Agent", "contacts-tools/1.0")
            resp = urlopen(req, timeout=10)
            if resp.status == 200:
                image_data = resp.read()
                photo_b64 = base64.b64encode(image_data).decode()
                resource = _add_prefix(_strip_prefix(contact_id))
                body = {"photoBytes": photo_b64}
                self.request(self.account, "PATCH", f"{PEOPLE_API}/{resource}:updateContactPhoto", body=body)
                logger.info("Set photo for contact %s", contact_id)
                return True
        except (HTTPError, URLError) as e:
            logger.warning(f"Failed to download avatar: {e}")
        except GoogleOAuthError as e:
            logger.warning("Failed to set contact photo via People API: %s", e)
        return False

    def enrich_all(
        self, apply: bool = False, limit: int = 50, pdl_api_key: str | None = None, gravatar_api_key: str | None = None
    ) -> dict:
        contacts = self.list_contacts(limit=5000)
        with_email = [c for c in contacts if c.get("emails")][:limit]

        avatar_hits = {}
        for c in with_email:
            email = c["emails"][0]["value"]
            avatar = self._gravatar_has_avatar(email)
            if avatar:
                avatar_hits[c["id"]] = email
        logger.info(f"Avatar sweep: {len(avatar_hits)} hits out of {len(with_email)} contacts")

        hits = []
        misses = 0
        applied_count = 0

        for c in with_email:
            email = c["emails"][0]["value"]
            sources = []

            gravatar_data = (
                self._enrich_gravatar(email, api_key=gravatar_api_key, skip_avatar_check=True)
                if c["id"] in avatar_hits
                else None
            )
            if gravatar_data:
                sources.append({"source": "gravatar", "data": gravatar_data})

            if pdl_api_key:
                pdl_data = self._enrich_pdl(email, pdl_api_key)
                if pdl_data:
                    sources.append({"source": "peopledatalabs", "data": pdl_data})

            if sources:
                hit = {"contact_id": c["id"], "name": c.get("name"), "email": email, "sources": sources}
                if apply:
                    try:
                        existing = self.get_contact(c["id"])
                        update_kwargs = self._build_enrichment_update(existing, sources)
                        avatar_url = update_kwargs.pop("_avatar_url", None)
                        contact_applied = False
                        if update_kwargs:
                            self.update_contact(c["id"], **update_kwargs)
                            contact_applied = True
                        if avatar_url and self._download_and_set_photo(c["id"], avatar_url):
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

    # ── Mail-cross-reference (no Contacts/People API calls beyond
    # self.get_contact/self.list_contacts/self.search_contacts - already
    # written against the manager's abstract interface, ported unchanged) ──

    def find_incomplete(self, missing: str = "any", limit: int = 50) -> dict:
        all_contacts = self.list_contacts(limit=5000)
        detailed = [self.get_contact(c["id"]) for c in all_contacts]

        results = []
        for serialized in detailed:
            name = serialized.get("name")
            if not name:
                continue

            has_phone = bool(serialized.get("phones"))
            has_email = bool(serialized.get("emails"))
            has_address = bool(serialized.get("addresses"))
            has_photo = bool(serialized.get("has_photo"))

            missing_fields = []
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

        return {"contacts": results, "count": len(results), "filter": missing}

    def find_last_interaction(self, contact_id: str | None = None, name: str | None = None, limit: int = 10) -> dict:
        mail_db_main = _locate_mail_db()
        if not mail_db_main:
            raise ContactsError("Mail.app database not found")

        if contact_id:
            targets = [self.get_contact(contact_id)]
        elif name:
            targets = [self.get_contact(sr["id"]) for sr in self.search_contacts(name, limit=limit)]
        else:
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
            received_rows = conn.execute(
                """
                SELECT LOWER(a.address) AS email, MAX(m.date_sent) AS last_date
                FROM messages m
                JOIN addresses a ON m.sender = a.ROWID
                WHERE a.address IS NOT NULL AND a.address != ''
                GROUP BY LOWER(a.address)
                """
            ).fetchall()
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
            days_ago = (datetime.now() - last_interaction).days if last_interaction else None

            results.append(
                {
                    "id": contact.get("id"),
                    "name": contact.get("name"),
                    "email": emails[0]["value"] if emails else None,
                    "last_sent": best_sent.isoformat() if best_sent else None,
                    "last_received": best_received.isoformat() if best_received else None,
                    "last_interaction": last_interaction.isoformat() if last_interaction else None,
                    "days_ago": days_ago,
                }
            )

        results.sort(key=lambda x: x.get("last_interaction") or "", reverse=True)

        if not contact_id and not name:
            results = results[:limit]

        return {"contacts": results, "count": len(results)}

    def find_stale_contacts(self, days: int = 365, limit: int = 50) -> dict:
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

        def _parse_interaction(val):
            if not val:
                return datetime.min
            try:
                return datetime.fromisoformat(val)
            except (TypeError, ValueError):
                return datetime.min

        stale.sort(key=lambda x: _parse_interaction(x.get("last_interaction")))

        return {"contacts": stale, "count": len(stale), "threshold_days": days}

    def find_unknown_senders(self, days: int = 90, min_count: int = 2, limit: int = 30) -> dict:
        mail_db_main = _locate_mail_db()
        if not mail_db_main:
            raise ContactsError("Mail.app database not found")

        all_contacts = self.list_contacts(limit=5000)
        known_emails = set()
        for c in all_contacts:
            for e in c.get("emails", []):
                addr = (e.get("value") or "").strip().lower()
                if addr:
                    known_emails.add(addr)

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

        senders = []
        for row in rows:
            email = (row["email"] or "").strip().lower()
            if not email or email in known_emails or noreply_patterns.search(email):
                continue

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

        return {"senders": senders, "count": len(senders), "scanned_days": days}

    # ── Export ──

    def export_contacts(
        self, group: str | None = None, contact_ids: list | None = None, output_path: str | None = None
    ) -> dict:
        """Export contacts to vCard (.vcf) format - hand-built via
        _build_vcard, since the People API has no vCard export endpoint.
        """
        output_path = (
            os.path.expanduser(output_path) if output_path else os.path.expanduser("~/Desktop/contacts-export.vcf")
        )

        if contact_ids:
            summary = [{"id": cid} for cid in contact_ids]
        elif group:
            summary = self.list_contacts(group=group, limit=5000)
        else:
            summary = self.list_contacts(limit=5000)

        # Full details (birthday/address/notes) require a per-contact get -
        # the connections.list summary fields don't carry them.
        contacts_list = [self.get_contact(c["id"]) for c in summary]

        if not contacts_list:
            raise ContactsError("No contacts found to export")

        vcard_data = "".join(_build_vcard(c) for c in contacts_list)

        output_dir = os.path.dirname(output_path)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(vcard_data)

        return {"path": output_path, "count": len(contacts_list), "exported": True}


# ── Helpers ──


def _locate_mail_db() -> str | None:
    """Locate Mail.app's Envelope Index across known version directories."""
    for v in ["V10", "V11", "V9", "V8"]:
        candidate = os.path.expanduser(f"~/Library/Mail/{v}/MailData/Envelope Index")
        if os.path.exists(candidate):
            return candidate
    return None


def _serialize_person(person: dict, include_details: bool = False) -> dict:
    """Convert a People API Person resource to the same dict shape as
    contacts.py's _serialize_contact.
    """
    resource_name = person.get("resourceName", "")
    contact_id = _strip_prefix(resource_name)

    names = person.get("names") or [{}]
    primary_name = names[0]
    first = primary_name.get("givenName") or ""
    last = primary_name.get("familyName") or ""
    name = (primary_name.get("displayName") or f"{first} {last}".strip()) or None

    orgs = person.get("organizations") or [{}]
    primary_org = orgs[0]

    result = {
        "id": contact_id,
        "name": name,
        "first_name": first or None,
        "last_name": last or None,
        "organization": primary_org.get("name") or None,
        "job_title": primary_org.get("title") or None,
    }

    emails = [
        {"label": e.get("type") or "other", "value": e["value"]}
        for e in person.get("emailAddresses", []) or []
        if e.get("value")
    ]
    if emails:
        result["emails"] = emails

    phones = [
        {"label": p.get("type") or "other", "value": p["value"]}
        for p in person.get("phoneNumbers", []) or []
        if p.get("value")
    ]
    if phones:
        result["phones"] = phones

    if include_details:
        addresses = [
            {
                "label": a.get("type") or "other",
                "street": a.get("streetAddress"),
                "city": a.get("city"),
                "state": a.get("region"),
                "postal_code": a.get("postalCode"),
                "country": a.get("country"),
            }
            for a in person.get("addresses", []) or []
        ]
        if addresses:
            result["addresses"] = addresses

        birthdays = person.get("birthdays") or []
        if birthdays:
            date = birthdays[0].get("date", {})
            year, month, day = date.get("year"), date.get("month"), date.get("day")
            if month and day:
                result["birthday"] = f"{year:04d}-{month:02d}-{day:02d}" if year else f"{month:02d}-{day:02d}"

        bios = person.get("biographies") or []
        result["notes"] = bios[0].get("value") if bios else None

        department = primary_org.get("department")
        if department:
            result["department"] = department

        urls = [
            {"label": u.get("type") or "other", "value": u["value"]}
            for u in person.get("urls", []) or []
            if u.get("value")
        ]
        if urls:
            result["urls"] = urls

        photos = person.get("photos") or []
        if photos:
            result["has_photo"] = any(p.get("url") for p in photos)

        result["etag"] = person.get("etag")

    return result


def _escape_vcard(value: str) -> str:
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _build_vcard(contact: dict) -> str:
    """Hand-build a vCard 3.0 record from an already-fetched contact dict -
    the People API has no vCard export endpoint.
    """
    lines = ["BEGIN:VCARD", "VERSION:3.0"]

    first = contact.get("first_name") or ""
    last = contact.get("last_name") or ""
    name = contact.get("name") or f"{first} {last}".strip() or "Unknown"
    lines.append(f"FN:{_escape_vcard(name)}")
    lines.append(f"N:{_escape_vcard(last)};{_escape_vcard(first)};;;")

    if contact.get("organization"):
        lines.append(f"ORG:{_escape_vcard(contact['organization'])}")
    if contact.get("job_title"):
        lines.append(f"TITLE:{_escape_vcard(contact['job_title'])}")

    for e in contact.get("emails", []):
        lines.append(f"EMAIL;TYPE={(e.get('label') or 'OTHER').upper()}:{e.get('value', '')}")
    for p in contact.get("phones", []):
        lines.append(f"TEL;TYPE={(p.get('label') or 'OTHER').upper()}:{p.get('value', '')}")
    for a in contact.get("addresses", []):
        label = (a.get("label") or "OTHER").upper()
        street = _escape_vcard(a.get("street") or "")
        city = _escape_vcard(a.get("city") or "")
        state = _escape_vcard(a.get("state") or "")
        postal = _escape_vcard(a.get("postal_code") or "")
        country = _escape_vcard(a.get("country") or "")
        lines.append(f"ADR;TYPE={label}:;;{street};{city};{state};{postal};{country}")

    if contact.get("notes"):
        lines.append(f"NOTE:{_escape_vcard(contact['notes'])}")
    for u in contact.get("urls", []):
        lines.append(f"URL:{u.get('value', '')}")

    bday = contact.get("birthday")
    if bday and len(bday) == 10:
        lines.append(f"BDAY:{bday.replace('-', '')}")

    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"
