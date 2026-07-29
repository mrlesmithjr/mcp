"""Tests for launchd-tools MCP server and pure helper modules.

Each acceptance criterion from the spec maps to one or more test functions.
All tests use captured fixtures and pure functions -- no live launchctl calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


# ---------------------------------------------------------------------------
# Fixtures: parsed text blocks
# ---------------------------------------------------------------------------


@pytest.fixture()
def context_manager_print() -> str:
    return _read("print_context_manager.txt")


@pytest.fixture()
def forgejo_backup_print() -> str:
    return _read("print_forgejo_backup.txt")


@pytest.fixture()
def utility_anomaly_print() -> str:
    return _read("print_utility_anomaly.txt")


@pytest.fixture()
def ynab_dashboard_print() -> str:
    return _read("print_ynab_dashboard.txt")


@pytest.fixture()
def launchctl_list_output() -> str:
    return _read("launchctl_list.txt")


# ---------------------------------------------------------------------------
# AC1: prefix filtering drops Apple/Homebrew labels
# ---------------------------------------------------------------------------


class TestPrefixFiltering:
    def test_personal_labels_included(self, launchctl_list_output):
        from launchd_tools.launchctl import _parse_list

        prefixes = [
            "com.homeops",
            "com.lawnops",
            "com.ynab-tools",
            "com.mrlesmithjr",
            "com.methodicalcloud",
            "com.larrysmithjr",
        ]
        agents = _parse_list(launchctl_list_output, prefixes)
        labels = [a["label"] for a in agents]
        assert any(lbl.startswith("com.homeops") for lbl in labels)
        assert any(lbl.startswith("com.mrlesmithjr") for lbl in labels)

    def test_apple_labels_dropped(self, launchctl_list_output):
        from launchd_tools.launchctl import _parse_list

        prefixes = [
            "com.homeops",
            "com.lawnops",
            "com.ynab-tools",
            "com.mrlesmithjr",
            "com.methodicalcloud",
            "com.larrysmithjr",
        ]
        agents = _parse_list(launchctl_list_output, prefixes)
        labels = [a["label"] for a in agents]
        apple_labels = [lbl for lbl in labels if lbl.startswith("com.apple")]
        assert apple_labels == [], f"Apple labels leaked through: {apple_labels}"

    def test_homebrew_labels_dropped(self, launchctl_list_output):
        from launchd_tools.launchctl import _parse_list

        prefixes = ["com.homeops", "com.lawnops"]
        agents = _parse_list(launchctl_list_output, prefixes)
        labels = [a["label"] for a in agents]
        # Must not contain any mrlesmithjr or methodicalcloud labels
        assert not any(lbl.startswith("com.mrlesmithjr") for lbl in labels)

    def test_pid_parsed_correctly(self, launchctl_list_output):
        from launchd_tools.launchctl import _parse_list

        prefixes = ["com.mrlesmithjr"]
        agents = _parse_list(launchctl_list_output, prefixes)
        running = [a for a in agents if a["pid"] is not None]
        # context-manager should be running
        assert any(a["label"] == "com.mrlesmithjr.context-manager" for a in running)

    def test_idle_pid_is_none(self, launchctl_list_output):
        from launchd_tools.launchctl import _parse_list

        prefixes = ["com.homeops"]
        agents = _parse_list(launchctl_list_output, prefixes)
        idle = [a for a in agents if a["pid"] is None]
        # utility-anomaly and others should be idle
        assert len(idle) > 0


# ---------------------------------------------------------------------------
# AC2: daemon-running healthy; scheduled idle exit-0 healthy
# ---------------------------------------------------------------------------


class TestHealthRules:
    def test_daemon_running_is_healthy(self, context_manager_print):
        from launchd_tools.health import classify_kind, is_healthy
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(context_manager_print)
        # context-manager is a keepalive daemon
        kind = classify_kind(parsed)
        assert kind == "daemon"
        assert parsed["state"] == "running"
        assert isinstance(parsed["pid"], int)
        assert is_healthy(parsed, kind) is True

    def test_scheduled_idle_exit0_is_healthy(self, forgejo_backup_print):
        from launchd_tools.health import classify_kind, is_healthy
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(forgejo_backup_print)
        kind = classify_kind(parsed)
        assert kind == "scheduled"
        assert parsed.get("last_exit_code") == 0
        assert is_healthy(parsed, kind) is True

    def test_daemon_not_running_is_unhealthy(self):
        from launchd_tools.health import is_healthy

        parsed = {"state": "not running", "last_exit_code": 0}
        assert is_healthy(parsed, "daemon") is False

    def test_scheduled_nonzero_exit_is_unhealthy(self):
        from launchd_tools.health import is_healthy

        parsed = {"state": "not running", "last_exit_code": 1}
        assert is_healthy(parsed, "scheduled") is False

    def test_ynab_dashboard_classifies_as_daemon(self, ynab_dashboard_print):
        """com.ynab-tools.dashboard is a KeepAlive daemon -- must be daemon kind."""
        from launchd_tools.health import classify_kind
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(ynab_dashboard_print)
        kind = classify_kind(parsed)
        assert kind == "daemon", f"Expected 'daemon', got '{kind}'"

    def test_ynab_dashboard_healthy_when_running(self, ynab_dashboard_print):
        """ynab dashboard judged by running+pid, not exit code."""
        from launchd_tools.health import classify_kind, is_healthy
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(ynab_dashboard_print)
        kind = classify_kind(parsed)
        # Running and has a pid
        assert parsed["state"] == "running"
        assert isinstance(parsed.get("pid"), int)
        assert is_healthy(parsed, kind) is True

    def test_never_run_scheduled_is_healthy(self, utility_anomaly_print):
        """AC2 at the is_healthy level: never-run scheduled job is healthy."""
        from launchd_tools.health import classify_kind, is_healthy
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(utility_anomaly_print)
        kind = classify_kind(parsed)
        assert kind == "scheduled"
        assert parsed.get("last_exit_code") is None
        assert parsed.get("runs") == 0
        assert is_healthy(parsed, kind) is True


# ---------------------------------------------------------------------------
# AC3: agent_health flips to failed on non-zero exit; rollup counts correct
# ---------------------------------------------------------------------------


class TestHealthRollup:
    def test_all_healthy_message(self):
        from launchd_tools.health import build_health_summary

        agents = [
            {"label": "com.homeops.foo", "healthy": True, "kind": "scheduled", "last_exit_code": 0},
            {"label": "com.homeops.bar", "healthy": True, "kind": "daemon", "last_exit_code": None},
        ]
        summary = build_health_summary(agents)
        assert summary == "All 2 agents healthy."

    def test_failed_scheduled_shows_exit_code(self):
        from launchd_tools.health import build_health_summary

        agents = [
            {"label": "com.homeops.foo", "healthy": True, "kind": "scheduled", "last_exit_code": 0},
            {"label": "com.homeops.bad", "healthy": False, "kind": "scheduled", "last_exit_code": 1},
            {"label": "com.lawnops.broken", "healthy": False, "kind": "scheduled", "last_exit_code": 2},
        ]
        summary = build_health_summary(agents)
        assert summary.startswith("2 failed:")
        assert "com.homeops.bad (exit 1)" in summary
        assert "com.lawnops.broken (exit 2)" in summary

    def test_failed_daemon_shows_stopped(self):
        """Stopped daemon renders '(stopped)', not '(exit unknown)'."""
        from launchd_tools.health import build_health_summary

        agents = [
            {"label": "com.mrlesmithjr.some-daemon", "healthy": False, "kind": "daemon", "last_exit_code": None},
        ]
        summary = build_health_summary(agents)
        assert "1 failed:" in summary
        assert "com.mrlesmithjr.some-daemon (stopped)" in summary
        assert "unknown" not in summary

    def test_never_exited_counts_as_healthy_in_rollup(self):
        from launchd_tools.health import build_health_summary

        agents = [{"label": "com.homeops.new", "healthy": True, "kind": "scheduled", "last_exit_code": None}]
        summary = build_health_summary(agents)
        assert "healthy" in summary

    def test_mcp_agent_health_returns_json(self):
        """MCP tool agent_health returns valid JSON with expected keys."""
        import launchd_tools.mcp_server as mcp_mod
        from launchd_tools.mcp_server import agent_health

        with (
            patch.object(mcp_mod, "list_agents") as mock_list,
            patch.object(mcp_mod, "print_agent") as mock_print,
            patch.object(mcp_mod, "parse_print") as mock_parse,
            patch.object(mcp_mod, "infer_last_run") as mock_last_run,
        ):
            mock_list.return_value = [
                {"label": "com.homeops.task-escalation", "pid": None, "last_exit_code": 0},
            ]
            mock_print.return_value = ""
            mock_parse.return_value = {
                "state": "not running",
                "last_exit_code": 0,
                "has_event_triggers": True,
            }
            mock_last_run.return_value = None

            result = agent_health()
            data = json.loads(result)
            assert "summary" in data
            assert "total" in data
            assert "healthy_count" in data
            assert "failed_count" in data
            assert "unhealthy" in data

    def test_mcp_agent_health_detects_failure(self):
        import launchd_tools.mcp_server as mcp_mod
        from launchd_tools.mcp_server import agent_health

        with (
            patch.object(mcp_mod, "list_agents") as mock_list,
            patch.object(mcp_mod, "print_agent") as mock_print,
            patch.object(mcp_mod, "parse_print") as mock_parse,
            patch.object(mcp_mod, "infer_last_run") as mock_last_run,
        ):
            mock_list.return_value = [
                {"label": "com.homeops.broken", "pid": None, "last_exit_code": 1},
            ]
            mock_print.return_value = ""
            mock_parse.return_value = {
                "state": "not running",
                "last_exit_code": 1,
                "has_event_triggers": True,
            }
            mock_last_run.return_value = None

            result = agent_health()
            data = json.loads(result)
            assert data["failed_count"] == 1
            assert len(data["unhealthy"]) == 1
            assert data["unhealthy"][0]["label"] == "com.homeops.broken"


# ---------------------------------------------------------------------------
# AC4: agent_logs tails the stdout_path from print fixture
# ---------------------------------------------------------------------------


class TestAgentLogs:
    def test_logs_uses_stdout_path_from_print(self):
        """agent_logs calls tail_file with the stdout_path from parse_print."""
        import launchd_tools.mcp_server as mcp_mod
        from launchd_tools.mcp_server import agent_logs

        with (
            patch.object(mcp_mod, "print_agent") as mock_print,
            patch.object(mcp_mod, "parse_print") as mock_parse,
            patch.object(mcp_mod, "tail_file") as mock_tail,
        ):
            mock_print.return_value = ""
            mock_parse.return_value = {
                "stdout_path": "/Users/testuser/.claude-context/server.log",
                "stderr_path": "/Users/testuser/.claude-context/server-error.log",
            }
            mock_tail.return_value = ["line1", "line2"]

            result = agent_logs("com.mrlesmithjr.context-manager", lines=10)
            data = json.loads(result)

            assert data["label"] == "com.mrlesmithjr.context-manager"
            assert data["log_lines"] == ["line1", "line2"]
            # tail_file was called with the stdout_path
            mock_tail.assert_called_once_with("/Users/testuser/.claude-context/server.log", 10)

    def test_logs_rejects_non_allowlisted_label(self):
        from launchd_tools.mcp_server import agent_logs

        result = agent_logs("com.apple.something", lines=10)
        data = json.loads(result)
        assert "error" in data
        assert "allowlist" in data["error"]

    def test_logs_rejects_shell_metachar_label(self):
        """agent_logs applies the same charset guard as agent_kickstart."""
        from launchd_tools.mcp_server import agent_logs

        with patch("subprocess.run") as mock_run:
            result = agent_logs("com.homeops.foo; rm -rf /", lines=10)
            data = json.loads(result)
            assert "error" in data
            assert "invalid characters" in data["error"].lower()
            mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# AC5: agent_kickstart refuses non-allowlisted and shell-metachar labels
# ---------------------------------------------------------------------------


class TestKickstartSecurity:
    def test_kickstart_refuses_non_allowlisted(self):
        import launchd_tools.mcp_server as mcp_mod
        from launchd_tools.mcp_server import agent_kickstart

        with patch.object(mcp_mod, "subprocess") as mock_sub:
            result = agent_kickstart("com.apple.forbidden")
            data = json.loads(result)
            assert "error" in data
            assert "allowlist" in data["error"].lower() or "not in" in data["error"].lower()
            mock_sub.run.assert_not_called()

    def test_kickstart_refuses_shell_metachar_label(self):
        from launchd_tools.mcp_server import agent_kickstart

        with patch("subprocess.run") as mock_run:
            result = agent_kickstart("com.homeops.foo; rm -rf /")
            data = json.loads(result)
            assert "error" in data
            assert "invalid characters" in data["error"].lower()
            mock_run.assert_not_called()

    def test_kickstart_refuses_pipe_metachar(self):
        from launchd_tools.mcp_server import agent_kickstart

        with patch("subprocess.run") as mock_run:
            result = agent_kickstart("com.homeops.foo|evil")
            data = json.loads(result)
            assert "error" in data
            mock_run.assert_not_called()

    def test_kickstart_refuses_backtick_metachar(self):
        from launchd_tools.mcp_server import agent_kickstart

        with patch("subprocess.run") as mock_run:
            result = agent_kickstart("com.homeops.foo`id`")
            data = json.loads(result)
            assert "error" in data
            mock_run.assert_not_called()

    def test_kickstart_allows_valid_allowlisted_label(self):
        """A valid label in the allowlist reaches subprocess.run."""
        import launchd_tools.mcp_server as mcp_mod
        from launchd_tools.mcp_server import agent_kickstart

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Service kickstarted\n"
        mock_result.stderr = ""

        with patch.object(mcp_mod, "subprocess") as mock_sub:
            mock_sub.run.return_value = mock_result
            result = agent_kickstart("com.homeops.task-escalation")
            data = json.loads(result)
            assert data["status"] == "ok"
            mock_sub.run.assert_called_once()


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------


class TestParseList:
    def test_header_skipped(self):
        from launchd_tools.launchctl import _parse_list

        output = "PID\tStatus\tLabel\n-\t0\tcom.homeops.foo\n28172\t0\tcom.mrlesmithjr.bar\n"
        agents = _parse_list(output, ["com.homeops", "com.mrlesmithjr"])
        assert len(agents) == 2

    def test_idle_pid_is_none(self):
        from launchd_tools.launchctl import _parse_list

        output = "PID\tStatus\tLabel\n-\t0\tcom.homeops.foo\n"
        agents = _parse_list(output, ["com.homeops"])
        assert agents[0]["pid"] is None

    def test_running_pid_is_int(self):
        from launchd_tools.launchctl import _parse_list

        output = "PID\tStatus\tLabel\n12345\t0\tcom.homeops.bar\n"
        agents = _parse_list(output, ["com.homeops"])
        assert agents[0]["pid"] == 12345


class TestParsePrint:
    def test_never_exited_is_none(self, utility_anomaly_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(utility_anomaly_print)
        assert parsed.get("last_exit_code") is None

    def test_state_not_running(self, utility_anomaly_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(utility_anomaly_print)
        assert parsed["state"] == "not running"

    def test_state_running(self, context_manager_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(context_manager_print)
        assert parsed["state"] == "running"

    def test_pid_parsed(self, context_manager_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(context_manager_print)
        assert isinstance(parsed["pid"], int)
        assert parsed["pid"] > 0

    def test_runs_parsed(self, context_manager_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(context_manager_print)
        assert parsed["runs"] == 1

    def test_stdout_path_parsed(self, context_manager_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(context_manager_print)
        assert parsed.get("stdout_path") == "/Users/testuser/.claude-context/server.log"

    def test_event_triggers_detected_scheduled(self, forgejo_backup_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(forgejo_backup_print)
        assert parsed.get("has_event_triggers") is True

    def test_no_event_triggers_for_daemon(self, context_manager_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(context_manager_print)
        assert not parsed.get("has_event_triggers", False)

    def test_properties_keepalive_present(self, context_manager_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(context_manager_print)
        assert "keepalive" in (parsed.get("properties") or "")

    def test_exit0_parsed(self, forgejo_backup_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(forgejo_backup_print)
        assert parsed.get("last_exit_code") == 0

    def test_runs_zero_for_never_run(self, utility_anomaly_print):
        from launchd_tools.launchctl import parse_print

        parsed = parse_print(utility_anomaly_print)
        assert parsed.get("runs") == 0


# ---------------------------------------------------------------------------
# Kind classification unit tests
# ---------------------------------------------------------------------------


class TestClassifyKind:
    def test_event_triggers_is_scheduled(self):
        from launchd_tools.health import classify_kind

        parsed = {"has_event_triggers": True}
        assert classify_kind(parsed) == "scheduled"

    def test_keepalive_is_daemon(self):
        from launchd_tools.health import classify_kind

        parsed = {"properties": "keepalive | runatload | inferred program"}
        assert classify_kind(parsed) == "daemon"

    def test_runatload_is_daemon(self):
        from launchd_tools.health import classify_kind

        parsed = {"properties": "runatload | inferred program"}
        assert classify_kind(parsed) == "daemon"

    def test_on_demand_false_is_daemon(self):
        from launchd_tools.health import classify_kind

        parsed = {}
        assert classify_kind(parsed, on_demand=False) == "daemon"

    def test_default_is_scheduled(self):
        from launchd_tools.health import classify_kind

        parsed = {}
        assert classify_kind(parsed) == "scheduled"

    def test_event_triggers_beats_keepalive(self):
        """event triggers takes highest precedence over properties."""
        from launchd_tools.health import classify_kind

        # A job that has both would still be scheduled
        parsed = {"has_event_triggers": True, "properties": "keepalive"}
        assert classify_kind(parsed) == "scheduled"


# ---------------------------------------------------------------------------
# MCP tool annotations: read tools read-only; kickstart destructive
# ---------------------------------------------------------------------------


class TestToolAnnotations:
    @staticmethod
    def _annotations_by_name() -> dict:
        from launchd_tools.mcp_server import mcp

        return {t.name: t.annotations for t in mcp._tool_manager.list_tools()}

    def test_all_tools_declare_annotations(self):
        annotations = self._annotations_by_name()
        for name in ("agent_status", "agent_health", "agent_logs", "agent_kickstart"):
            ann = annotations.get(name)
            # readOnlyHint must be set explicitly: a bare ToolAnnotations() leaves it None.
            assert ann is not None and ann.readOnlyHint is not None, f"{name} is missing tool annotations"

    @pytest.mark.parametrize("name", ["agent_status", "agent_health", "agent_logs"])
    def test_read_tools_are_read_only(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is True

    def test_kickstart_is_destructive_not_read_only(self):
        ann = self._annotations_by_name()["agent_kickstart"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is True
        assert ann.idempotentHint is False

    def test_all_tools_are_closed_world(self):
        # The spec default for openWorldHint is true; these tools touch only local
        # launchctl/log state, so each must override it to false explicitly.
        for ann in self._annotations_by_name().values():
            assert ann.openWorldHint is False


# ---------------------------------------------------------------------------
# Config unit tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults_never_raise(self):
        from launchd_tools.config import get_label_prefixes, reset_cache

        reset_cache()
        prefixes = get_label_prefixes()
        assert isinstance(prefixes, list)
        assert len(prefixes) > 0

    def test_env_var_overrides(self, monkeypatch):
        from launchd_tools.config import get_label_prefixes, reset_cache

        reset_cache()
        monkeypatch.setenv("LAUNCHD_TOOLS_LABEL_PREFIXES", "com.test1,com.test2")
        prefixes = get_label_prefixes()
        assert prefixes == ["com.test1", "com.test2"]
        reset_cache()

    def test_default_contains_expected_prefixes(self):
        from launchd_tools.config import _DEFAULT_LABEL_PREFIXES

        assert "com.homeops" in _DEFAULT_LABEL_PREFIXES
        assert "com.ynab-tools" in _DEFAULT_LABEL_PREFIXES
        assert "com.mrlesmithjr" in _DEFAULT_LABEL_PREFIXES
