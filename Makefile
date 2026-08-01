# Makefile -- release tooling for the personal MCP marketplace.
#
# The marketplace (.claude-plugin/marketplace.json) is GitHub-sourced from
# main with autoUpdate enabled, so whatever lands on main is what every
# installed plugin pulls on the next `/plugin update`. main is therefore the
# LIVE marketplace branch. To keep un-reviewed code off main, day-to-day work
# happens on `develop` and reaches main only through a CI-gated PR (see
# `release`). Run `make init-develop` once to create that branch.
#
# Per-package versions are the single source of truth (packages/<tool>/
# pyproject.toml). `bump` moves a version; gen_marketplace.py propagates it
# into marketplace.json and the per-plugin plugin.json. `--check` (run in CI
# and by `check`) fails on any drift.
#
# Common flows:
#   make bump TOOL=unifi-tools            # bump one tool's patch version
#   make check                            # local mirror of CI
#   make ship TOOL=unifi-tools            # bump + commit + push + release
#   make release                          # promote current develop to main

REPO        := mrlesmithjr/mcp
BUMP        ?= patch
# Use the SSH key file directly, bypassing any agent (parity with the
# context-manager release flow; this repo uses an SSH remote).
GIT_NOAGENT := GIT_SSH_COMMAND='ssh -o IdentityAgent=none'
# Resolve a package directory's real PyPI name from its pyproject.toml, so
# both tools (mrlesmithjr-mcp-<slug>) and mcp-common resolve correctly.
PKG_NAME     = uv run python -c "import tomllib,sys;print(tomllib.load(open('packages/$(1)/pyproject.toml','rb'))['project']['name'])"

.DEFAULT_GOAL := help
.PHONY: help gen check bump ship release init-develop hooks publish-common check-common-published

help:
	@echo "Release tooling for the personal MCP marketplace"
	@echo ""
	@echo "  make bump TOOL=<slug> [BUMP=patch|minor|major]"
	@echo "      Bump one package's version, relock, and regenerate marketplace files."
	@echo "  make check"
	@echo "      Local mirror of CI: gen --check, ruff lint + format, pytest."
	@echo "  make ship TOOL=<slug> [BUMP=patch|minor|major]"
	@echo "      bump -> commit -> push develop -> release (one-shot)."
	@echo "  make release [SKIP_CI=1]"
	@echo "      Promote develop to main via a CI-gated PR, then tag each plugin."
	@echo "      SKIP_CI=1 verifies locally (make check) instead of waiting on GitHub"
	@echo "      Actions -- use when the account's Actions minutes are exhausted."
	@echo "  make init-develop"
	@echo "      One-time: create and push the develop branch off main."
	@echo "  make hooks"
	@echo "      One-time per clone: activate the repo-local pre-push main guard."
	@echo "  make publish-common"
	@echo "      Build and publish mrlesmithjr-mcp-common to PyPI (needs UV_PUBLISH_TOKEN)."
	@echo ""
	@echo "  Tools: $(notdir $(wildcard packages/*))"

# Regenerate marketplace.json + per-plugin files, then assert no drift.
gen:
	@uv run python scripts/gen_marketplace.py
	@uv run python scripts/gen_marketplace.py --check

# Local mirror of .github/workflows/ci.yml so a release never surprises CI.
# Syncs and tests every package in one pass (this repo's dev machine is
# macOS, so the PyObjC packages resolve locally too -- CI only splits them
# onto a separate runner because ubuntu-latest can't). Each package's tests
# run as its own pytest invocation, not combined, because several packages
# share the literal filename tests/test_mcp_server.py and combined
# collection silently drops tests when that happens (#96).
check:
	@uv run python scripts/gen_marketplace.py --check
	@uv run ruff check packages/
	@uv run ruff format --check packages/
	@uv sync --extra dashboard \
		--package mrlesmithjr-mcp-common \
		--package mrlesmithjr-mcp-flightops \
		--package mrlesmithjr-mcp-homeops \
		--package mrlesmithjr-mcp-launchd-tools \
		--package mrlesmithjr-mcp-lawnops \
		--package mrlesmithjr-mcp-nextdns-tools \
		--package mrlesmithjr-mcp-obsidian-search-tools \
		--package mrlesmithjr-mcp-sheets-tools \
		--package mrlesmithjr-mcp-unifi-tools \
		--package mrlesmithjr-mcp-weather-tools \
		--package mrlesmithjr-mcp-ynab-tools \
		--package mrlesmithjr-mcp-apple-eventkit-tools \
		--package mrlesmithjr-mcp-contacts-tools \
		--package mrlesmithjr-mcp-imessage-tools \
		--package mrlesmithjr-mcp-mail-tools
	@for pkg in mcp-common flightops homeops launchd-tools lawnops \
	            nextdns-tools obsidian-search-tools sheets-tools unifi-tools \
	            weather-tools ynab-tools apple-eventkit-tools \
	            contacts-tools imessage-tools mail-tools; do \
		echo "[check] pytest packages/$$pkg"; \
		uv run pytest packages/$$pkg -q || exit 1; \
	done

