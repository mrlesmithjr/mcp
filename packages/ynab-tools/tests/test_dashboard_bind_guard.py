"""Tests for the dashboard's fail-closed bind guard.

Every dashboard route serves budget data, and HTTP Basic auth is only
enforced when DASHBOARD_PASSWORD is set. Binding a non-loopback address
without a password therefore publishes the full budget to the network, so
that combination must be refused outright rather than warned about.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="dashboard extra not installed")

from ynab_tools.dashboard.server import (  # noqa: E402
    is_loopback_host,
    require_password_for_remote_bind,
)


class TestIsLoopbackHost:
    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1", "127.1.2.3", "localhost", "LOCALHOST", "::1", "[::1]", " 127.0.0.1 ", ""],
    )
    def test_loopback_forms(self, host):
        assert is_loopback_host(host) is True

    @pytest.mark.parametrize(
        "host",
        ["0.0.0.0", "::", "192.168.1.50", "10.0.0.5", "example.com", "0", "127.0.0.1.evil.com"],
    )
    def test_non_loopback_forms(self, host):
        assert is_loopback_host(host) is False

    def test_unresolvable_hostname_fails_safe(self):
        """A name we cannot prove is loopback must not be treated as one."""
        assert is_loopback_host("not-a-real-host.invalid") is False


class TestRequirePasswordForRemoteBind:
    def test_loopback_without_password_allowed(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        require_password_for_remote_bind("127.0.0.1")  # must not raise

    def test_remote_bind_without_password_refused(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        with pytest.raises(SystemExit) as exc:
            require_password_for_remote_bind("0.0.0.0")
        assert "Refusing to bind" in str(exc.value)

    def test_whitespace_only_password_is_not_a_password(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_PASSWORD", "   ")
        with pytest.raises(SystemExit):
            require_password_for_remote_bind("0.0.0.0")

    def test_remote_bind_with_password_allowed(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_PASSWORD", "hunter2")
        require_password_for_remote_bind("0.0.0.0")  # must not raise

    def test_lan_address_without_password_refused(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
        with pytest.raises(SystemExit):
            require_password_for_remote_bind("192.168.1.50")
