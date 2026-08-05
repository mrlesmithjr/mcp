# CLAUDE.md

Guidance for Claude Code when working in the `mcp` uv workspace.

**Status**: ACTIVE
**Last Updated**: 2026-07-20

---

## What this repo is

A uv workspace that consolidates all personal MCP servers into a single lockfile,
shared dev tooling, and a marketplace generator. Each tool lives under `packages/`
as a subtree-merged member.

---

## Structure

```
mcp/
├── pyproject.toml              # Virtual workspace root (no distribution)
├── uv.lock                     # Single root lockfile (authoritative)
├── .claude-plugin/
│   └── marketplace.json        # GENERATED -- do not hand-edit
├── .agents/plugins/
│   └── marketplace.json        # GENERATED Codex marketplace -- do not hand-edit
├── packages/
│   ├── mcp-common/             # Shared library (mrlesmithjr-mcp-common); PyPI-ready
│   ├── weather-tools/          # mrlesmithjr-mcp-weather-tools
│   │   ├── .claude-plugin/
│   │   │   └── plugin.json     # GENERATED: Claude plugin metadata (no hooks field)
│   │   ├── .codex-plugin/
│   │   │   └── plugin.json     # GENERATED: Codex plugin metadata
│   │   ├── .mcp.json           # GENERATED: Claude Code MCP definition (runs hooks/run_server.sh)
│   │   └── hooks/
│   │       ├── hooks.json      # GENERATED: SessionStart hook wiring
│   │       ├── install_deps.sh # GENERATED: SessionStart venv installer
│   │       └── run_server.sh   # GENERATED: cold-start launcher
│   ├── ynab-tools/             # mrlesmithjr-mcp-ynab-tools (same layout)
│   │                           # ships NO launchagents/: the dashboard is opt-in
│   │                           # via `ynab dashboard install` (see below)
│   ├── homeops/, lawnops/      # launchagents/: plist templates (__HOME__) + ops
│   │                           # scripts, shipped in the plugin and auto-loaded
│   ├── contacts-tools/         # mrlesmithjr-mcp-contacts-tools (Google People API; ContactsManager/PyObjC retained but unwired)
│   └── ...                     # 15 packages total; the 14 plugins share this layout
├── scripts/
│   ├── gen_marketplace.py      # Reads packages/*/pyproject.toml, emits per-package files
│   └── install_launchagents.sh # Manual/dev re-install of all LaunchAgents from the checkout
└── dev/
    └── register_dev.py         # Registers <tool>-dev servers pointing at .venv/bin/
```

### Plugin pattern

Each tool's `packages/<tool>/` directory IS the plugin source. When Claude installs a
plugin, it clones that directory into `~/.claude/plugins/cache/{id}/{ver}/` (available
as `${CLAUDE_PLUGIN_ROOT}`). The persistent venv lives in `${CLAUDE_PLUGIN_DATA}` and
survives plugin updates.

The SessionStart hook (`hooks/install_deps.sh`) runs on each session start. It:
1. Hashes `pyproject.toml` AND `install_deps.sh` itself, comparing against
   `${CLAUDE_PLUGIN_DATA}/deps.hash` (so a change to the install target or the
   installer logic forces a rebuild, not just a dependency change)