# Bump a single package's version and propagate it everywhere it is recorded:
# pyproject.toml (uv version), uv.lock (uv lock), and the generated marketplace
# + plugin.json files (gen). Leaves the changes staged-but-uncommitted for review.
bump:
	@test -n "$(TOOL)" || { echo "Usage: make bump TOOL=<slug> [BUMP=patch|minor|major]"; exit 1; }
	@test -d "packages/$(TOOL)" || { echo "ERROR: packages/$(TOOL) does not exist"; exit 1; }
	@PKG=$$($(call PKG_NAME,$(TOOL))); \
	echo "[bump] $(TOOL) ($$PKG): $(BUMP)"; \
	uv version --package "$$PKG" --bump $(BUMP)
	@uv lock
	@uv run python scripts/gen_marketplace.py
	@uv run python scripts/gen_marketplace.py --check
	@echo "[bump] done. Review the diff, then commit (or use 'make ship')."

# One-time bootstrap: create develop off main and push it. Safe to re-run.
init-develop: hooks
	@if git show-ref --verify --quiet refs/heads/develop; then \
		echo "[init-develop] local develop already exists"; \
	else \
		git branch develop main && echo "[init-develop] created local develop"; \
	fi
	@$(GIT_NOAGENT) git push -u origin develop 2>&1 | tail -1 || true
	@echo "[init-develop] develop tracks origin/develop. Do day-to-day work here."

# Activate the repo-local git hooks (pre-push main guard + global delegation).
# core.hooksPath is local config and not cloned, so run this once per clone.
hooks:
	@chmod +x .githooks/pre-push .githooks/pre-commit
	@git config core.hooksPath .githooks
	@echo "[hooks] core.hooksPath -> .githooks (direct pushes to main are now blocked)"

