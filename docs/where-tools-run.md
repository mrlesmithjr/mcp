# Where the tools run: the three surfaces

Every tool in this workspace can run in three different places, and **each one reads a
different MCP registry**. Setting a tool up in one surface does **not** make it available in
the others.

This is the single biggest source of confusion: a tool can work in Cowork while regular
Desktop chat reports it as "not set up", because those are two separate registries that do
not know about each other.

## The three surfaces at a glance

| Surface | Registry it reads | Where you configure it | Stays current on its own? |
|---------|-------------------|------------------------|---------------------------|
| **Claude Code** (CLI, IDE extension, and the Claude Code panel in Desktop) | `mcpServers` in `~/.claude.json` (user scope) | `dev/register_dev.py` (dev machine) or `/plugin install` (consumer) | Yes if pointed at `.venv`; otherwise `/plugin update` |
| **Regular Claude Desktop chat** (the normal chat window) | `claude_desktop_config.json` -> `mcpServers` | Settings -> Developer -> Local MCP servers -> Edit Config | Yes if pointed at `.venv` |
| **Cowork** (Desktop's agent / local-agent mode) | `@inline` plugins | Settings -> Plugins (Add / Browse / Update) | **No** -- a frozen clone that needs a manual Update |

Claude Code and regular chat launch a binary directly, so on a dev machine you point them at
the workspace `.venv` and they are always current. Cowork installs a **frozen copy** of the
plugin and must be updated by hand after each release.

## Golden rules (read these first)

1. **Each surface is independent.** Installing a tool in Cowork does not put it in regular
   chat, and vice versa. To have a tool in all three, set it up in all three.
2. **"Not set up" in regular chat almost always means the tool is only in Cowork's Plugins
   registry.** Fix: add it to `claude_desktop_config.json` (see below).
3. **On a dev machine (repo checked out), point everything you can at `.venv`.** Those entries
   self-update when you bump a version. Only Cowork forces a frozen copy.
4. **Regular Desktop chat needs a full quit and reopen** (Cmd+Q) to pick up config changes.
   Starting a new chat is not enough.

## Setting up each surface

### 1. Claude Code

**Dev machine (repo checked out):**

```bash
uv sync --all-packages
uv run python dev/register_dev.py      # registers <tool>-dev servers pointing at .venv/bin/
```

These are user scope, so they work in every Claude Code session (CLI, IDE, and Desktop's
Claude Code panel). They track `.venv`, so a version bump needs no re-registration.

**Consumer machine (no checkout):**

```bash
claude plugin marketplace add mrlesmithjr/mcp
claude plugin install <slug>@mrlesmithjr-mcp
```

Update later with `/plugin update <slug>@mrlesmithjr-mcp`.

Do **not** install the marketplace plugin on a dev machine; it builds a frozen copy and
hijacks your `~/.local/bin` CLIs. See `CLAUDE.md`.

### 2. Regular Claude Desktop chat

Open Settings -> Developer -> Local MCP servers -> **Edit Config** (this opens
`claude_desktop_config.json`) and add one entry per tool.

**Dev machine** -- point at the workspace `.venv` binary. Use an absolute path; Desktop does
not read your shell `PATH` and does not expand `~`:

```json
"lawnops": {
  "command": "/Users/<you>/Projects/Personal/mcp/.venv/bin/lawnops-mcp"
}
```

Some tools need environment variables the binary cannot infer. `obsidian-search-tools` needs
the vault path:

```json
"obsidian-search-tools": {
  "command": "/Users/<you>/Projects/Personal/mcp/.venv/bin/obsidian-search-tools-mcp",
  "env": { "OBSIDIAN_VAULT_PATH": "/Users/<you>/Obsidian/<Vault>" }
}
```

Tools that read a config file (`~/.config/<tool>/config.json`) need no `env` block.

**Consumer machine** -- install the package standalone, then point at that binary:

```bash
uv tool install mrlesmithjr-mcp-<slug>
```

Then use the installed binary path (for example `~/.local/bin/<tool>-mcp`) in the config.

**Quit and reopen Desktop** after editing.

### 3. Cowork

Settings -> Plugins -> **Add** (or **Browse** the marketplace) and install the plugin. It then
appears in the Plugins list with a Skills count and a Last updated date.

Cowork installs a **frozen venv clone**, so:

- After you release a new version, click **Update** on the Plugins screen. Cowork does not
  track `.venv` and does not auto-follow the repo.
- This is the only surface with an ongoing manual step.

## The binary names are not guessable

The MCP binary name does not always match the tool slug. Check `ls .venv/bin/*-mcp` (or the
package's `[project.scripts]`), or use this table:

| Tool (slug) | Binary |
|-------------|--------|
| lawnops | `lawnops-mcp` |
| weather-tools | `weather-tools-mcp` |
| obsidian-search-tools | `obsidian-search-tools-mcp` |
| ynab-tools | `ynab-mcp` |
| sheets-tools | `sheets-mcp` |
| nextdns-tools | `nextdns-mcp` |
| unifi-tools | `unifi-mcp` |
| apple-eventkit-tools | `ical-mcp` |
| contacts-tools | `contacts-mcp` |
| mail-tools | `mail-mcp` |
| imessage-tools | `imessage-mcp` |
| homeops | `homeops-mcp` |
| flightops | `flightops-mcp` |
| launchd-tools | `launchd-tools-mcp` |

Note the inconsistency: most drop `-tools` (`ynab-tools` -> `ynab-mcp`), some keep it
(`weather-tools` -> `weather-tools-mcp`), and `apple-eventkit-tools` is `ical-mcp`. Do not
guess; verify.

## Recipe: one tool, all three surfaces (dev machine)

1. **Claude Code:** `uv run python dev/register_dev.py`
2. **Regular chat:** add the `.venv/bin/<tool>-mcp` entry to `claude_desktop_config.json` (with
   an `env` block if the tool needs one), then quit and reopen Desktop.
3. **Cowork:** Settings -> Plugins -> Add -> install the plugin.

## Keeping current after a version bump

| Surface | Action needed |
|---------|---------------|
| Claude Code (`-dev`, `.venv`) | None. Self-updates |
| Regular chat (`.venv` config) | None. Self-updates; relaunch Desktop to restart the process |
| Claude Code (marketplace plugin) | `/plugin update <slug>@mrlesmithjr-mcp` |
| Cowork (`@inline` plugin) | Click **Update** on the Plugins screen |

## Troubleshooting

- **"Tool is not set up / not installed" in regular chat.** It is only in Cowork's Plugins
  registry. Add it to `claude_desktop_config.json` (Developer -> Local MCP servers) and
  relaunch Desktop.
- **Regular chat or Claude Code runs an old version.** If the entry points at `.venv` it
  cannot be stale; just relaunch to restart the process. If it is a marketplace or `@inline`
  install, update it.
- **Cowork has an old version after a release.** Frozen copies do not auto-update; click
  Update on the Plugins screen.
- **The same tool appears twice.** It is registered in two surfaces' registries (for example
  both `claude_desktop_config.json` and Cowork Plugins). That is expected when you want it in
  both regular chat and Cowork; each surface reads its own registry and they do not collide.
