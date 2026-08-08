# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Status**: ACTIVE
**Last Updated**: August 7, 2026

## Overview

Apple Mail MCP server for Claude Code. Reads, searches, archives, deletes, marks, composes, and replies to email across all accounts. Non-Gmail accounts (iCloud, Exchange, IMAP) go through ScriptingBridge to Mail.app for every operation; Gmail-authorized accounts are Gmail-API-only (issue #50) for list, search, compose, reply, archive, delete, mark read/unread, and unread counts (issue #101) - flag and move stay ScriptingBridge-only for all accounts, including Gmail, as a deliberate exception (see MCP Tools table). No credentials required for Apple Mail access - macOS TCC handles it. Gmail OAuth tokens stored in `~/.config/mail-tools/`.

## Setup

```bash
# Install
pip install -e .

# Register MCP server (user scope)
claude mcp add -s user mail -- mail-mcp
```

On first run, macOS prompts for Automation access to Mail.app. Grant it once; TCC remembers the decision.

For the optional Gmail API integration (list/search/compose/reply/archive/delete/mark, see Two backends below):
```bash
# Authorize Gmail OAuth (opens browser)
mail-mcp  # then call gmail_authorize tool
```

## Architecture

```
mail_tools/
├── __init__.py
├── __main__.py
├── mail.py          # MailManager - ScriptingBridge to Mail.app
├── gmail.py         # GmailClient - OAuth2 via Gmail API
├── rules.py         # Sender-rules persistence (sender_rules.json) - load/save/query-build/staleness
├── search.py        # Deep search combining Mail.app + Gmail
└── mcp_server.py    # FastMCP server (JSON output)
```

**Two backends**: Mail.app via ScriptingBridge for standard operations and `mail_deep_search` (reads Mail.app's local SQLite Envelope Index directly); the Gmail API handles `mail_archive`/`mail_delete`/`mail_mark_read`/`mail_mark_unread`/`mail_list`/`mail_search`/`mail_compose`/`mail_reply`/`mail_unread`/`mail_bulk_action`/`mail_apply_rules`/label management for Gmail-authorized accounts, batched via `GmailClient.batch_modify()` (chunked `messages.batchModify`, <=1000 ids/call). `mail_flag` and `mail_move` stay ScriptingBridge-only for every account including Gmail: Gmail has no folder-move equivalent, and its STARRED-label equivalent needs label support `mail_flag` doesn't use.

**Gmail-internal id resolution**: Gmail-authorized accounts return bare hex ids (no `"@"`) from list/search/bulk_action, not rfc822 Message-IDs. `mail_read`/`mail_archive`/`mail_delete`/`mail_mark_read`/`mail_mark_unread` resolve them through up to three passes: a per-account INBOX scan, an `rfc822msgid:` search, then a direct per-account `message_exists()`/`get_message()` lookup for a message no longer in INBOX (already archived, or filtered straight to a label on arrival). A Gmail-authorized account's id that fails all three passes reports `not_found` rather than falling back to a ScriptingBridge mailbox scan - that fallback would reintroduce the per-account brute force the Gmail-API migration (issue #50) exists to remove. `MailManager._call_gmail_batch()` wraps every per-account Gmail API call across all three passes, so one dead/expired token is logged and skipped rather than aborting resolution for ids belonging to other, healthy accounts.

**`mail_read` body extraction**: `_extract_plain_text_body()` does a deliberate two-pass MIME walk - pass 1 searches every nested multipart sibling for a `text/plain` part with no HTML fallback, and only if that comes up empty does pass 2 allow HTML. This matters for a `multipart/mixed` containing an HTML-only `multipart/related` (inline images) ordered before a `multipart/alternative` with both parts: a single-pass walk would return the first sibling's HTML and never inspect the second sibling's real plain text. HTML fallback content is returned as raw, unstripped markup (a known gap). Truncation (`MAX_CONTENT_LENGTH`) applies uniformly regardless of backend.

**Bulk operations**: `mail_bulk_action` queries Gmail's index directly via `search_ids()` (native query syntax, paginated `messages.list`) and mutates via `batch_modify()` - no per-message Mail.app listing step, which is what keeps "clean up everything from sender X" cheap at 1000+ messages. `mail_apply_rules` layers persisted sender categories (`~/.config/mail-tools/sender_rules.json`: `senders`, `default_action` of `archive`/`mark_read`/`archive_and_mark_read`/`hold`, `status` of `active`/`paused`/`leave_alone`/`needs_review`, optional `apply_label`) on top of `bulk_action()`, one call per active category; a `hold` category is skipped entirely unless it carries `apply_label`, even when named explicitly in `category_ids`.

**Label lifecycle**: `mail_labels`, `mail_bulk_action`'s `apply_label`/`remove_label`, `mail_label_delete`, and `mail_label_rename` all share `GmailClient.find_label()` for lookup (an instance-cached `email -> {name: id}` map); only `create_label()`/`resolve_label_id()` create on a miss, and `create_label()` treats a 409 as success by re-listing, so creating the same label twice never raises or duplicates. Deleting or renaming a label never touches a message's INBOX/UNREAD/TRASH state - deletion issues only a `DELETE` call, renaming only a `PATCH`; neither can carry a `batchModify`.

### Prompt injection guarding

`mail_read`/`mail_search`/`mail_deep_search` return raw email content (subject, sender, body) into the model's context alongside this server's write-capable tools, making an attacker-controlled email an indirect-prompt-injection vector. Guarded via the `prompt-security-utils` library - see the root CLAUDE.md's "Prompt injection guarding" section for the shared mechanism (markers, `SecurityConfig`, install-weight caveat). mail-tools' own wrapped fields:

| Tool | Wrapped fields |
|------|-----------------|
| `mail_list` | `subject`, `from` |
| `mail_read` | `subject`, `from`, `content` |
| `mail_search` | `subject`, `from` (per result) |
| `mail_deep_search` | `subject`, `sender_name` (per result; `sender_email` left unwrapped - not free-text authored content) |
| `mail_reply` | `subject` (echoed from the source message) |
| `mail_bulk_action` | `subject`, `from` (dry-run `sample`, up to 10) |
| `mail_apply_rules` | `subject`, `from` (per-category dry-run `sample`, up to 10) |

`_wrap_message_list()` in `mcp_server.py` is the shared helper behind every list-shaped wrap site above, parameterized by `fields` and by `fallback_id` (the id to fall back to when `message_id` is absent, e.g. `deep_search:<index>` for `mail_deep_search`).

## MCP Tools

| Tool | Description |
|------|-------------|
| `mail_accounts` | All configured mail accounts |
| `mail_mailboxes(account?)` | Mailboxes with unread/total counts |
| `mail_unread(account?)` | Unread message counts per account - Gmail-authorized accounts route through the Gmail API (issue #101, live per-label `messagesUnread` via `users.labels.get`) instead of ScriptingBridge's `mb.unreadCount()`, which reads Mail.app's locally IMAP-synced state and can lag behind Gmail's actual server-side unread count; non-Gmail accounts (e.g. iCloud) are unaffected |
| `mail_list(mailbox?, account?, limit?, unread_only?)` | List messages - non-Gmail accounts always batch-serialize the full target mailbox before sorting/filtering/limiting; Gmail-authorized accounts route through the Gmail API (INBOX cases, and an explicit account's arbitrary mailbox), which fetches only a single page (padded when `unread_only=true`). Each result's `subject`/`from` are wrapped with session security markers (issue #115) |
| `mail_read(message_id)` | Read a message body - automatically resolves Gmail-internal ids (issue #87): an id with no "@" tries every Gmail-authorized account via the API before falling back to ScriptingBridge's slower all-mailboxes scan; an rfc822-style id (has "@") goes straight to ScriptingBridge, unaffected. `subject`/`from`/`content` are wrapped with session security markers before being returned (issue #115, see Prompt injection guarding above) |
| `mail_search(query, mailbox?, account?)` | Substring match on subject/sender via Mail.app for non-Gmail accounts and non-INBOX mailboxes; for Gmail-authorized accounts searched with mailbox=INBOX (default), `query` is Gmail's own full-text search instead - not the same search semantics, see CLAUDE.md. Each result's `subject`/`from` are wrapped with session security markers (issue #115) |
| `mail_deep_search(query, max_results?)` | Full-text search via Mail.app's local SQLite Envelope Index. Each result's `subject`/`sender_name` are wrapped with session security markers (issue #115); `sender_email` is left unwrapped |
| `mail_compose(to, subject, body, cc?, bcc?, account?)` | Compose and send |
| `mail_reply(message_id, body, reply_all?)` | Reply to a message. `subject` in the response is wrapped with session security markers (issue #115) |
| `mail_mark_read(message_ids)` | Mark one or more messages as read (comma-separated; Gmail-authorized accounts are Gmail-API-only end to end - never fall back to a ScriptingBridge mailbox scan, even when unresolved by the API. Resolves a Gmail-internal id for a message no longer in INBOX via a direct per-account lookup (issue #89) before giving up. Non-Gmail accounts get a ScriptingBridge mailbox-scan fallback for messages already filed elsewhere) |
| `mail_mark_unread(message_ids)` | Mark one or more messages as unread (same batching/fallback as `mail_mark_read`) |
| `mail_flag(message_ids, flagged?)` | Flag or unflag one or more messages (comma-separated; ScriptingBridge-only for every account including Gmail - deliberate exception to the #50 Gmail-API-only migration, since Gmail's STARRED-label equivalent needs label support; `mail_bulk_action`/`mail_apply_rules` now support arbitrary custom labels (issue #58), but `mail_flag` itself still targets only the built-in STARRED flag and remains ScriptingBridge-only) |
| `mail_archive(message_ids)` | Archive one or more messages (comma-separated; Gmail accounts via the API remove the INBOX label; others fall back to mark-as-read. Resolves a Gmail-internal id for a message no longer in INBOX - e.g. already archived, or filtered straight to a category/label on arrival - via a direct per-account lookup (issue #89) before reporting not found) |
| `mail_move(message_id, mailbox)` | Move to a mailbox (ScriptingBridge-only for every account including Gmail - deliberate exception to the #50 Gmail-API-only migration, since Gmail has no folder-move equivalent; for a Gmail account prefer `mail_archive`/`mail_delete`, or `mail_bulk_action`/`mail_apply_rules` with `apply_label` (issue #58) to tag a message with a custom label instead of moving it) |
| `mail_delete(message_ids)` | Delete one or more messages (comma-separated; known issue - prefer mail_move to Trash. Same Gmail-internal-id direct-lookup fallback as `mail_archive` (issue #89)) |
| `mail_bulk_action(account, query, action, confirm?, apply_label?, remove_label?)` | Bulk archive/mark-read/label Gmail messages matching a native Gmail search query (Gmail-API-only, no ScriptingBridge fallback). Defaults to a dry run (`confirm=false`) returning `matched_count` + a sample of up to 10 matches, each with `subject`/`from` wrapped with session security markers (issue #115), plus `apply_label`/`remove_label` echoed back when set; `confirm=true` re-runs the search fresh and executes via chunked `batchModify` (this response has no `sample` field, so nothing to wrap). `action` is one of `archive`, `mark_read`, `archive_and_mark_read`, `hold` (no archive/read mutation) - no delete action. `apply_label` (issue #58) applies a custom Gmail label to every match, creating it first if it doesn't already exist (idempotent); the response then also carries `label_created` (true only if this call created the label). `remove_label` (issue #85) removes a custom Gmail label from every match in the same `batchModify` call, never creating it - naming a label that doesn't exist is a safe no-op reported via `remove_label_found: False`, not an error. Combine `apply_label` and `remove_label` together to swap matches from one label to another in one call, or combine either with `action="hold"` to tag/untag matches without archiving or marking read. `action="hold"` with neither `apply_label` nor `remove_label` raises, since it would be a fully no-op call |
| `mail_apply_rules(account, category_ids?, confirm?)` | Apply persisted sender-rule categories (`~/.config/mail-tools/sender_rules.json`) to an account, calling `mail_bulk_action`'s logic once per targeted `status=="active"` category (default: all active categories, or an explicit comma-separated `category_ids` subset). Categories with `status != "active"` are always skipped before any mutation, even if named explicitly in `category_ids`; a `default_action == "hold"` category is also skipped UNLESS it carries an `apply_label` (issue #58), in which case it is deliberately targeted as a label-only category. Dry run (`confirm=false`, default) returns per-category `matched_count` plus a `sample` of up to 10 matches (each with `subject`/`from` wrapped with session security markers, issue #115) and a `stale_reviews` nudge (any category whose `review_after_days` has elapsed, regardless of status); `confirm=true` executes and writes `last_reviewed` back only for categories actually run |
| `mail_labels(account)` | List every label (system + custom) for a Gmail-authorized account - `{id, name, type}` per label (issue #85) |
| `mail_label_delete(account, name, confirm?)` | Delete a custom Gmail label by name (issue #85). Idempotent: a name that doesn't resolve returns `{"label": name, "found": False}` in either `confirm` state, never an error. `confirm=false` (default) previews `messages_affected` without deleting; `confirm=true` deletes after computing that same count. Can never change a message's INBOX/UNREAD/TRASH state, structurally - only ever issues a `DELETE users/me/labels/{id}` call |
| `mail_label_rename(account, old_name, new_name)` | Rename a custom Gmail label in place, preserving its Gmail-internal id (issue #85) - every message that carried it keeps it under the new name, via a single `PATCH`, no `batchModify` needed. Unlike `mail_label_delete`, a not-found `old_name` raises rather than silently no-opping, since it names one specific target |
| `gmail_authorize` | Start Gmail OAuth flow |
| `gmail_status(verify?)` | Check Gmail OAuth token status. `verify=true` (default) makes one `users.getProfile` call per authorized account to confirm the token actually still works, since a token-file entry alone doesn't mean the refresh token hasn't been revoked/expired; returns `live: true/false/null` per account (`null` when `verify=false`, or when the live check itself failed for a reason unrelated to the token, e.g. a network outage - see that account's `error`). `verify=false` skips the network calls entirely (offline, no API quota) |

## Key Patterns

- Prefer batch operations - rapid sequential ScriptingBridge calls can spike Mail.app CPU
- Collecting "which messages match these ids" from a mailbox uses `arrayByApplyingSelector_("messageId")` (one IPC call) instead of a per-message `.messageId()` loop - see `_match_messages_by_id()` in `mail.py`
- Use `mail_move` to Trash instead of `mail_delete` - delete has a known issue
- Gmail OAuth tokens cached at `~/.config/mail-tools/gmail_tokens.json` (600 permissions)
- Gmail bulk label changes go through `GmailClient.batch_modify()`, chunked to <=1000 ids per `messages.batchModify` call. A 429/quota-403 chunk failure backs off and retries the whole chunk (up to `RATE_LIMIT_MAX_RETRIES`, then raises); any other chunk failure (e.g. one bad id) falls back to a per-message retry
- All tools return structured JSON; errors return `{"error": "..."}`