# Guard: refuse to release when the local mcp-common is ahead of PyPI. Tool
# venvs install mcp-common from PyPI, so shipping tools that expect an
# unpublished mcp-common version would break fresh installs. Run
# `make publish-common` first in that case.
check-common-published:
	@LOCAL=$$(uv run python -c "import tomllib;print(tomllib.load(open('packages/mcp-common/pyproject.toml','rb'))['project']['version'])"); \
	PYPI=$$(curl -fsSL https://pypi.org/pypi/mrlesmithjr-mcp-common/json 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])" 2>/dev/null || echo "none"); \
	echo "[check-common] local=$$LOCAL pypi=$$PYPI"; \
	if [ "$$PYPI" = "none" ]; then \
		echo "ERROR: mrlesmithjr-mcp-common has never been published to PyPI."; \
		echo "       Run 'make publish-common' before releasing tools that depend on it."; \
		exit 1; \
	fi; \
	if [ "$$LOCAL" != "$$PYPI" ]; then \
		NEWER=$$(printf '%s\n%s\n' "$$PYPI" "$$LOCAL" | sort -V | tail -1); \
		if [ "$$NEWER" = "$$LOCAL" ]; then \
			echo "ERROR: local mcp-common $$LOCAL is ahead of PyPI $$PYPI."; \
			echo "       Run 'make publish-common' before releasing tools that depend on it."; \
			exit 1; \
		fi; \
	fi

# Build and publish mcp-common to PyPI. Requires UV_PUBLISH_TOKEN in the env;
# publishing is intentionally a manual, credentialed step.
publish-common:
	@test -n "$$UV_PUBLISH_TOKEN" || { echo "ERROR: set UV_PUBLISH_TOKEN (PyPI token) first"; exit 1; }
	@VER=$$(uv run python -c "import tomllib;print(tomllib.load(open('packages/mcp-common/pyproject.toml','rb'))['project']['version'])"); \
	echo "[publish-common] building + publishing mrlesmithjr-mcp-common $$VER"; \
	uv build packages/mcp-common; \
	uv publish dist/mrlesmithjr_mcp_common-$$VER-*.whl dist/mrlesmithjr_mcp_common-$$VER.tar.gz

# Promote the current develop to main through a CI-gated PR, then tag each
# plugin at its shipped version. Run from a clean, pushed develop.
#
#   1. Block if mcp-common is unpublished (check-common-published).
#   2. Open (or reuse) a develop -> main PR.
#   3. Poll until CI passes; fail fast on a red check (or, with SKIP_CI=1,
#      run `make check` locally instead of touching GitHub Actions at all --
#      for when the account's Actions minutes are exhausted).
#   4. Squash-merge, then tag every plugin as <slug>-v<version> from main.
#   5. Merge main back into develop so the squash commit does not diverge.
release: check-common-published
	@BRANCH=$$(git branch --show-current); \
	if [ "$$BRANCH" != "develop" ]; then echo "ERROR: run 'make release' from develop (on '$$BRANCH')"; exit 1; fi; \
	if [ -n "$$(git status --porcelain)" ]; then echo "ERROR: uncommitted changes -- commit and push first"; exit 1; fi; \
	$(GIT_NOAGENT) git fetch origin develop; \
	AHEAD=$$(git rev-list origin/develop..develop --count 2>/dev/null || echo 0); \
	if [ "$$AHEAD" -gt 0 ]; then echo "ERROR: local develop is ahead of origin -- 'git push' first"; exit 1; fi; \
	TITLE="Release: $$(date +%Y-%m-%d)"; \
	PR_OUTPUT=$$(gh pr create --repo $(REPO) --base main --head develop --title "$$TITLE" --body "Marketplace release." 2>&1); \
	if [ $$? -ne 0 ]; then \
		if echo "$$PR_OUTPUT" | grep -qi "already exists"; then \
			PR_NUM=$$(gh pr list --repo $(REPO) --base main --head develop --json number --jq '.[0].number'); \
			echo "[release] reusing existing PR #$$PR_NUM"; \
		else echo "ERROR: gh pr create failed:"; echo "$$PR_OUTPUT"; exit 1; fi; \
	else \
		PR_NUM=$$(echo "$$PR_OUTPUT" | grep -oE '[0-9]+$$'); \
		echo "[release] PR #$$PR_NUM created"; \
	fi; \
	if [ -n "$$SKIP_CI" ]; then \
		echo "[release] SKIP_CI=1 set -- skipping GitHub Actions, verifying locally instead"; \
		if ! $(MAKE) check; then \
			echo "ERROR: local verification (make check) failed -- not merging."; exit 1; fi; \
		echo "[release] local verification passed (GitHub CI was skipped). Squash-merging PR #$$PR_NUM..."; \
	else \
		echo "[release] waiting for CI on PR #$$PR_NUM..."; \
		sleep 5; \
		ELAPSED=0; MAX_WAIT=1800; \
		while gh pr checks "$$PR_NUM" --repo $(REPO) 2>&1 | grep -qE "pending|queued"; do \
			printf "."; sleep 10; ELAPSED=$$((ELAPSED + 10)); \
			if [ "$$ELAPSED" -ge "$$MAX_WAIT" ]; then \
				echo ""; echo "ERROR: CI still pending after $$((MAX_WAIT / 60)) minutes -- not waiting forever."; \
				echo "       Check run status: gh run list --repo $(REPO) --limit 5"; \
				exit 1; \
			fi; \
		done; \
		echo ""; \
		CHECKS_OUTPUT=$$(gh pr checks "$$PR_NUM" --repo $(REPO) 2>&1); \
		if [ -z "$$CHECKS_OUTPUT" ] || echo "$$CHECKS_OUTPUT" | grep -qiE "no checks reported"; then \
			echo "ERROR: no CI checks reported for PR #$$PR_NUM -- refusing to merge unverified."; \
			echo "       This usually means the workflow never started (e.g. Actions minutes"; \
			echo "       exhausted for the month). Either wait for the monthly reset, or verify"; \
			echo "       locally instead: make release SKIP_CI=1"; \
			echo "       Check: gh api /users/$$(gh api /user --jq .login)/settings/billing/usage"; \
			exit 1; \
		fi; \
		if echo "$$CHECKS_OUTPUT" | grep -qi "fail"; then \
			echo "ERROR: CI failed:"; echo "$$CHECKS_OUTPUT"; exit 1; fi; \
		echo "[release] CI passed. Squash-merging PR #$$PR_NUM..."; \
	fi; \
	if ! gh pr merge "$$PR_NUM" --repo $(REPO) --squash --admin; then \
		echo "ERROR: merge failed -- check conflicts: gh pr view $$PR_NUM"; exit 1; fi; \
	$(GIT_NOAGENT) git fetch origin main; \
	for T in $$(uv run python -c "import json;[print(p['name']+'-v'+p['version']) for p in json.load(open('.claude-plugin/marketplace.json'))['plugins']]"); do \
		if git tag "$$T" origin/main 2>/dev/null; then \
			$(GIT_NOAGENT) git push origin "$$T" 2>/dev/null && echo "[release] tagged $$T" || true; \
		fi; \
	done; \
	echo "[release] syncing main back into develop..."; \
	git merge origin/main --no-edit -X ours 2>&1 && $(GIT_NOAGENT) git push origin develop \
		|| echo "[WARN] could not auto-sync; run: git merge origin/main && git push"; \
	echo ""; \
	echo "================================================================"; \
	echo " Released. Next: /plugin update <tool>  (inside Claude Code)"; \
	echo "================================================================"

# One-shot: bump a tool, commit, push develop, and release.
ship:
	@test -n "$(TOOL)" || { echo "Usage: make ship TOOL=<slug> [BUMP=patch|minor|major]"; exit 1; }
	@BRANCH=$$(git branch --show-current); \
	if [ "$$BRANCH" != "develop" ]; then echo "ERROR: run 'make ship' from develop (on '$$BRANCH')"; exit 1; fi
	$(MAKE) bump TOOL=$(TOOL) BUMP=$(BUMP)
	@PKG=$$($(call PKG_NAME,$(TOOL))); \
	VER=$$(uv run python -c "import tomllib;print(tomllib.load(open('packages/$(TOOL)/pyproject.toml','rb'))['project']['version'])"); \
	git add -A && git commit -m "chore($(TOOL)): release v$$VER"; \
	$(GIT_NOAGENT) git push origin develop
	$(MAKE) release
