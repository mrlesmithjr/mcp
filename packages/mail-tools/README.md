# Mail Tools

Apple Mail MCP server for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Read, search, compose, and manage email across all accounts configured in macOS Mail.app - iCloud, Gmail, Exchange, IMAP, and more through a single interface.

Uses [ScriptingBridge](https://developer.apple.com/documentation/scriptingbridge) via [PyObjC](https://pyobjc.readthedocs.io/) to communicate with Mail.app. No API keys, OAuth tokens, or IMAP credentials needed for basic operation - if the account works in Mail.app, it works here.

For Gmail accounts, an optional [Gmail API integration](#gmail-api-setup-optional) replaces ScriptingBridge for nearly every operation - list, search, compose, reply, archive, delete, mark read/unread, and unread counts - since ScriptingBridge can't properly remove Gmail's INBOX label, can lag behind Gmail's actual unread state, and the two backends otherwise diverge in what they can do. Flagging and moving messages to another mailbox remain ScriptingBridge-only for Gmail accounts (see [Tools](#tools)) - Gmail has no folder-move equivalent, and flag support needs Gmail label integration that doesn't exist yet.

**macOS only** - requires Mail.app (macOS 12+).

## Setup

### From Source

```bash
git clone https://github.com/mrlesmithjr/mcp.git
cd mcp/packages/mail-tools
uv tool install --editable .
```

Mail.app will auto-launch when the server is first used. On first use, macOS may prompt for Automation access - grant it in **System Settings > Privacy & Security > Automation**.

## MCP Server

Register as a Claude Code MCP server:

```bash
claude mcp add -s user mail-tools -- mail-mcp
```

Or add manually to your Claude Code `settings.json`:

```json
{
  "mcpServers": {
    "mail-tools": {
      "command": "python",
      "args": ["-m", "mail_tools.mcp_server"]
    }
  }
}
```

## Tools

**Mailbox & Account Info**

| Tool | Description |
|------|-------------|
| `mail_accounts` | List all configured accounts with email addresses |
| `mail_mailboxes` | List mailboxes with unread and total message counts |
| `mail_unread` | Unread counts per account and mailbox - Gmail-authorized accounts get live counts via the Gmail API instead of Mail.app/ScriptingBridge, which can lag behind Gmail's actual state ([issue #101](https://github.com/mrlesmithjr/mcp/issues/101)) |

**Reading & Searching**

| Tool | Description |
|------|-------------|
| `mail_list` | List messages from a mailbox (unified inbox or specific); Gmail-authorized accounts list via the Gmail API |
| `mail_read` | Read full message content by ID - automatically resolves Gmail-internal ids via the Gmail API (tried across every authorized account) before falling back to ScriptingBridge for rfc822-style ids (different semantics - see CLAUDE.md) |
| `mail_search` | Search messages by subject/sender substring; Gmail-authorized accounts searched with mailbox=INBOX use Gmail's own full-text search instead (different semantics - see CLAUDE.md) |
| `mail_deep_search` | Search ALL mail across ALL accounts and mailboxes via SQLite index |

**Composing**

| Tool | Description |
|------|-------------|
| `mail_compose` | Create a new email (draft or send) |
| `mail_reply` | Reply or reply-all to a message |

**Organizing**

| Tool | Description |
|------|-------------|
| `mail_mark_read` | Mark a message as read - uses Gmail API for Gmail-authorized accounts (never falls back to a ScriptingBridge mailbox scan for those; resolves an id no longer in INBOX via a direct per-account Gmail lookup first), ScriptingBridge (with a mailbox-scan fallback) for others |
| `mail_mark_unread` | Mark a message as unread - same routing as `mail_mark_read` |
| `mail_flag` | Flag or unflag a message - ScriptingBridge-only for every account, including Gmail; `mail_bulk_action`/`mail_apply_rules` gained custom Gmail label support ([issue #58](https://github.com/mrlesmithjr/mcp/issues/58)), but `mail_flag` still targets only the built-in STARRED flag and stays ScriptingBridge-only |
| `mail_move` | Move a message to a different mailbox - ScriptingBridge-only for every account, including Gmail (deliberate exception; Gmail has no folder-move equivalent, use `mail_archive`/`mail_delete` instead) |
| `mail_archive` | Archive messages - uses Gmail API for Gmail accounts, ScriptingBridge for others. For a Gmail-internal id no longer in INBOX (already archived, or filtered straight to a label on arrival), resolves the owning account via a direct Gmail lookup before giving up ([issue #89](https://github.com/mrlesmithjr/mcp/issues/89)) |
| `mail_delete` | Move messages to trash - uses Gmail API for Gmail accounts, ScriptingBridge for others. Same direct-lookup fallback as `mail_archive` ([issue #89](https://github.com/mrlesmithjr/mcp/issues/89)) |
| `mail_bulk_action` | Bulk archive/mark-read/label Gmail messages matching a native Gmail search query (Gmail-API-only); `apply_label` tags matches with a custom Gmail label, creating it if needed, `remove_label` untags them (never creates - a nonexistent name is a safe no-op), and both can be combined with each other or with `action="hold"` to label/unlabel without archiving or marking read. Defaults to a dry run |
| `mail_apply_rules` | Apply persisted sender-rule categories (`~/.config/mail-tools/sender_rules.json`) to an account, one `mail_bulk_action` call per active category. Defaults to a dry run |
| `mail_labels` | List every label (system + custom) for a Gmail-authorized account |
| `mail_label_delete` | Delete a custom Gmail label by name (idempotent - deleting an already-gone name returns `found: false`, never an error); defaults to a dry run reporting how many messages carry it |
| `mail_label_rename` | Rename a custom Gmail label in place, preserving its id so every tagged message keeps it under the new name; a not-found name raises (unlike `mail_label_delete`) |

**Gmail API Management**

| Tool | Description |
|------|-------------|
| `gmail_authorize` | Authorize a Gmail account for API access (one-time, opens browser) |
| `gmail_status(verify?)` | Show which Gmail accounts are authorized for API access. Defaults to a live check (one lightweight API call per account) so a revoked/expired token is reported as dead rather than looking identical to a working one; pass `verify=false` to skip the network calls and only check token-file presence |

## Usage Examples

Once registered, you can ask Claude things like:

- "How many unread emails do I have?"
- "Show me my latest emails"
- "Read the Amazon order confirmation"
- "Search for emails from the dentist"
- "Find that receipt from last month" (uses deep search across all mailboxes)
- "Draft a reply to that last email"
- "Archive all the newsletters"
- "Move that to trash"

### Compose Behavior

By default, `mail_compose` and `mail_reply` create **drafts** - they do not send automatically. Pass `send=true` to send immediately. This is a safety measure to let you review before sending.

### Pairing with Other Tools

Mail Tools pairs naturally with other Apple MCP servers:

- **[contacts-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/contacts-tools)** - "Email the dentist about rescheduling" (looks up contact, finds email, composes message)
- **[apple-eventkit-tools](https://github.com/mrlesmithjr/mcp/tree/main/packages/apple-eventkit-tools)** - "Create a calendar event from that appointment confirmation email" or "Create a follow-up reminder for that email"

## Gmail API Setup (Optional)

By default, mail-tools uses ScriptingBridge (Mail.app) for **all** operations. This works for reading, searching, composing, and organizing mail across all account types.

However, several Gmail operations can't work correctly through ScriptingBridge, mainly because it can't remove INBOX labels from Gmail messages or reach anything outside the account's Mail.app-cached mailboxes. Without the Gmail API:

- **Archive** falls back to marking messages as read (they stay in your inbox)
- **Delete** works via ScriptingBridge but may behave unexpectedly
- **List/search** are limited to what Mail.app has locally cached, with ScriptingBridge's substring-only search
- **Compose/reply** send through Mail.app's outgoing-message flow instead of the Gmail API
- **Mark read/unread** works via ScriptingBridge exactly like any other account, including the mailbox-scan fallback for messages Mail.app can't find in INBOX
- **Unread counts** read Mail.app's locally IMAP-synced mailbox state, which can lag far behind Gmail's actual server-side unread count, and require Mail.app to be running to answer at all

With the Gmail API enabled:

- **Archive** properly removes the INBOX label (messages move to All Mail)
- **Delete** moves messages to Trash via the API
- **List/search** query the Gmail API directly, with `mail_search` using Gmail's native full-text query engine instead of a subject/sender substring match (different semantics - see `CLAUDE.md`)
- **Compose/reply** send or draft through the Gmail API, with reply-threading that keeps the message in the same Gmail conversation
- **Mark read/unread** batch through the API, with `not_found` reported honestly instead of a ScriptingBridge fallback attempt
- **Unread counts** come from live per-label `messagesUnread` via the Gmail API, not Mail.app's locally cached state ([issue #101](https://github.com/mrlesmithjr/mcp/issues/101))
- **Auto-sync** triggers Mail.app to refresh after API operations

`mail_flag` and `mail_move` stay ScriptingBridge-only regardless of Gmail API setup - see [Tools](#tools) for why.

**If you only use iCloud, Exchange, or other non-Gmail accounts, skip this section entirely.**

### Step 1: Install the Google Cloud CLI

```bash
brew install --cask google-cloud-sdk
```

After installation, ensure it's on your PATH:

```bash
export PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH"
```

Add that line to your shell profile (`~/.zshrc` or `~/.bashrc`) for persistence.

### Step 2: Create a Google Cloud Project

Choose a project name (e.g., `mail-tools-mcp`) and run:

```bash
gcloud auth login
gcloud projects create YOUR-PROJECT-NAME --name="Mail Tools MCP"
gcloud config set project YOUR-PROJECT-NAME
gcloud services enable gmail.googleapis.com
```

Replace `YOUR-PROJECT-NAME` with your chosen name throughout the remaining steps.

### Step 3: Configure the OAuth Consent Screen

This step must be done in the browser - Google doesn't expose it via CLI.

1. Open `https://console.cloud.google.com/apis/credentials/consent?project=YOUR-PROJECT-NAME`
2. **App Information:**
   - **App name:** `Mail Tools MCP`
   - **User support email:** select your email from the dropdown
   - Click **Next**
3. **Audience:**
   - Select **External**
   - Click **Next**
4. **Contact Information:**
   - Enter your email address
   - Click **Next**
5. Click **Create**

After creation, add your Gmail accounts as **test users**. Since the app stays in "testing" mode (never published), only test users can authorize:

1. On the consent screen page, find the **Audience** or **Test users** section
2. Add each Gmail address you want to use with mail-tools

### Step 4: Create OAuth Client Credentials

1. Open `https://console.cloud.google.com/apis/credentials/oauthclient?project=YOUR-PROJECT-NAME`
2. **Application type:** select **Desktop app**
3. **Name:** `Mail Tools MCP`
4. Click **Create**
5. Click **Download JSON** on the confirmation dialog
6. Move the downloaded file to the mail-tools config directory:

```bash
mkdir -p ~/.config/mail-tools
mv ~/Downloads/client_secret_*.json ~/.config/mail-tools/gmail_credentials.json
```

`gmail_credentials.json.example` in the repo shows the expected JSON shape if you need a reference.

### Step 5: Authorize Each Gmail Account

Use the `gmail_authorize` MCP tool (or call it directly) for each Gmail account:

```
gmail_authorize("user@gmail.com")
```

This opens a browser window for Google consent. **Make sure you sign into the correct Google account** - if you have multiple Gmail accounts, Google may default to a different one.

After approval:
- Tokens are stored at `~/.config/mail-tools/gmail_tokens.json`
- Tokens auto-refresh - you only do this once per account
- Check status anytime with `gmail_status`

### How the Gmail API Integration Works

When `mail_list`, `mail_search`, `mail_compose`, `mail_reply`, `mail_archive`, `mail_delete`, `mail_mark_read`, or `mail_mark_unread` processes a message or account:

1. Identifies which Mail.app account the message (or request) belongs to
2. Checks if the account is Gmail (has "All Mail" mailbox)
3. Checks if the Gmail API client has credentials and the account is authorized
4. **If all yes:** routes through the Gmail REST API instead of ScriptingBridge (proper INBOX label removal for archive, Trash for delete, native full-text search, etc. - see `CLAUDE.md` for the exact per-tool routing and where Gmail's semantics diverge from ScriptingBridge's, e.g. `mail_search`)
5. **If any no:** falls back to ScriptingBridge (mark-as-read for archive, ScriptingBridge delete, ScriptingBridge substring search, etc.)

`mail_flag` and `mail_move` are the one deliberate exception: they stay ScriptingBridge-only for every account, including Gmail (see [Tools](#tools)).

After Gmail API operations, Mail.app is automatically triggered to sync so changes appear immediately without manual refresh.

Non-Gmail accounts (iCloud, Exchange, IMAP) always use ScriptingBridge regardless of Gmail API setup.

### File Locations

| File | Purpose |
|------|---------|
| `~/.config/mail-tools/gmail_credentials.json` | OAuth2 client credentials (from Google Cloud Console) |
| `~/.config/mail-tools/gmail_tokens.json` | Per-account access/refresh tokens (auto-managed) |

These files contain sensitive credentials. They are stored outside the repository and should never be committed to version control.

## How It Works

**ScriptingBridge** is Apple's Objective-C/Python bridge for controlling applications via their scripting interface. Mail.app exposes accounts, mailboxes, messages, and outgoing message composition through this interface.

**`mail_search`** searches client-side against a single mailbox for non-Gmail accounts (and non-INBOX mailboxes on Gmail accounts). **`mail_deep_search`** queries Mail.app's SQLite index directly (`~/Library/Mail`) for instant results across all accounts and mailboxes, including archived mail.

**Gmail API** (optional) handles list, search, archive, delete, mark read/unread, compose, and reply for Gmail-authorized accounts via the REST API with OAuth2 authentication, in place of ScriptingBridge. Tokens auto-refresh and are stored locally.

## Requirements

- macOS 12+ (Monterey or later)
- Python 3.11+
- Mail.app configured with at least one account
- Terminal app granted Automation access (TCC permission)

### Optional (for full Gmail API integration - list, search, compose, reply, archive, delete, mark read/unread)

- Google Cloud project with Gmail API enabled
- OAuth2 Desktop credentials at `~/.config/mail-tools/gmail_credentials.json`
- `gcloud` CLI (`brew install --cask google-cloud-sdk`) - only needed for initial project setup

## Project Structure

```
mail_tools/
├── __init__.py      # Package init
├── __main__.py      # python -m mail_tools.mcp_server
├── mail.py          # ScriptingBridge bridge (MailManager class)
├── gmail.py         # Gmail REST API client (OAuth2)
├── rules.py         # Sender-rules persistence (sender_rules.json)
├── search.py        # SQLite deep search across all mailboxes
└── mcp_server.py    # FastMCP server (JSON output)
```

## License

MIT
