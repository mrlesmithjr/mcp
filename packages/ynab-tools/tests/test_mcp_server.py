"""Tests for ynab_tools/mcp_server.py - _capture helper and SystemExit handling."""

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# MCP tool annotations: registry-only, no network, no YNAB API
# ---------------------------------------------------------------------------


class TestToolAnnotations:
    @staticmethod
    def _annotations_by_name() -> dict:
        from ynab_tools.mcp_server import mcp

        return {t.name: t.annotations for t in mcp._tool_manager.list_tools()}

    def test_all_tools_declare_annotations(self):
        annotations = self._annotations_by_name()
        assert annotations, "No tools registered"
        for name, ann in annotations.items():
            assert ann is not None, f"{name} has no annotations"
            assert ann.readOnlyHint is not None, f"{name} is missing readOnlyHint"

    @pytest.mark.parametrize(
        "name",
        [
            "sync_status",
            "budget_check",
            "month_end_report",
            "monthly_summary",
            "spending_report",
            "category_trend",
            "spending_pace",
            "recent_transactions",
            "large_expenses",
            "income_report",
            "paycheck_forecast",
            "debt_status",
            "sinking_funds",
            "category_balance",
            "transfers",
            "funding_status",
            "fund_goals_preview",
            "paycheck_funding",
            "paycheck_breakdown",
            "two_pot_compliance",
            "reconcile_list",
            "get_subscriptions",
            "movement_log",
            "planned_expenses",
            "payee_audit",
            "payee_preview",
        ],
    )
    def test_local_read_tools_are_read_only_and_closed_world(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is True, f"{name}: expected readOnlyHint=True"
        assert ann.openWorldHint is False, f"{name}: expected openWorldHint=False"

    def test_unapproved_transactions_is_read_only_open_world(self):
        # Verifies approval status against the YNAB API on every call.
        ann = self._annotations_by_name()["unapproved_transactions"]
        assert ann.readOnlyHint is True
        assert ann.openWorldHint is True

    @pytest.mark.parametrize(
        "name",
        [
            "fund_goals_apply",
            "fund_category",
            "add_transaction",
            "update_transaction",
            "categorize_transactions",
            "paycheck_funding_apply",
            "reconcile_account",
            "payee_fix",
            "create_payee",
            "create_category_group",
            "create_category",
        ],
    )
    def test_reversible_ynab_write_tools(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False, f"{name}: expected readOnlyHint=False"
        assert ann.destructiveHint is False, f"{name}: expected destructiveHint=False"
        assert ann.openWorldHint is True, f"{name}: expected openWorldHint=True"

    @pytest.mark.parametrize(
        "name",
        [
            "net_worth",
            "add_planned_expense",
            "complete_planned_expense",
            "edit_planned_expense",
        ],
    )
    def test_reversible_local_write_tools(self, name):
        ann = self._annotations_by_name()[name]
        assert ann.readOnlyHint is False, f"{name}: expected readOnlyHint=False"
        assert ann.destructiveHint is False, f"{name}: expected destructiveHint=False"
        assert ann.openWorldHint is False, f"{name}: expected openWorldHint=False"

    def test_delete_transaction_is_destructive(self):
        ann = self._annotations_by_name()["delete_transaction"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is True
        assert ann.openWorldHint is True

    def test_clear_category_goal_is_destructive_and_idempotent(self):
        ann = self._annotations_by_name()["clear_category_goal"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is True
        assert ann.idempotentHint is True
        assert ann.openWorldHint is True

    def test_set_category_goal_is_reversible_and_idempotent(self):
        ann = self._annotations_by_name()["set_category_goal"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.idempotentHint is True
        assert ann.openWorldHint is True

    def test_approve_transactions_is_idempotent(self):
        ann = self._annotations_by_name()["approve_transactions"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.idempotentHint is True
        assert ann.openWorldHint is True

    def test_sync_data_is_open_world_reversible_write(self):
        ann = self._annotations_by_name()["sync_data"]
        assert ann.readOnlyHint is False
        assert ann.destructiveHint is False
        assert ann.openWorldHint is True


class TestCapture:
    def test_normal_function_captures_output(self):
        from ynab_tools.mcp_server import _capture

        def printer():
            print("hello world")

        result = _capture(printer)
        assert result == "hello world\n"

    def test_function_with_no_output_returns_empty_string(self):
        from ynab_tools.mcp_server import _capture

        def silent():
            pass

        result = _capture(silent)
        assert result == ""

    def test_function_that_raises_value_error_propagates(self):
        from ynab_tools.mcp_server import _capture

        def raiser():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _capture(raiser)

    def test_function_returning_none_still_captures_stdout(self):
        from ynab_tools.mcp_server import _capture

        def prints_and_returns_none():
            print("output")
            return None

        result = _capture(prints_and_returns_none)
        assert "output" in result

    def test_passes_args_and_kwargs_to_function(self):
        from ynab_tools.mcp_server import _capture

        def greet(name, greeting="Hello"):
            print(f"{greeting}, {name}!")

        result = _capture(greet, "Alice", greeting="Hi")
        assert result == "Hi, Alice!\n"

    def test_multiple_print_calls_all_captured(self):
        from ynab_tools.mcp_server import _capture

        def multi():
            print("line one")
            print("line two")

        result = _capture(multi)
        assert "line one" in result
        assert "line two" in result


class TestPayeeToolsSystemExit:
    """Verify SystemExit from missing credentials is caught and returns an error string."""

    def test_payee_audit_returns_error_on_system_exit(self):
        from ynab_tools.mcp_server import payee_audit

        with patch("ynab_tools.config.require_credentials", side_effect=SystemExit(1)):
            result = payee_audit()
        assert isinstance(result, str)
        assert "configure" in result.lower() or "credentials" in result.lower()

    def test_payee_preview_returns_error_on_system_exit(self):
        from ynab_tools.mcp_server import payee_preview

        with patch("ynab_tools.config.require_credentials", side_effect=SystemExit(1)):
            result = payee_preview()
        assert isinstance(result, str)
        assert "configure" in result.lower() or "credentials" in result.lower()

    def test_payee_fix_returns_error_on_system_exit(self):
        from ynab_tools.mcp_server import payee_fix

        with patch("ynab_tools.config.require_credentials", side_effect=SystemExit(1)):
            result = payee_fix()
        assert isinstance(result, str)
        assert "configure" in result.lower() or "credentials" in result.lower()

    def test_create_payee_returns_error_on_system_exit(self):
        from ynab_tools.mcp_server import create_payee

        with patch("ynab_tools.config.require_credentials", side_effect=SystemExit(1)):
            result = create_payee("Test Payee")
        assert isinstance(result, str)
        assert "configure" in result.lower() or "credentials" in result.lower()

    def test_payee_audit_result_is_not_exception(self):
        """Ensure the tool does not propagate SystemExit to the caller."""
        from ynab_tools.mcp_server import payee_audit

        with patch("ynab_tools.config.require_credentials", side_effect=SystemExit(1)):
            # Should not raise - should return a string
            result = payee_audit()
        assert not isinstance(result, BaseException)

    def test_create_payee_result_is_not_exception(self):
        from ynab_tools.mcp_server import create_payee

        with patch("ynab_tools.config.require_credentials", side_effect=SystemExit(1)):
            result = create_payee("Whatever")
        assert not isinstance(result, BaseException)
