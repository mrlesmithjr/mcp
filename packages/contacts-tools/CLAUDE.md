# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: 2026-07-11

## Overview

Contacts MCP server for Claude Code. As of issue #60, all contact tools go through the Google People API (`GooglePeopleClient`) - a full replacement of the CNContactStore-based `ContactsManager`, no dual-path. Requires OAuth (see `google_people_authorize`/`google_people_status` below) with credentials shared at `~/.config/google/credentials.json` across all Google-using packages. `contacts.py`'s `ContactsManager` (native PyObjC `Contacts` framework) remains in the codebase and is still directly unit-tested, but `mcp_server.py`'s `_contacts()` no longer instantiates it.

## Setup

```bash
# Install
pip install -e .

# Register MCP server (user scope)
claude mcp add -s user contacts -- contacts-mcp
```

Copy the shared Google OAuth client credentials to `~/.config/google/credentials.json` (same file used by mail-tools' Gmail integration and apple-eventkit-tools' Calendar integration - one GCP app registration, People API enabled on the same project), then run `google_people_authorize` once.

**Before the first authorize**, the People API must be enabled on that GCP project - it isn't on by default even though the project already has Gmail API (and possibly Calendar API) enabled. This is a one-time Google Cloud requirement, not something the code can do for you: enable it at `console.cloud.google.com/apis/library/people.googleapis.com`, or `gcloud services enable people.googleapis.com` if using the CLI. Skipping this step surfaces as a 403 error on the first live API call, not at authorize time.

## Architecture

