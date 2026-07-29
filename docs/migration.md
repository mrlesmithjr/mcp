# Phase 2: Per-Tool Migration Runbook

This document describes how to migrate an existing personal MCP tool from its standalone
repository into the mcp workspace as a subtree member.

## Prerequisites

- The tool's standalone repo is on Forgejo (or GitHub for the few exceptions).
- You have run `uv sync --all-packages` at the workspace root at least once.
- The tool passes its own tests before migration.

## Per-Tool Subtree Migration (one session per tool)

### 1. Add the subtree

```bash
# From the workspace root
# Use the LOCAL clone path to preserve commit history and avoid Forgejo CF Access.
git subtree add \
  --prefix=packages/<tool-dir-name> \
  <local-clone-path>/<tool-dir-name> \
  main
```

`<tool-dir-name>` is the directory name the tool will live under in `packages/`.
Convention: match the existing tool directory name (e.g. `lawnops`, `weather-tools`).

Do NOT use `--squash`. Without it the tool's full commit history merges into the
workspace graph and remains reachable via `git log -- packages/<tool>`.

Example:
```bash
git subtree add \
  --prefix=packages/weather-tools \
  <local-clone-path>/weather-tools \
  main
```

Each tool's source is wherever its standalone clone lives; use that actual local
path as the subtree source.

Verify history landed:
```bash
git log --oneline -20 | grep -A1 "Add 'packages/<tool>'"
# The subtree merge commit's parent chain includes the tool's own commits.
```

### 2. Edit the tool's pyproject.toml (3 required changes)

Open `packages/<tool-dir-name>/pyproject.toml` and make these edits:

**a. Rename the project name to the prefixed form:**
```toml
# Before
name = "weather-tools"

# After
name = "mrlesmithjr-mcp-weather-tools"
```

**b. Add mrlesmithjr-mcp-common as a dependency:**
```toml
dependencies = [
    "mrlesmithjr-mcp-common",
    # ... existing deps ...
]
```

**c. Delete the tool's own uv.lock** (the workspace root lockfile takes over):
```bash
rm packages/<tool-dir-name>/uv.lock
```

If the tool never had a standalone `uv.lock` (e.g. contacts-tools), skip this step.

**d. Fix prohibited punctuation in the description field.** The project bans em-dashes
(U+2014) everywhere. If the description contains one, replace it with `:` or a comma:
```toml
# bad:  description = "Apple Contacts MCP server [emdash] read and write contacts"
# good: description = "Apple Contacts MCP server: read and write contacts"
```

### 3. Lock and sync

```bash
uv lock
uv sync --all-packages
```

Run lock+sync BEFORE the generator. Both must succeed before proceeding.

Resolve any dependency conflicts reported by `uv lock` one tool at a time.
The common source of conflict is `requires-python` floor mismatches (all tools
must be >=3.11 after migration). Update the tool's pyproject.toml if needed.

**ynab-tools note:** its dashboard tests import `fastapi`. Install the extra so tests pass:
```bash
uv sync --all-packages --extra dashboard
```
This only affects the local dev venv; the lockfile already contains fastapi regardless.

### 4. Re-run the generator

```bash
uv run python scripts/gen_marketplace.py
uv run python scripts/gen_marketplace.py --check
```

This emits the following files directly inside the package directory for the newly added tool:

- `packages/<tool-slug>/.claude-plugin/plugin.json` -- plugin metadata (no hooks field; hooks live in hooks.json)
- `packages/<tool-slug>/.mcp.json` -- MCP server definition (runs hooks/run_server.sh)
- `packages/<tool-slug>/hooks/hooks.json` -- SessionStart hook wiring
- `packages/<tool-slug>/hooks/install_deps.sh` -- SessionStart venv installer
- `packages/<tool-slug>/hooks/run_server.sh` -- cold-start launcher

It also updates `.claude-plugin/marketplace.json` with `source: ./packages/<tool-slug>`.
Do NOT hand-edit any of these files; they are fully generated.

### 5. Run tests

Run each package individually to avoid pytest module-name collisions (multiple
packages share `tests/test_mcp_server.py` with no `__init__.py`, confusing
pytest when it collects across packages in a single invocation):

```bash
uv run pytest packages/<tool-dir-name>
uv run pytest packages/mcp-common
```

Both must be green before committing. Do NOT use `uv run pytest packages/` in a
single call -- it will hit import-file-mismatch errors when two packages have
identically named test files.

### 6. Commit

```bash
git add packages/<tool-dir-name> .claude-plugin/ uv.lock
git commit -m "feat(packages): migrate <tool-name> into workspace refs #<issue>"
```

