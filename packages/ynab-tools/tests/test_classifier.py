"""Tests for the category classifier (pure classification logic)."""

from unittest.mock import MagicMock

from ynab_tools.categories.classifier import (
    HIGH,
    LOW,
    MEDIUM,
    classify_transaction,
    dollars_to_milliunits,
    get_split_payees,
    suggest_split,
)

SAMPLE_DEFS = {
    "categories": {
        "Auto: Fuel": {
            "description": "Gas and fuel",
            "keywords": ["gas", "fuel"],
            "typical_payees": ["Chevron", "Shell"],
        },
        "Groceries": {
            "description": "Grocery stores",
            "keywords": ["groceries"],
            "typical_payees": ["Kroger", "Publix"],
        },
    },
    "disambiguation_rules": [
        {
            "rule": "Walmart default is Groceries unless memo indicates otherwise",
            "payee": "Walmart",
        },
    ],
}


def _make_txn(payee: str = "", memo: str = "", amount: float = 0) -> dict:
    return {
        "id": "test-id",
        "date": "2025-01-01",
        "amount": amount,
        "payee_name": payee,
        "memo": memo,
        "account_name": "Checking",
        "approved": True,
    }


def _mock_conn_no_history():
    """Return a mock connection that returns no historical categories."""
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = []
    return conn


class TestClassifyTransaction:
    def test_typical_payee_match(self):
        txn = _make_txn(payee="Chevron")
        result = classify_transaction(txn, SAMPLE_DEFS, _mock_conn_no_history())
        assert result is not None
        assert result["category"] == "Auto: Fuel"
        assert result["confidence"] == HIGH

    def test_typical_payee_prefix_match(self):
        txn = _make_txn(payee="Kroger Fuel Center")
        result = classify_transaction(txn, SAMPLE_DEFS, _mock_conn_no_history())
        assert result is not None
        assert result["category"] == "Groceries"
        assert result["confidence"] == HIGH

    def test_keyword_match_in_memo(self):
        txn = _make_txn(payee="Unknown Store", memo="got gas for the car")
        result = classify_transaction(txn, SAMPLE_DEFS, _mock_conn_no_history())
        assert result is not None
        assert result["category"] == "Auto: Fuel"
        assert result["confidence"] == MEDIUM

    def test_disambiguation_rule(self):
        txn = _make_txn(payee="Walmart")
        result = classify_transaction(txn, SAMPLE_DEFS, _mock_conn_no_history())
        assert result is not None
        assert result["category"] == "Groceries"
        assert result["confidence"] == LOW

    def test_no_match(self):
        txn = _make_txn(payee="Random Place", memo="nothing relevant")
        result = classify_transaction(txn, SAMPLE_DEFS, _mock_conn_no_history())
        assert result is None

    def test_case_insensitive_payee(self):
        txn = _make_txn(payee="SHELL")
        result = classify_transaction(txn, SAMPLE_DEFS, _mock_conn_no_history())
        assert result is not None
        assert result["category"] == "Auto: Fuel"

    def test_empty_defs(self):
        txn = _make_txn(payee="Chevron")
        result = classify_transaction(txn, {}, _mock_conn_no_history())
        assert result is None


class TestGetSplitPayees:
    def test_returns_lowercase_set(self):
        defs = {"split_payees": ["Target", "Amazon", "Walmart"]}
        result = get_split_payees(defs)
        assert result == {"target", "amazon", "walmart"}

    def test_empty_when_no_key(self):
        result = get_split_payees({})
        assert result == set()

    def test_empty_list(self):
        result = get_split_payees({"split_payees": []})
        assert result == set()


class TestSuggestSplit:
    def test_proportional_split(self):
        txn = _make_txn(payee="Target", amount=-100.00)
        history = [
            {"category": "Groceries", "count": 60, "proportion": 0.6, "avg_amount": 30.0},
            {"category": "Home: Household Supplies", "count": 40, "proportion": 0.4, "avg_amount": 20.0},
        ]
        cat_id_map = {
            "Groceries": "cat-grocery-id",
            "Home: Household Supplies": "cat-household-id",
        }
        result = suggest_split(txn, history, cat_id_map)
        assert result is not None
        assert len(result) == 2
        assert result[0]["category"] == "Groceries"
        assert result[0]["amount"] == -60.00
        assert result[1]["category"] == "Home: Household Supplies"
        assert result[1]["amount"] == -40.00
        # Amounts must sum to transaction total
        assert sum(s["amount"] for s in result) == -100.00

    def test_remainder_goes_to_last(self):
        """Rounding remainder is assigned to the last category."""
        txn = _make_txn(payee="Target", amount=-100.00)
        history = [
            {"category": "A", "count": 33, "proportion": 0.333, "avg_amount": 10.0},
            {"category": "B", "count": 33, "proportion": 0.333, "avg_amount": 10.0},
            {"category": "C", "count": 34, "proportion": 0.334, "avg_amount": 10.0},
        ]
        cat_id_map = {"A": "id-a", "B": "id-b", "C": "id-c"}
        result = suggest_split(txn, history, cat_id_map)
        assert result is not None
        total = sum(s["amount"] for s in result)
        assert total == -100.00

    def test_filters_missing_categories(self):
        txn = _make_txn(payee="Target", amount=-100.00)
        history = [
            {"category": "Groceries", "count": 60, "proportion": 0.6, "avg_amount": 30.0},
            {"category": "Deleted Category", "count": 40, "proportion": 0.4, "avg_amount": 20.0},
        ]
        cat_id_map = {"Groceries": "cat-grocery-id"}
        result = suggest_split(txn, history, cat_id_map)
        assert result is not None
        assert len(result) == 1
        assert result[0]["category"] == "Groceries"
        assert result[0]["amount"] == -100.00

    def test_no_history_returns_none(self):
        txn = _make_txn(payee="Target", amount=-50.00)
        result = suggest_split(txn, [], {"Groceries": "id"})
        assert result is None

    def test_no_valid_categories_returns_none(self):
        txn = _make_txn(payee="Target", amount=-50.00)
        history = [
            {"category": "Deleted", "count": 10, "proportion": 1.0, "avg_amount": 50.0},
        ]
        result = suggest_split(txn, history, {"Groceries": "id"})
        assert result is None


class TestDollarsToMilliunits:
    def test_positive(self):
        assert dollars_to_milliunits(50.00) == 50000

    def test_negative(self):
        assert dollars_to_milliunits(-100.50) == -100500

    def test_zero(self):
        assert dollars_to_milliunits(0) == 0

    def test_rounding(self):
        assert dollars_to_milliunits(33.333) == 33333
