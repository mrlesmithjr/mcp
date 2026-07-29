"""Tests for brokerage CSV parsing and conversion."""

from datetime import datetime

import pytest

from ynab_tools.importers.brokerage import (
    Transaction,
    detect_format,
    parse_currency,
    parse_date,
    to_ynab_csv,
)


class TestParseDate:
    def test_us_format(self):
        assert parse_date("12/25/2025") == datetime(2025, 12, 25)

    def test_iso_format(self):
        assert parse_date("2025-12-25") == datetime(2025, 12, 25)

    def test_dash_us_format(self):
        assert parse_date("12-25-2025") == datetime(2025, 12, 25)

    def test_empty_string(self):
        assert parse_date("") is None

    def test_none_returns_none(self):
        assert parse_date(None) is None

    def test_invalid_date(self):
        assert parse_date("not-a-date") is None


class TestParseCurrency:
    def test_simple_number(self):
        assert parse_currency("100.50") == 100.50

    def test_dollar_sign(self):
        assert parse_currency("$1,234.56") == 1234.56

    def test_parentheses_negative(self):
        assert parse_currency("($50.00)") == -50.00

    def test_na_value(self):
        assert parse_currency("N/A") is None

    def test_empty_string(self):
        assert parse_currency("") is None

    def test_none_value(self):
        assert parse_currency(None) is None


class TestDetectFormat:
    def test_fidelity(self):
        content = "Run Date,Action,Symbol,Amount ($)\n01/15/2025,DIVIDEND,FXAIX,100.00"
        assert detect_format(content) == "fidelity"

    def test_merrill_lynch(self):
        content = "My 401k Account\nDate,Transaction,Status,Amount\n01/15/2025,Contribution,Completed,500.00"
        assert detect_format(content) == "merrill_lynch"

    def test_unknown_format(self):
        import pytest

        with pytest.raises(ValueError, match="Unrecognized CSV format"):
            detect_format("random,columns,here\n1,2,3")


class TestToYnabCsv:
    def test_basic_conversion(self):
        txns = [
            Transaction(
                date=datetime(2025, 1, 15),
                transaction_type="Dividend",
                raw_action="DIVIDEND",
                symbol="FXAIX",
                amount=100.50,
            ),
        ]
        csv_output = to_ynab_csv(txns)
        lines = csv_output.strip().split("\n")
        assert lines[0].strip() == "Date,Payee,Memo,Amount"
        assert "01/15/2025" in lines[1]
        assert "Dividend" in lines[1]
        assert "FXAIX" in lines[1]

    def test_empty_list(self):
        csv_output = to_ynab_csv([])
        assert csv_output.strip() == "Date,Payee,Memo,Amount"


# ── Fidelity positions importer ──────────────────────────────────────────────

FIDELITY_CSV_SAMPLE = (
    "Account Number,Account Name,Symbol,Description,Quantity,Last Price,"
    "Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,"
    "Total Gain/Loss Dollar,Cost Basis Total,Percent Of Account\n"
    "X12345678,Roth IRA,FXAIX,FIDELITY 500 INDEX FUND,50.000,175.00,"
    "8750.00,25.00,0.29%,1250.00,7500.00,87.50%\n"
    "X12345678,Roth IRA,SPAXX*,FIDELITY GOV MONEY MARKET,1000.00,1.00,"
    "1000.00,0.00,0.00%,0.00,1000.00,10.00%\n"
    "X12345678,Roth IRA,,,,,,,,,\n"
    "X87654321,Taxable Brokerage,VTI,VANGUARD TOTAL STOCK,20.000,250.00,"
    "5000.00,10.00,0.20%,500.00,4500.00,100.00%\n"
)


class TestFidelityParsePositions:
    def test_returns_dict_keyed_by_account_number(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        assert "X12345678" in result
        assert "X87654321" in result

    def test_account_name_captured(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        assert result["X12345678"]["name"] == "Roth IRA"
        assert result["X87654321"]["name"] == "Taxable Brokerage"

    def test_total_value_summed_across_holdings(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        # X12345678: FXAIX $8750 + SPAXX $1000 = $9750
        assert result["X12345678"]["total_value"] == pytest.approx(9750.0)
        # X87654321: VTI $5000
        assert result["X87654321"]["total_value"] == pytest.approx(5000.0)

    def test_holdings_list_populated(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        holdings = result["X12345678"]["holdings"]
        symbols = [h["symbol"] for h in holdings]
        assert "FXAIX" in symbols
        # SPAXX* should be stripped of trailing asterisk
        assert "SPAXX" in symbols

    def test_symbol_asterisk_stripped(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        holdings = result["X12345678"]["holdings"]
        for h in holdings:
            assert not h["symbol"].endswith("*"), f"Symbol {h['symbol']} still has trailing *"

    def test_holding_has_expected_fields(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        fxaix = next(h for h in result["X12345678"]["holdings"] if h["symbol"] == "FXAIX")
        assert "value" in fxaix
        assert "cost_basis" in fxaix
        assert "gain_loss" in fxaix
        assert "pct_of_account" in fxaix

    def test_holding_value_parsed_correctly(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        fxaix = next(h for h in result["X12345678"]["holdings"] if h["symbol"] == "FXAIX")
        assert fxaix["value"] == pytest.approx(8750.0)

    def test_holding_cost_basis_parsed(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        fxaix = next(h for h in result["X12345678"]["holdings"] if h["symbol"] == "FXAIX")
        assert fxaix["cost_basis"] == pytest.approx(7500.0)

    def test_holding_gain_loss_parsed(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        fxaix = next(h for h in result["X12345678"]["holdings"] if h["symbol"] == "FXAIX")
        assert fxaix["gain_loss"] == pytest.approx(1250.0)

    def test_rows_without_symbol_excluded_from_holdings(self):
        from ynab_tools.importers.fidelity import parse_positions

        result = parse_positions(FIDELITY_CSV_SAMPLE)
        # Row with empty Symbol is not appended to holdings list
        holdings = result["X12345678"]["holdings"]
        symbols = [h["symbol"] for h in holdings]
        assert "" not in symbols

    def test_invalid_csv_raises_value_error(self):
        from ynab_tools.importers.fidelity import parse_positions

        with pytest.raises(ValueError, match="Could not find header row"):
            parse_positions("random,data\n1,2,3")

    def test_empty_accounts_when_no_valid_rows(self):
        from ynab_tools.importers.fidelity import parse_positions

        # Has header but no valid account rows
        minimal = (
            "Account Number,Account Name,Symbol,Description,Quantity,Last Price,"
            "Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,"
            "Total Gain/Loss Dollar,Cost Basis Total,Percent Of Account\n"
        )
        result = parse_positions(minimal)
        assert result == {}
