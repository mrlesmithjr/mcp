"""Tests for YNAB API client."""

from unittest.mock import MagicMock, patch

from ynab_tools.client import YNABClient


class TestClientInit:
    def test_plan_id_stored(self):
        client = YNABClient("token", "plan-123")
        assert client.plan_id == "plan-123"

    def test_auth_header_set(self):
        client = YNABClient("my-token", "plan-123")
        assert client.session.headers["Authorization"] == "Bearer my-token"


class TestClientURLs:
    """Verify all request methods use /plans/ path prefix."""

    def setup_method(self):
        self.client = YNABClient("token", "plan-abc")

    @patch.object(YNABClient, "_retry_on_429")
    def test_get_uses_plans_path(self, mock_retry):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"test": True}}
        mock_retry.return_value = mock_resp

        self.client._get("accounts")
        url = mock_retry.call_args[0][1]
        assert "/plans/plan-abc/accounts" in url
        assert "/budgets/" not in url

    @patch.object(YNABClient, "_retry_on_429")
    def test_patch_uses_plans_path(self, mock_retry):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"test": True}}
        mock_retry.return_value = mock_resp

        self.client._patch("transactions/tx1", {"transaction": {}})
        url = mock_retry.call_args[0][1]
        assert "/plans/plan-abc/transactions/tx1" in url

    @patch.object(YNABClient, "_retry_on_429")
    def test_post_uses_plans_path(self, mock_retry):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"test": True}}
        mock_retry.return_value = mock_resp

        self.client._post("payees", {"payee": {"name": "Test"}})
        url = mock_retry.call_args[0][1]
        assert "/plans/plan-abc/payees" in url

    @patch.object(YNABClient, "_retry_on_429")
    def test_delete_uses_plans_path(self, mock_retry):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"test": True}}
        mock_retry.return_value = mock_resp

        self.client._delete("transactions/tx1")
        url = mock_retry.call_args[0][1]
        assert "/plans/plan-abc/transactions/tx1" in url


class TestClientMethods:
    """Test client method return values and error handling."""

    def setup_method(self):
        self.client = YNABClient("token", "plan-abc")

    @patch.object(YNABClient, "_delete")
    def test_delete_transaction_success(self, mock_delete):
        mock_delete.return_value = {"transaction": {"id": "tx1", "deleted": True}}
        assert self.client.delete_transaction("tx1") is True
        mock_delete.assert_called_once_with("transactions/tx1")

    @patch.object(YNABClient, "_delete")
    def test_delete_transaction_failure(self, mock_delete):
        from requests import HTTPError

        mock_delete.side_effect = HTTPError("404")
        assert self.client.delete_transaction("tx1") is False

    @patch.object(YNABClient, "_post")
    def test_create_payee_success(self, mock_post):
        mock_post.return_value = {"payee": {"id": "p1", "name": "Test"}}
        result = self.client.create_payee("Test")
        assert result == {"id": "p1", "name": "Test"}

    @patch.object(YNABClient, "_post")
    def test_create_payee_failure(self, mock_post):
        from requests import HTTPError

        mock_post.side_effect = HTTPError("500")
        assert self.client.create_payee("Test") is None

    @patch.object(YNABClient, "_post")
    def test_create_category_group_success(self, mock_post):
        mock_post.return_value = {"category_group": {"id": "cg1", "name": "Savings"}}
        result = self.client.create_category_group("Savings")
        assert result == {"id": "cg1", "name": "Savings"}

    @patch.object(YNABClient, "_post")
    def test_create_category_group_failure(self, mock_post):
        from requests import HTTPError

        mock_post.side_effect = HTTPError("500")
        assert self.client.create_category_group("Savings") is None

    @patch.object(YNABClient, "_patch")
    def test_update_payee_success(self, mock_patch):
        mock_patch.return_value = {"payee": {"id": "p1", "name": "New Name"}}
        assert self.client.update_payee("p1", "New Name") is True

    @patch.object(YNABClient, "_patch")
    def test_update_payee_failure(self, mock_patch):
        from requests import HTTPError

        mock_patch.side_effect = HTTPError("404")
        assert self.client.update_payee("p1", "New Name") is False