### 7. Register dev-mode server

```bash
uv run python dev/register_dev.py
```

This registers `<tool>-dev` in Claude Code pointing at the workspace `.venv/bin/<script>`.
Confirm with `claude mcp list`.

## Migrating mcp-common usage into a tool (optional, Phase 3+)

After the subtree is in place, you can optionally refactor the tool's
`mcp_server.py` to use `mcp_common` utilities:

```python
# Before (typical standalone pattern)
import logging, sys
logging.basicConfig(stream=sys.stderr, ...)
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("tool-name", instructions="...")
def main():
    mcp.run(transport="stdio")

# After (using mcp_common)
from mcp_common.logging import configure_logging
from mcp_common.server import build_server, run_stdio
configure_logging()
mcp = build_server("tool-name", instructions="...")
def main():
    run_stdio(mcp)
```

This refactor is NOT required during Phase 2. The tool can operate as a plain
workspace member without touching `mcp_common` at all.

## Plugin pattern (SessionStart venv)

The marketplace uses the officially documented Claude Code plugin pattern. Each tool's
`packages/<tool>/` IS the plugin source; `claude plugin install` clones it into
`~/.claude/plugins/cache/{id}/{ver}/` and exposes it as `${CLAUDE_PLUGIN_ROOT}`.

The persistent venv lives in `${CLAUDE_PLUGIN_DATA}` (survives updates):

```
~/.claude/plugins/data/<tool>/
  venv/           # installed venv (created by install_deps.sh)
  deps.hash       # SHA-256 of pyproject.toml at last install
```

The SessionStart hook (`hooks/install_deps.sh`) runs on every session start. The guard
file makes it a no-op after the first install unless `pyproject.toml` changes.

### mcp-common and PyPI

`mrlesmithjr-mcp-common` is a direct dependency of all tools. Within the workspace
`uv.lock` resolves it via `[tool.uv.sources]` (local). When a tool is installed via
the plugin runtime (outside the workspace), `uv pip install ${CLAUDE_PLUGIN_ROOT}`
must resolve `mrlesmithjr-mcp-common` from PyPI.

Before live plugin installs will work, you must publish mcp-common:

```bash
uv build packages/mcp-common
uv publish dist/mrlesmithjr_mcp_common-*.whl dist/mrlesmithjr_mcp_common-*.tar.gz
```

This requires your PyPI token and is a one-time manual step. After publishing,
the hook installs it automatically for every tool.

## Risk notes

**R3 (lockfile conflicts):** The single root `uv.lock` must satisfy all tools simultaneously.
Migrate tools one at a time and run `uv lock` after each to catch conflicts early.

**R4 (macOS-only tools):** Tools using PyObjC (contacts-tools, apple-eventkit-tools,
mail-tools, imessage-tools) cannot resolve on a Linux CI runner. On macOS, uv resolves
and installs pyobjc-framework-* packages cleanly into the workspace venv without any
special markers -- the single-root lockfile works fine for local development. For CI,
use a macOS runner or add platform markers to the PyObjC dependencies. The CI skeleton
will need a `macos-latest` runner job for PyObjC tools; this is deferred to the CI
hardening phase after the full sweep.

## Tool migration checklist

Use this as the commit body for each tool migration issue:

- [ ] `git subtree add` from LOCAL clone (no --squash), history verified in `git log`
- [ ] `[project] name` prefixed to `mrlesmithjr-mcp-<tool>`
- [ ] `requires-python` bumped to `>=3.11` if it was `>=3.10`
- [ ] `mrlesmithjr-mcp-common` added to `[project] dependencies`
- [ ] Em-dash in description field fixed (replace with `:` or comma)
- [ ] Tool's `uv.lock` deleted (skip if it never existed)
- [ ] `uv lock` passes (no conflicts)
- [ ] `uv sync --all-packages` succeeds (add `--extra dashboard` if migrating ynab-tools)
- [ ] Generator run: `python scripts/gen_marketplace.py`
- [ ] Generator `--check` passes
- [ ] `packages/<tool>/.mcp.json` server key equals the tool slug
- [ ] `packages/<tool>/hooks/install_deps.sh` present and references correct slug
- [ ] Tests pass: `uv run pytest packages/<tool-dir-name>` (run per-package, not combined)
- [ ] MCP server smoke-test: `uv run python -c "from <pkg>.mcp_server import mcp; ..."`
- [ ] `dev/register_dev.py --list` shows `<tool>-dev` with status OK
- [ ] Committed with `refs #<issue>`