```
contacts_tools/
├── __init__.py
├── __main__.py
├── contacts.py       # ContactsManager - CNContactStore via PyObjC (no longer wired into mcp_server.py by default)
├── google_people.py  # GooglePeopleClient(GoogleOAuthClient) - Google People API (issue #60)
└── mcp_server.py     # FastMCP server (JSON output)
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `contact_groups` | List all contact groups |
| `contact_list(group?, limit?)` | List contacts, optionally filtered by group. Free-text fields (name, first_name, last_name, organization, job_title, and emails/phones/urls values) are wrapped with session security markers (issue #120) |
| `contact_search(query, limit?)` | Search by name, email, or phone. Same wrapping as `contact_list` |
| `contact_detail(contact_id)` | Full contact record. Same wrapping as `contact_list`, plus notes/department/addresses |
| `contact_create(first_name, last_name?, ...)` | Create a new contact. Not wrapped - the response is freshly created from the caller's own arguments, not pre-existing content |
| `contact_update(contact_id, ...)` | Update contact fields. Returns the FULL re-fetched contact including untouched fields - wrapped the same as `contact_detail` (issue #120) |
| `contact_delete(contact_id)` | Delete a contact |
| `contact_add_to_group(contact_id, group_name)` | Add contact to a group |
| `contact_merge(keep_id, merge_ids)` | Merge duplicate contacts (client-side field-combine, no native People API merge endpoint). Merged contact wrapped the same as `contact_detail` |
| `contact_duplicates` | Find likely duplicate contacts. Nested contacts wrapped the same as `contact_list`; `match_value` (the matched name/email/phone) is also wrapped |
| `contact_incomplete(missing?)` | Find contacts missing key info (phone, email, address, or photo). `name`/`organization` wrapped |
| `contact_stale(days?)` | Contacts with no email interaction in N days. `name`/`email` wrapped |
| `contact_last_interaction(contact_id)` | Last known interaction date. `name`/`email` wrapped |
| `contact_enrich(contact_id)` | Enrich a contact with details from online sources (photo, social, company). `email` and each `sources[].data` (freshly-fetched external content) wrapped (issue #120) |
| `contact_enrich_all` | Enrich all incomplete contacts. Each hit's `name`/`email`/`sources[].data` wrapped, same as `contact_enrich` |
| `contact_unknown_senders` | Find frequent email senders not in your contacts (cross-references Mail). `email`/`name` wrapped - raw, unauthenticated email header data |
| `contact_export(group?, contact_ids?)` | Export contacts to vCard (.vcf): all, by group, or specific contacts (hand-built - no People API vCard endpoint). Not wrapped - writes to a local file, the JSON response carries only a path |
| `google_people_authorize(account?)` | Authorize the Google account for People API access |
| `google_people_status(verify?)` | Show Google People API authorization status |

### Prompt Injection Guarding (issue #120)

Contact data returned by this server isn't necessarily authored by the account owner: contacts can be auto-created from email interactions (`contact_unknown_senders` exists specifically for this), shared/synced from other sources, or proposed by `contact_enrich`'s external lookups - the same vulnerability class closed for mail-tools in issue #115 and sheets-tools in issue #118, since this server also exposes write-capable tools (`contact_create`, `contact_update`, `contact_add_to_group`, `contact_merge`, `contact_enrich`/`contact_enrich_all` with `apply=True`, `contact_delete`) in the same conversation. `mcp_server.py` adopts `prompt-security-utils` the same way as mail-tools/sheets-tools: `generate_markers()` at module load, `security_instructions()` folded into `FastMCP(instructions=...)` (appended after, not replacing, the pre-existing "Always use contact_search before contact_create..." guidance), and `_wrap_untrusted_field()`/`_wrap_contact()`/`_wrap_contact_list()`/`_wrap_fields_in_list()`/`_wrap_enrichment_sources()` wrap fields at the tool-return boundary before `json.dumps`. `SecurityConfig(semantic_enabled=False, llm_screen_enabled=False)` matches mail-tools/sheets-tools.

**`contact_enrich`'s preview data (issue #120's key design question):** `contact_enrich`/`contact_enrich_all`'s `sources[].data` is freshly-fetched external content (Gravatar and, if `PDL_API_KEY` is set, PeopleDataLabs), proposed for the contact record before - or, when `apply=False`, instead of - ever being written. This is structurally distinct from the already-stored `Person` fields `_wrap_contact()` guards: it hasn't been written to the contact at all, and its shape varies per source (Gravatar returns `avatar_url`/`display_name`/`about`/`social_profiles`/etc, PDL returns a different key set) with no fixed field list to wrap by name. `_wrap_enrichment_sources()` mirrors sheets-tools' `_wrap_values` blob strategy for the same "no fixed shape" reason: the whole `data` dict is JSON-serialized and wrapped as one unit via `wrap_external_data()`, regardless of `apply` - the content remains untrusted even after being written, since the write itself doesn't validate or sanitize it.

**Full sweep result (all 19 tools checked):**
- **Wrapped**: `contact_list`/`contact_search` (name, first_name, last_name, organization, job_title, emails/phones/urls `value`), `contact_detail`/`contact_update`/`contact_merge` (the above plus notes, department, addresses' street/city/state/postal_code/country), `contact_duplicates` (nested contacts plus `match_value`), `contact_enrich`/`contact_enrich_all` (email/name plus `sources[].data` as one blob per source), `contact_incomplete` (name, organization), `contact_last_interaction`/`contact_stale` (name, email), `contact_unknown_senders` (email, name - raw Mail header data, not even Google-vetted).
- **Not wrapped - Google's controlled vocabulary, not free text**: `emails[].label`/`phones[].label`/`addresses[].label`/`urls[].label` are the People API's `type` field (home/work/other/custom); `_serialize_person()` maps `type` straight to `label` and never reads `customType`, so even a "custom" label surfaces as the literal string `"custom"`, not attacker-suppliable text.
- **Not wrapped - opaque/structural, not free text**: `id` (People API resourceName), `etag` (optimistic-concurrency token, only present via `contact_detail`/nested contact fetches), `has_photo` (bool), `birthday` (numeric year/month/day, not a free-text string).
- **Not wrapped - self-authored, not synced from an external party**: `contact_groups`' `name` - Google Contacts groups are created via the account owner's own UI action or are one of Google's own predefined system groups (myContacts, starred), never derived from a shared/synced contact's data the way a tab title or contact field can be.
- **Not wrapped - freshly created from the caller's own arguments**: `contact_create`'s response (re-fetched after the write, same reasoning sheets-tools applied to `sheet_create`) and `contact_add_to_group`/`contact_delete`'s caller-supplied `contact_id`/`group` echoes.
- **Not wrapped - no free-text contact content in the response**: `contact_export` (path/count only - the vCard itself goes to disk), `google_people_authorize`/`google_people_status` (local OAuth/config state).
- **Fixed (issue #120 follow-up)**: `create_contact()`'s duplicate-name/duplicate-email pre-checks (`google_people.py`) used to raise `ContactsError` with the *existing* conflicting contact's name interpolated into the message text (e.g. `f"Duplicate contact found: '{existing.get('name')}' ..."`), which reached the model unwrapped via `contact_create`'s generic `except Exception as e: return json.dumps({"error": str(e)})` handler - pre-existing contact data leaking through an error path rather than a response field. Rather than wrapping only the name segment of an otherwise-trusted, already-formatted exception string (fragile - would need `ContactsError` to carry structured data separately from its message), the fix drops the conflicting contact's name from both messages entirely, keeping only its `id`, and points the caller at `contact_search`/`contact_detail` to look it up (where it comes back wrapped). The email-match branch keeps the caller's own `email` argument in the message (their own search input, not pre-existing untrusted data) but drops the matched contact's name the same way.

## Key Patterns

- Always call `contact_search` before `contact_create` to avoid duplicates (enforced via MCP instructions)
- `GooglePeopleClient` (active backend since issue #60): `create_contact` re-fetches the new contact via `get_contact()` after `people:createContact` returns, rather than trusting the create response - `createContact` accepts no `personFields` mask, so its raw response can silently omit fields even though the create succeeded (same "never trust the mutate response" pattern as `update_contact`); `update_contact` requires re-fetching the contact's `etag` first (People API's optimistic-concurrency check - a stale/missing etag fails the write); `updatePersonFields` excludes `photos`, so avatar writes go through the separate `updateContactPhoto` endpoint (`_download_and_set_photo`); `search_contacts` sends a throwaway empty-query warmup request before the real query to avoid a cold-cache miss on recently added contacts; `social_profiles` has no dedicated People API field and is folded into `urls` as a simplification (see `update_contact`'s docstring)
- `GooglePeopleClient` only ever talks to one configured account (defaults to the sole authorized account, overridable via `~/.config/contacts-tools/config.json` or `GOOGLE_PEOPLE_ACCOUNT` env var) - not a per-call multi-account client
- The bullets below describe `ContactsManager` (`contacts.py`), which remains in the codebase and is still directly unit-tested but is no longer instantiated by `mcp_server.py`'s `_contacts()`
- `ContactsManager` initializes `CNContactStore` lazily via a `store` property - the store is created and TCC access is requested on first use, and `self._store` is only assigned after access is confirmed
- `update_contact` re-fetches the full contact via `get_contact()` after saving, so the returned record is always complete. If the notes entitlement is missing (macOS 13+), the save is retried without notes and a warning is logged; no error is raised.
- `merge_contacts` commits the primary update and all deletions in a single `CNSaveRequest` (atomic). If all `merge_ids` refer to non-existent contacts, it raises `ContactsError` rather than silently succeeding.
- `find_stale_contacts` compares interaction dates using `datetime.fromisoformat()` for correctness; the sort helper returns `datetime.min` for contacts with no interaction date, placing them first.
- `find_last_interaction` (and `find_stale_contacts` by extension, since it calls `find_last_interaction(limit=500)`) issues exactly 2 grouped SQL queries against Mail's Envelope Index total - one for received, one for sent - regardless of contact count, instead of 2 queries per contact (issue #68). Matches `find_unknown_senders`'s existing pattern: `GROUP BY LOWER(address)` across the whole `messages` table in one pass per direction, then results are matched back to each contact's email(s) in Python.
- `search_contacts` and `list_contacts` halt full-store enumeration early once `limit` is reached by raising the internal `_EnumerationDone` sentinel exception from inside `enum_cb` and catching it around the `enumerateContactsWithFetchRequest_error_usingBlock_` call - filtering happens inside the callback to avoid accumulating the full contact list in memory. Do not write to the callback's `stop` parameter: in this environment's PyObjC/pyobjc-framework-Contacts version, `stop` is always `None`, not the mutable `BOOL *` wrapper the API appears to promise (issue #80).
- `create_contact` returns a top-level `group_added: bool` field when a `group_name` is supplied. If the group name does not match any existing group or the store write fails, `group_added: false` is returned and a warning is logged; no error is raised.
- `enrich_all` fetches up to 5000 contacts before filtering by email presence, then applies the caller-supplied `limit` ceiling. The old hard cap of 500 silently skipped contacts in larger address books.
- `_enrich_gravatar` is non-blocking: pre-request rate-limit exhaustion returns `None` immediately, and 429 retries log a warning and continue without sleeping. Do not add `time.sleep()` calls here - the MCP server is single-threaded and sleeping stalls all enrichment.
- All tools return structured JSON; errors are returned as `{"error": "..."}` rather than raising