2. If the hash differs, builds a fresh venv (`uv venv --relocatable`) in a per-PID
   temp dir, installs the tool with `uv pip install ${CLAUDE_PLUGIN_ROOT}` (plus any
   runtime extra, e.g. ynab-tools gets `[dashboard]`) into it, then atomically swaps
   it into place via rename and writes the new hash. Building in a temp dir and
   renaming (not rebuilding in place) means a concurrent multi-plugin release never
   exposes a half-built venv to an importing server (issue #22); `--relocatable` lets
   the finished venv survive the rename. The superseded venv is kept until the next
   rebuild's leftover-clear, so a server still importing from it is not pulled out from
   under. The rebuild is serialized with a `mkdir` lock (`build.lock`) so the
   SessionStart hook and `run_server.sh` cannot rebuild the same plugin at once and
   collide on the swap; the second builder re-checks the hash under the lock and
   no-ops (issue #24).
   `run_server.sh` does the same staleness check before launching the server: if the
   venv binary is missing, or its `deps.hash` does not match the current spec, it runs
   `install_deps.sh` synchronously first, so it never execs a stale or torn venv left
   by a prior release (issue #24). On the warm path it is a hash compare and an exec.
3. Always (every session, cheap and self-healing) symlinks the tool's human-facing
   CLIs -- every console script not ending in `-mcp` -- into `~/.local/bin` so they
   are on PATH. This is what makes `lawnops`, `homeops`, `ynab` (incl. `configure`),
   etc. runnable in a terminal.
4. On a (re)build, installs any LaunchAgents shipped under the plugin's
   `launchagents/` dir: copies the ops scripts to `~/.local/share/<tool>/`, renders
   `__HOME__` in the plist templates into `~/Library/LaunchAgents/`, and always
   `launchctl unload`s (failure suppressed) then `launchctl load -w`s each --
   never skip-if-already-loaded, since launchd caches the job definition it was
   loaded with and a plist rewritten in place would otherwise keep running under
   the stale in-memory `ProgramArguments` (issue #151). A plugin that ships
   `launchagents/render.sh` gets a second, cheap gate alongside the dependency
   rebuild: `render.sh` post-processes the rendered plist (e.g. filling in a
   dynamic schedule block from user config), and re-runs whenever a hash of
   `~/.config/<tool>/env` changes -- not only on a dependency bump -- so a
   user-edited setting (e.g. obsidian-search-tools' reindex cadence) takes
   effect on the next session (issue #60). `render.sh` failure is non-fatal;
   the unload/load below already tolerates a plist it can't parse.

   Only ship a plist here for an *unattended scheduled job* (obsidian-search-tools
   is the reference example; homeops and lawnops LaunchAgents are maintainer-home-
   specific and are not shipped from the plugin, issue #37). A persistent
   user-facing service must not be plugin-installed: this step runs
   `launchctl load -w` on every rebuild, so it would silently resurrect a
   service the user had deliberately stopped. The ynab dashboard is the
   worked example -- it is opt-in via `ynab dashboard install`, which owns
   writing, loading, and removing its own plist. Two owners of one label
   means uninstall does not stick.

This self-install means a nuclear rebuild needs no monorepo clone: installing the
plugins from the marketplace restores the venvs, the CLIs on PATH, and the
LaunchAgents. `scripts/install_launchagents.sh` is a manual equivalent for the dev
checkout. Runtime extras and CLI/LaunchAgent ownership are configured in the
generator (`_RUNTIME_EXTRAS`, per-package `launchagents/`). A `_RUNTIME_EXTRAS`
entry only makes a feature available in the venv; it never starts anything.

The machine-wide reset/rebuild/nuclear runbook is kept in the maintainer's
private dotfiles repo (the provisioning authority), not in this repo.

`mrlesmithjr-mcp-common` is a PyPI dependency of every tool -- it resolves from
PyPI when the tool is installed standalone via the plugin runtime. It is published
separately (see below).

### Codex plugin pattern

Each tool also ships a `.codex-plugin/plugin.json`, generated alongside the
Claude files. It carries an inline MCP definition whose `cwd` is `.` and whose
launcher path is relative to the installed plugin root. This is intentionally
separate from Claude's `.mcp.json`: Codex does not expand
`${CLAUDE_PLUGIN_ROOT}` in a plugin MCP server's stdio arguments. Keeping the
Codex transport inline avoids that unresolved template while leaving Claude
Code's established manifest and launcher contract unchanged.

`codex plugin marketplace add` and
`codex plugin add <slug>@mrlesmithjr-mcp` install cleanly and pass Codex's own
plugin schema validator. Verified end to end on codex-cli 0.144.6: a live
`codex exec` session against `weather-tools` and `ynab-tools` recorded a real
`mcp_tool_call` for the installed server, no shell fallback (issue #133).

### Which install path to use

These consumption paths do not overlap. Picking the wrong one is the most
common source of confusion, because two of them can silently fight over
`~/.local/bin/<cli>`.

| Situation | Path | How |
|-----------|------|-----|
| **This repo is checked out** (dev machine) | Workspace venv | `uv sync --all-packages`, then `uv run python dev/register_dev.py` to register `<tool>-dev` servers pointing at `.venv/bin/` |
| **No checkout** (consumer machine) | Marketplace plugin | `/plugin marketplace add mrlesmithjr/mcp` + `/plugin install <slug>@mrlesmithjr-mcp`; the SessionStart hook self-bootstraps the venv, CLIs, and LaunchAgents |
| **Claude Desktop, regular chat** | Absolute-path config | Regular Desktop chat does not read Claude Code's MCP config. Point `claude_desktop_config.json` (Settings -> Developer -> Local MCP servers -> Edit Config) at an absolute path to the `<tool>-mcp` binary; on a dev box, the `.venv` one |
| **Claude Desktop, Cowork** | `@inline` plugin | Cowork (Desktop's agent mode) has its own plugin system (Settings -> Plugins), separate from regular chat. Installing there builds a frozen clone that needs a manual Update after each release |

Claude Code, regular Desktop chat, and Cowork each read a **different** MCP registry and are
set up independently, so a tool installed in one is not available in the others. Full
per-surface setup, the (non-guessable) `<tool>-mcp` binary-name table, and troubleshooting are
in [`docs/where-tools-run.md`](docs/where-tools-run.md).

Do **not** install the marketplace plugin on a dev machine. `dev/register_dev.py`
states the rule directly: marketplace plugins are consumer-only. A plugin install
clones the package and builds its own venv, so you end up running a frozen copy of
the code you are editing — and `install_deps.sh` unconditionally re-points
`~/.local/bin/<cli>` at that venv on every run, silently taking the CLI away from the
workspace build.

Running `hooks/install_deps.sh` by hand from a checkout has the same effect: with no
plugin runtime supplying `CLAUDE_PLUGIN_ROOT`/`CLAUDE_PLUGIN_DATA`, it falls back to
`packages/<tool>` and `~/.local/share/<tool>`, producing a plugin-shaped venv with no
plugin behind it. It also installs **unlocked** (`uv pip install ${PLUGIN_ROOT}`), so
it can resolve dependency versions the workspace lockfile would never pick.

For Claude Desktop specifics — absolute path requirement, credential handling,
verifying with a raw MCP handshake, troubleshooting — see
`packages/ynab-tools/docs/mcp-server.md`, which is the worked reference for any tool
in this workspace.

---

## Common operations

### After editing any package's pyproject.toml

```bash
uv lock
uv sync --all-packages          # add --extra dashboard if ynab-tools tests are needed
uv run python scripts/gen_marketplace.py
uv run python scripts/gen_marketplace.py --check
```

### Run tests (per-package only -- combined invocation causes name collisions)

```bash
uv run pytest packages/mcp-common
uv run pytest packages/weather-tools
uv run pytest packages/contacts-tools
uv run pytest packages/ynab-tools   # requires --extra dashboard at sync time
```

### Register dev servers

```bash
uv run python dev/register_dev.py --list    # preview
uv run python dev/register_dev.py           # register all <tool>-dev entries
```

### Releasing (marketplace updates)

`main` is the live marketplace branch: the marketplace is GitHub-sourced from
`main` with `autoUpdate` on, so anything on `main` is what installed plugins
pull on the next `/plugin update`. To keep un-reviewed code off `main`,
day-to-day work happens on `develop` and reaches `main` only through a
CI-gated PR. The `Makefile` drives this:

```bash
make init-develop                       # one-time: create + push develop (also runs `make hooks`)
make hooks                               # one-time per clone: activate the pre-push main guard
make bump TOOL=unifi-tools              # bump one tool (pyproject + lock + generated files move together)
make check                              # local mirror of CI
make ship TOOL=unifi-tools              # bump -> commit -> push develop -> release
make release                            # promote current develop to main via CI-gated PR, then tag each plugin
```

`main` cannot use server-side branch protection (private repo on the free
plan), so `make hooks` installs a repo-local `pre-push` guard (`.githooks/`,
via `core.hooksPath`) that blocks direct pushes to `main` and delegates to the
global privacy hook. It is machine-local and bypassable with `--no-verify`;
true server-side rulesets get applied when the repo goes public (issue #2).
`core.hooksPath` is local config and is not cloned, so re-run `make hooks`
after a fresh clone.

Per-package versions are the single source of truth; `gen_marketplace.py`
propagates a bump into `marketplace.json` and the per-plugin `plugin.json`.
`make release` tags each plugin `<slug>-v<version>` from `main`.

`mcp-common` is depended on with a `>=` floor and installs from PyPI in tool
venvs. When `mcp-common`'s API changes, bump it, then `make publish-common`
(needs `UV_PUBLISH_TOKEN`) and raise the floor in dependents BEFORE shipping
those tools. `make release` runs `check-common-published` and refuses to
release when the local `mcp-common` version is ahead of PyPI.

### Migrate a new tool (Phase 2)

See `docs/migration.md` for the full runbook. Summary:

1. `git subtree add --prefix=packages/<tool> <local-clone-path> main`
2. Edit `packages/<tool>/pyproject.toml`: rename, bump python floor, add common dep, fix em-dashes
3. Delete the tool's own `uv.lock` if present
4. `uv lock && uv sync --all-packages`
5. `uv run python scripts/gen_marketplace.py`
6. `uv run pytest packages/<tool>`
7. Smoke-test: `uv run python -c "from <pkg>.mcp_server import mcp; ..."`
8. Commit referencing the migration issue (e.g. `refs #<issue>`)

---

## Package naming convention

| Role | PyPI name | Import package | Console script |
|------|-----------|----------------|----------------|
| Shared lib | `mrlesmithjr-mcp-common` | `mcp_common` | (none) |
| Tool | `mrlesmithjr-mcp-<slug>` | `<pkg>_tools` or `<pkg>` | `<slug>-mcp` |

The tool slug (e.g. `unifi-tools`) is the server name in `.mcp.json`, which is
shared unmodified by both Claude and Codex. Because the server ships as a plugin (plugin name == server name == slug), the tool namespace in
Claude is `mcp__plugin_<slug>_<slug>__<tool>` (not `mcp__<slug>__`). Downstream
allowlists, workflows, agents, and skills reference that plugin form.

---

## MCP tool annotations convention

Every tool declares MCP tool annotations (`@mcp.tool(annotations=ToolAnnotations(...))`)
so clients can gate confirmation correctly. Annotations are hints, not guarantees:
clients treat them as untrusted unless the server is trusted, so they never replace
server-side validation or human-in-the-loop on destructive ops.

Mind the spec defaults: `readOnlyHint` and `idempotentHint` default to **false**, but
`destructiveHint` and `openWorldHint` default to **true**. So a tool that is closed-world
or non-destructive must set those `False` explicitly; an unset field is not a safe
default. Classify by effect:

| Tool effect | Annotation |
|-------------|------------|
| Reads/queries state only (`*_list`, `*_status`, `*_summary`, `*_history`, reports) | `readOnlyHint=True` |
| Modifies, deletes, or restarts in a way that is not easily undone | `readOnlyHint=False, destructiveHint=True`; add `idempotentHint=True` only if re-running with the same args is safe |
| Writes state but reversibly / non-destructively | `readOnlyHint=False, destructiveHint=False` (the `False` is required; the default is true) |
| Reaches external/unpredictable systems (web, third-party APIs) | `openWorldHint=True` |
| Touches only local/closed state (no external calls) | `openWorldHint=False` (the `False` is required; the default is true) |

`destructiveHint`/`idempotentHint` are only meaningful when `readOnlyHint=False`, so
read tools set `readOnlyHint=True` plus `openWorldHint` as appropriate. `launchd-tools`
is the reference implementation (issue #20): its tools are all closed-world, so the
shared `_READ_ONLY` constant carries `readOnlyHint=True, openWorldHint=False`. Each
package's test suite asserts the registered tools' annotations via
`mcp._tool_manager.list_tools()`.

---

## PyObjC tools

contacts-tools, apple-eventkit-tools, mail-tools, and imessage-tools depend on
PyObjC. These resolve and install fine on macOS without platform markers. A
Linux CI runner cannot resolve them, so they run in a dedicated `test-macos`
job (`.github/workflows/ci.yml`); every other package runs in the Linux job.
homeops and lawnops dropped their apple-eventkit-tools dependency (issue #39)
and moved to the Linux job; `homeops-coordinator`, the only other transitive
dependent, was retired outright (issue #40). Keep the macOS job's package
list minimal -- macOS runner minutes are billed at a 10x multiplier versus
Linux.

---

## What NOT to edit by hand

- `.claude-plugin/marketplace.json` -- generated by `gen_marketplace.py`
- `.agents/plugins/marketplace.json` -- generated by `gen_marketplace.py`
- `packages/<tool>/.mcp.json` -- generated by `gen_marketplace.py`, used by Claude Code
- `packages/<tool>/.claude-plugin/plugin.json` -- generated by `gen_marketplace.py`
- `packages/<tool>/.codex-plugin/plugin.json` -- generated by `gen_marketplace.py`
- `packages/<tool>/hooks/hooks.json` -- generated by `gen_marketplace.py`
- `packages/<tool>/hooks/install_deps.sh` -- generated by `gen_marketplace.py`
- `packages/<tool>/hooks/run_server.sh` -- generated by `gen_marketplace.py`
- `README.md` plugin-table **Version** column -- generated by `gen_marketplace.py`
  (the name and description cells, and the `(library)` row, are hand-curated
  and left untouched)

Re-run `gen_marketplace.py` after any pyproject.toml change that affects name,
version, description, or scripts.

## PyPI-published workspace packages

`packages/mcp-common` is depended on by every other plugin's `pyproject.toml`,
so it needs its own PyPI presence -- a tool installed standalone via the
plugin runtime (`uv pip install ${CLAUDE_PLUGIN_ROOT}`) resolves its
dependencies from PyPI, entirely outside the uv workspace.

To publish it:

```bash
make publish-common                    # needs UV_PUBLISH_TOKEN
```

(equivalent to `uv build packages/mcp-common` + `uv publish dist/mcp-common-*.whl
dist/mcp-common-*.tar.gz` by hand). Until published, the workspace `uv.lock`
resolves it from the workspace via `[tool.uv.sources]`. `make release`
refuses to promote `develop` to `main` while the local version is ahead of
what's live on PyPI (`check-common-published`), so a version bump always
needs a matching `make publish-common` run before the next release -- this
is the only step in the release flow that requires a manual, credentialed
action.

`packages/apple-eventkit-tools` was published the same way while `homeops`,
`lawnops`, and `homeops-coordinator` depended on it (issue #146). Issues #39
and #40 dropped all three dependents (homeops/lawnops for read-only reports
with no Reminders delivery channel; homeops-coordinator retired outright),
so `apple-eventkit-tools` has zero dependents now and its `make
publish-apple-eventkit-tools` / `check-apple-eventkit-tools-published`
release gate was removed. It remains a standalone plugin, just no longer a
published dependency of anything else in the workspace.
