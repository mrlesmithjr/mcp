"""YNAB API client for fetching and updating budget data."""

import logging
import time

import requests

logger = logging.getLogger(__name__)

YNAB_BASE_URL = "https://api.ynab.com/v1"


class YNABClient:
    """YNAB API client with rate limiting."""

    def __init__(self, access_token: str, plan_id: str):
        self.plan_id = plan_id
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})
        self._last_request_time = 0

    def _rate_limit(self):
        """Respect YNAB rate limit: 200 req/hr (~0.35s between requests)."""
        elapsed = time.time() - self._last_request_time
        if elapsed < 0.35:
            time.sleep(0.35 - elapsed)
        self._last_request_time = time.time()

    def _retry_on_429(self, method, url, **kwargs) -> requests.Response:
        """Execute request with retry on 429 (rate limit exceeded)."""
        for attempt in range(4):
            self._rate_limit()
            resp = method(url, **kwargs)
            if resp.status_code != 429:
                return resp
            wait = int(resp.headers.get("Retry-After", 30 * (attempt + 1)))
            logger.warning(f"Rate limited (429), waiting {wait}s (attempt {attempt + 1}/4)")
            time.sleep(wait)
        return resp  # Last attempt, let caller handle

    def _raise_for_status(self, resp: requests.Response) -> None:
        """Raise HTTPError without exposing the plan ID embedded in the request URL."""
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            raise requests.HTTPError(
                f"YNAB API error: {resp.status_code} {resp.reason}",
            ) from None

    def _get(self, path: str, params: dict | None = None) -> dict:
        """Make a rate-limited GET request to a plan endpoint."""
        url = f"{YNAB_BASE_URL}/plans/{self.plan_id}/{path}"
        resp = self._retry_on_429(self.session.get, url, params=params, timeout=30)
        self._raise_for_status(resp)
        return resp.json()["data"]

    def _get_root(self, path: str) -> dict:
        """Make a rate-limited GET request to a root (non-budget) endpoint."""
        url = f"{YNAB_BASE_URL}/{path}"
        resp = self._retry_on_429(self.session.get, url, timeout=30)
        self._raise_for_status(resp)
        return resp.json()["data"]

    def _patch(self, path: str, payload: dict) -> dict:
        """Make a rate-limited PATCH request to a plan endpoint."""
        url = f"{YNAB_BASE_URL}/plans/{self.plan_id}/{path}"
        resp = self._retry_on_429(self.session.patch, url, json=payload, timeout=60)
        self._raise_for_status(resp)
        return resp.json()["data"]

    def _post(self, path: str, payload: dict) -> dict:
        """Make a rate-limited POST request to a plan endpoint."""
        url = f"{YNAB_BASE_URL}/plans/{self.plan_id}/{path}"
        resp = self._retry_on_429(self.session.post, url, json=payload, timeout=60)
        self._raise_for_status(resp)
        return resp.json()["data"]

    def _delete(self, path: str) -> dict:
        """Make a rate-limited DELETE request to a plan endpoint."""
        url = f"{YNAB_BASE_URL}/plans/{self.plan_id}/{path}"
        resp = self._retry_on_429(self.session.delete, url, timeout=60)
        self._raise_for_status(resp)
        return resp.json()["data"]

    # ── Read endpoints ──

    def get_plans(self) -> list[dict]:
        """Fetch all plans (not plan-scoped)."""
        data = self._get_root("plans")
        return data["plans"]

    def get_plan_detail(self, server_knowledge: int | None = None) -> dict:
        """Fetch full plan export (all entities in one call). Supports delta sync.

        Returns {"plan": {...}, "server_knowledge": int}.
        """
        params = {"last_knowledge_of_server": server_knowledge} if server_knowledge is not None else None
        url = f"{YNAB_BASE_URL}/plans/{self.plan_id}"
        resp = self._retry_on_429(self.session.get, url, params=params, timeout=120)
        self._raise_for_status(resp)
        data = resp.json()["data"]
        return {"plan": data["plan"], "server_knowledge": data["server_knowledge"]}

    def get_accounts(self, server_knowledge: int | None = None) -> dict:
        """Fetch accounts. Returns {"accounts": [...], "server_knowledge": int}."""
        params = {"last_knowledge_of_server": server_knowledge} if server_knowledge is not None else None
        data = self._get("accounts", params=params)
        return {"accounts": data["accounts"], "server_knowledge": data["server_knowledge"]}

    def get_categories(self, server_knowledge: int | None = None) -> dict:
        """Fetch category groups. Returns {"category_groups": [...], "server_knowledge": int}."""
        params = {"last_knowledge_of_server": server_knowledge} if server_knowledge is not None else None
        data = self._get("categories", params=params)
        return {"category_groups": data["category_groups"], "server_knowledge": data["server_knowledge"]}

    def get_months(self) -> list[dict]:
        """Fetch all budget months (summary only)."""
        return self._get("months")["months"]

    def get_month_detail(self, month: str) -> dict:
        """Fetch detailed budget month (includes category breakdowns)."""
        return self._get(f"months/{month}")["month"]

    def get_transactions(self, since_date: str | None = None, server_knowledge: int | None = None) -> dict:
        """Fetch transactions. Returns {"transactions": [...], "server_knowledge": int}."""
        params = {}
        if server_knowledge is not None:
            params["last_knowledge_of_server"] = server_knowledge
        elif since_date:
            params["since_date"] = since_date
        data = self._get("transactions", params=params)
        return {"transactions": data["transactions"], "server_knowledge": data["server_knowledge"]}

    def get_payees(self, server_knowledge: int | None = None) -> dict:
        """Fetch payees. Returns {"payees": [...], "server_knowledge": int}."""
        params = {"last_knowledge_of_server": server_knowledge} if server_knowledge is not None else None
        data = self._get("payees", params=params)
        return {"payees": data["payees"], "server_knowledge": data["server_knowledge"]}

    def get_money_movements(self, server_knowledge: int | None = None) -> dict:
        """Fetch all money movements. Returns {"money_movements": [...], "server_knowledge": int}."""
        params = {}
        if server_knowledge is not None:
            params["last_knowledge_of_server"] = server_knowledge
        data = self._get("money_movements", params=params or None)
        return {
            "money_movements": data["money_movements"],
            "server_knowledge": data["server_knowledge"],
        }

    def get_transaction(self, transaction_id: str) -> dict:
        """Fetch a single transaction by ID."""
        data = self._get(f"transactions/{transaction_id}")
        return data["transaction"]

    def get_transactions_by_payee(self, payee_id: str) -> list[dict]:
        """Fetch all transactions for a specific payee."""
        data = self._get(f"payees/{payee_id}/transactions")
        return data["transactions"]

    # ── Write endpoints ──

    def create_payee(self, name: str) -> dict | None:
        """Create a new payee. Returns the created payee data, or None on failure."""
        try:
            data = self._post("payees", {"payee": {"name": name}})
            return data.get("payee")
        except requests.HTTPError as e:
            logger.error(f"Failed to create payee: {e}")
            return None

    def update_transaction(self, transaction_id: str, **fields) -> bool:
        """Update a single transaction. Accepts payee_name, category_id, etc."""
        try:
            self._patch(
                f"transactions/{transaction_id}",
                {"transaction": fields},
            )
            return True
        except requests.HTTPError as e:
            logger.error(f"Failed to update transaction {transaction_id}: {e}")
            return False

    def delete_transaction(self, transaction_id: str) -> bool:
        """Delete a transaction. Returns True on success."""
        try:
            self._delete(f"transactions/{transaction_id}")
            return True
        except requests.HTTPError as e:
            logger.error(f"Failed to delete transaction {transaction_id}: {e}")
            return False

    def update_category_budget(self, month: str, category_id: str, budgeted_milliunits: int) -> dict:
        """Update the budgeted amount for a category in a given month.

        month: YYYY-MM-DD format (e.g. "2026-03-01")
        category_id: YNAB category UUID
        budgeted_milliunits: amount in milliunits (1 dollar = 1000)
        """
        return self._patch(
            f"months/{month}/categories/{category_id}",
            {"category": {"budgeted": budgeted_milliunits}},
        )

    def update_category_goal(
        self, category_id: str, goal_type: str, goal_target_milliunits: int, goal_target_month: str | None = None
    ) -> bool:
        """Set or update the goal/target on a category.

        goal_type: "MF" (monthly funding), "TB" (target balance),
                   "TBD" (target balance by date), "NEED" (needed for spending)
        goal_target_milliunits: target amount in milliunits
        goal_target_month: required for TBD type (YYYY-MM-DD format)
        """
        payload: dict = {
            "goal_type": goal_type,
            "goal_target": goal_target_milliunits,
        }
        if goal_target_month:
            payload["goal_target_month"] = goal_target_month
        try:
            self._patch(f"categories/{category_id}", {"category": payload})
            return True
        except requests.HTTPError as e:
            logger.error(f"Failed to update category goal: {e}")
            return False

    def clear_category_goal(self, category_id: str) -> bool:
        """Remove the goal from a category."""
        try:
            self._patch(f"categories/{category_id}", {"category": {"goal_type": None, "goal_target": 0}})
            return True
        except requests.HTTPError as e:
            logger.error(f"Failed to clear category goal: {e}")
            return False

    def create_transaction(self, transaction: dict) -> dict | None:
        """Create a single transaction.

        transaction: {"account_id": "...", "date": "YYYY-MM-DD",
                      "amount": milliunits, "payee_name": "...",
                      "cleared": "reconciled", "approved": True, ...}
        Returns the created transaction data, or None on failure.
        """
        try:
            data = self._post("transactions", {"transaction": transaction})
            return data.get("transaction")
        except requests.HTTPError as e:
            logger.error(f"Failed to create transaction: {e}")
            return None

    def split_transaction(self, transaction_id: str, subtransactions: list[dict]) -> bool:
        """
        Convert a single-category transaction into a split transaction.

        subtransactions: [{"amount": milliunits, "category_id": "...", "memo": "..."}, ...]
        Amounts must be in milliunits (negative for outflows) and sum to parent amount.

        NOTE: Once split via the API, subtransactions cannot be modified - only
        deleted by removing the split in the YNAB app.
        """
        try:
            self._patch(
                f"transactions/{transaction_id}",
                {
                    "transaction": {
                        "subtransactions": subtransactions,
                    }
                },
            )
            return True
        except requests.HTTPError as e:
            logger.error(f"Failed to split transaction {transaction_id}: {e}")
            return False

    def create_category_group(self, name: str) -> dict | None:
        """Create a new category group.

        Returns the created category group data, or None on failure.
        """
        try:
            data = self._post("category_groups", {"category_group": {"name": name}})
            return data.get("category_group")
        except requests.HTTPError as e:
            logger.error(f"Failed to create category group: {e}")
            return None

    def create_category(self, category_group_id: str, name: str) -> dict | None:
        """Create a new category in a category group.

        Returns the created category data, or None on failure.
        """
        try:
            data = self._post(
                "categories",
                {
                    "category": {
                        "category_group_id": category_group_id,
                        "name": name,
                    },
                },
            )
            return data.get("category")
        except requests.HTTPError as e:
            logger.error(f"Failed to create category: {e}")
            return None

    def update_payee(self, payee_id: str, name: str) -> bool:
        """Rename a payee. All transactions referencing this payee update automatically."""
        try:
            self._patch(f"payees/{payee_id}", {"payee": {"name": name}})
            return True
        except requests.HTTPError as e:
            logger.error(f"Failed to rename payee {payee_id}: {e}")
            return False

    def bulk_update_transactions(self, updates: list[dict]) -> dict:
        """
        Bulk update transactions (max 1000 per call).

        updates: [{"id": "...", "payee_name": "...", "category_id": "..."}]
        Returns: {"success": int, "failed": int}
        """
        try:
            self._patch("transactions", {"transactions": updates})
            return {"success": len(updates), "failed": 0}
        except requests.HTTPError as e:
            logger.error(f"Bulk update failed: {e}")
            return {"success": 0, "failed": len(updates)}
