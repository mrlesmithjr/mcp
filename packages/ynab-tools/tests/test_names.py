"""Tests for payee name normalization and matching."""

from ynab_tools.payees.names import (
    filter_mismatches,
    find_duplicate_payees,
    is_legitimate_rename,
    normalize_payee_name,
)


class TestNormalizePayeeName:
    def test_strips_city_state_suffix(self):
        # City+state and trailing digits are both stripped
        assert normalize_payee_name("CHEVRON 44512 Anytown GA") == "Chevron"

    def test_strips_store_number(self):
        assert normalize_payee_name("KROGER #1234 SOME") == "Kroger Some"

    def test_strips_trailing_digits(self):
        assert normalize_payee_name("WALMART 12345") == "Walmart"

    def test_preserves_known_uppercase(self):
        assert normalize_payee_name("cvs pharmacy") == "CVS pharmacy"

    def test_titlecases_all_caps(self):
        assert normalize_payee_name("HOME DEPOT") == "Home Depot"

    def test_system_payee_passthrough(self):
        assert normalize_payee_name("Starting Balance") == "Starting Balance"

    def test_transfer_passthrough(self):
        assert normalize_payee_name("Transfer: Checking") == "Transfer: Checking"

    def test_empty_string(self):
        assert normalize_payee_name("") == ""

    def test_none_returns_none(self):
        assert normalize_payee_name(None) is None


class TestIsLegitimateRename:
    def test_same_first_word(self):
        assert is_legitimate_rename("Chevron", "CHEVRON 44512") is True

    def test_known_variation(self):
        assert is_legitimate_rename("Walmart", "WAL-MART SUPERCENTER") is True

    def test_transfer_pattern(self):
        assert is_legitimate_rename("Transfer: Savings", "online banking transfer") is True

    def test_actual_mismatch(self):
        assert is_legitimate_rename("Target", "CHEVRON 44512") is False

    def test_empty_strings(self):
        assert is_legitimate_rename("", "") is True

    def test_payee_substring_in_import(self):
        assert is_legitimate_rename("Kroger", "KROGER FUEL CENTER 123") is True

    def test_payment_pattern(self):
        assert is_legitimate_rename("Bank of America", "PAYMENT - THANK YOU") is True


class TestFindDuplicatePayees:
    def test_finds_duplicates(self):
        payees = [
            {"id": "1", "name": "KROGER"},
            {"id": "2", "name": "Kroger"},
            {"id": "3", "name": "Target"},
        ]
        dupes = find_duplicate_payees(payees)
        assert len(dupes) == 1
        assert any(len(v) == 2 for v in dupes.values())

    def test_no_duplicates(self):
        payees = [
            {"id": "1", "name": "Kroger"},
            {"id": "2", "name": "Target"},
        ]
        assert find_duplicate_payees(payees) == {}

    def test_skips_system_payees(self):
        payees = [
            {"id": "1", "name": "Starting Balance"},
            {"id": "2", "name": "starting balance"},
        ]
        assert find_duplicate_payees(payees) == {}

    def test_skips_transfers(self):
        payees = [
            {"id": "1", "name": "Transfer: Checking"},
            {"id": "2", "name": "Transfer: Savings"},
        ]
        assert find_duplicate_payees(payees) == {}


class TestFilterMismatches:
    def test_filters_legitimate(self):
        txns = [
            {"payee_name": "Kroger", "original_import": "KROGER #1234"},
            {"payee_name": "Target", "original_import": "CHEVRON 44512"},
        ]
        result = filter_mismatches(txns)
        assert len(result) == 1
        assert result[0]["payee_name"] == "Target"
