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
│   │   └── launchagents/       # plist templates (__HOME__) + ops scripts, shipped in the plugin
│   ├── contacts-tools/         # mrlesmithjr-mcp-contacts-tools (Google People API; ContactsManager/PyObjC retained but unwired)
│   ├── homeops-coordinator/    # daemon package; workspace member, NOT a plugin (no -mcp script)
│   └── ...                     # 16 packages total; the 14 plugins share this layout
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
4. On a (re)build only, installs any LaunchAgents shipped under the plugin's
   `launchagents/` dir: copies the ops scripts to `~/.local/share/<tool>/`, renders
   `__HOME__` in the plist templates into `~/Library/LaunchAgents/`, and always
   `launchctl unload`s (failure suppressed) then `launchctl load -w`s each --
   never skip-if-already-loaded, since launchd caches the job definition it was
   loaded with and a plist rewritten in place would otherwise keep running under
   the stale in-memory `ProgramArguments` (issue #151).

This self-install means a nuclear rebuild needs no monorepo clone: installing the
plugins from the marketplace restores the venvs, the CLIs on PATH, and the
LaunchAgents. `scripts/install_launchagents.sh` is a manual equivalent for the dev
checkout. Runtime extras and CLI/LaunchAgent ownership are configured in the
generator (`_RUNTIME_EXTRAS`, per-package `launchagents/`).

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
job (`.github/workflows/ci.yml`) alongside `homeops-coordinator` (which
transitively depends on apple-eventkit-tools); every other package runs in
the Linux job. Keep the macOS job's package list minimal -- macOS runner
minutes are billed at a 10x multiplier versus Linux.

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
  (the name and description cells, and the `(library)`/`(daemon)` rows, are
  hand-curated and left untouched)

Re-run `gen_marketplace.py` after any pyproject.toml change that affects name,
version, description, or scripts.

## PyPI-published workspace packages

Two packages are depended on by other plugins' `pyproject.toml` files, so each
needs its own PyPI presence -- a tool installed standalone via the plugin
runtime (`uv pip install ${CLAUDE_PLUGIN_ROOT}`) resolves its dependencies
from PyPI, entirely outside the uv workspace:

- `packages/mcp-common` -- depended on by every tool.
- `packages/apple-eventkit-tools` -- depended on by `homeops` and `lawnops`
  (issue #146, for direct Reminders creation without a headless LLM call).

To publish either:

```bash
make publish-common                    # needs UV_PUBLISH_TOKEN
make publish-apple-eventkit-tools      # needs UV_PUBLISH_TOKEN
```

(equivalent to `uv build packages/<pkg>` + `uv publish dist/<pkg>-*.whl dist/<pkg>-*.tar.gz`
by hand). Until published, the workspace `uv.lock` resolves both from the
workspace via `[tool.uv.sources]`. `make release` refuses to promote `develop`
to `main` while either package's local version is ahead of what's live on
PyPI (`check-common-published` / `check-apple-eventkit-tools-published`), so
a version bump to either always needs its matching `make publish-*` run
before the next release -- this is the only step in the release flow that
requires a manual, credentialed action.
