"""Tests for Hydrawise client caching in irrigation.get_client() (issue #75).

pydrawise's own Auth class already caches/refreshes its OAuth token
internally, but get_client() used to build a fresh Auth/Hydrawise instance
on every call and discard it afterward, so every irrigation tool
invocation paid a full password-grant round trip before pydrawise's own
caching ever got a chance to help. These tests assert the client (and its
underlying Auth) is constructed exactly once across sequential calls with
the same credentials, and rebuilt only when credentials actually change.
"""

from __future__ import annotations

from unittest.mock import patch

import lawnops.irrigation as irrigation
import pytest


def _config(username="user", password="pass"):
    return {"hydrawise": {"username": username, "password": password}}


@pytest.fixture(autouse=True)
def _reset_client_cache():
    """get_client() caches the client at module level - reset before and
    after every test so tests never leak state into each other."""
    irrigation._client = None
    irrigation._client_credentials = None
    yield
    irrigation._client = None
    irrigation._client_credentials = None


class TestGetClientCaching:
    def test_two_sequential_calls_with_same_credentials_authenticate_once(self):
        with (
            patch("lawnops.config.load_config", return_value=_config()),
            patch("lawnops.irrigation.HydrawiseAuth") as mock_auth,
            patch("lawnops.irrigation.Hydrawise") as mock_hydrawise,
        ):
            first = irrigation.get_client()
            second = irrigation.get_client()

        assert mock_auth.call_count == 1
        assert mock_hydrawise.call_count == 1
        assert first is second

    def test_credentials_change_rebuilds_the_client(self):
        with (
            patch("lawnops.config.load_config", return_value=_config(username="user1")),
            patch("lawnops.irrigation.HydrawiseAuth") as mock_auth,
            patch("lawnops.irrigation.Hydrawise") as mock_hydrawise,
        ):
            first = irrigation.get_client()

        with (
            patch("lawnops.config.load_config", return_value=_config(username="user2")),
            patch("lawnops.irrigation.HydrawiseAuth") as mock_auth,
            patch("lawnops.irrigation.Hydrawise") as mock_hydrawise,
        ):
            second = irrigation.get_client()

        assert mock_auth.call_count == 1
        assert mock_hydrawise.call_count == 1
        assert first is not second

    def test_missing_credentials_still_raises_and_does_not_cache(self):
        with patch("lawnops.config.load_config", return_value={"hydrawise": {}}):
            with pytest.raises(RuntimeError):
                irrigation.get_client()

        assert irrigation._client is None
