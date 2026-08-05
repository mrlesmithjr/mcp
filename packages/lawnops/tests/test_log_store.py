"""Tests for the lawn_log backend abstraction (issue #57).

Covers: log_store dispatch, the sqlite/markdown backends, log_compute's
shared report logic, and the 16 rewired MCP tools (via direct function
calls with a monkeypatched lawnops.mcp_server._config, matching the
pattern the rest of this test suite uses for MCP tool coverage).
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from lawnops import log_compute, log_store
from lawnops.db.schema import SCHEMA_SQL


@pytest.fixture
def sqlite_config(tmp_path):
    """Zero-config lawn_log -- no `lawn_log` key at all, matching a real install."""
    db = tmp_path / "lawnops.db"
    return {
        "database": {"path": str(db)},
        "mowing": {"schedule_day": "friday", "default_provider": "Test Mowing"},
    }


@pytest.fixture
def markdown_config(tmp_path):
    note = tmp_path / "Lawn Log.md"
    db = tmp_path / "lawnops.db"  # must never be created under this config
    return {
        "database": {"path": str(db)},
        "lawn_log": {"backend": "markdown", "markdown": {"note": str(note)}},
        "mowing": {"schedule_day": "friday", "default_provider": "Test Mowing"},
    }


# ── log_store dispatch ──


class TestBackendDispatch:
    def test_default_backend_is_sqlite(self, sqlite_config):
        from lawnops.log_backends.sqlite_backend import SqliteBackend

        assert isinstance(log_store._backend(sqlite_config), SqliteBackend)

    def test_explicit_markdown_backend(self, markdown_config):
        from lawnops.log_backends.markdown_backend import MarkdownBackend

        assert isinstance(log_store._backend(markdown_config), MarkdownBackend)

    def test_unknown_backend_raises(self, sqlite_config):
        sqlite_config["lawn_log"] = {"backend": "sheets"}
        with pytest.raises(RuntimeError, match="Unknown lawn_log.backend"):
            log_store._backend(sqlite_config)


# ── Default install: sqlite, zero config, unchanged behavior ──


class TestSqliteDefaultUnchanged:
    def test_treatment_add_and_list_round_trip(self, sqlite_config):
        log_store.append_row(
            sqlite_config,
            "treatments",
            {"date": "2026-03-01", "area": "front", "product": "Prodiamine", "cost": 25.0, "notes": "n1"},
        )
        rows = log_store.read_table(sqlite_config, "treatments")
        treatments, year = log_compute.list_treatments(rows, 2026)
        assert year == 2026
        assert treatments == [
            {
                "id": 1,
                "date": "2026-03-01",
                "treatment_area": "front",
                "product": "Prodiamine",
                "method": None,
                "cost": 25.0,
                "notes": "n1",
            }
        ]

    def test_product_add_sets_last_ordered_like_db_add_product(self, sqlite_config):
        from datetime import datetime

        log_store.append_row(
            sqlite_config, "products", {"name": "Widget", "qty_on_hand": 2, "unit": "bag", "cost_each": 10}
        )
        rows = log_store.read_table(sqlite_config, "products")
        assert rows[0]["last_ordered"] == datetime.now().strftime("%Y-%m-%d")

    def test_delete_by_id(self, sqlite_config):
        log_store.append_row(sqlite_config, "equipment", {"name": "Mower", "cost": 300})
        rows = log_store.read_table(sqlite_config, "equipment")
        deleted = log_store.delete_row(sqlite_config, "equipment", {"id": rows[0]["id"]})
        assert deleted == 1
        assert log_store.read_table(sqlite_config, "equipment") == []


# ── MCP tool wiring (both backends) ──


class TestMcpToolsSqliteBackend:
    def test_treatment_add_then_list(self, sqlite_config, monkeypatch):
        import lawnops.mcp_server as ms

        monkeypatch.setattr(ms, "_config", lambda: sqlite_config)

        added = json.loads(ms.treatment_add(date="2026-03-01", area="front", product="Prodiamine", cost=25))
        assert added["added"] is True

        listed = json.loads(ms.treatment_list())
        assert listed["count"] == 1
        assert listed["treatments"][0]["product"] == "Prodiamine"


class TestMcpToolsMarkdownBackend:
    def test_treatment_add_writes_markdown_and_list_returns_it(self, markdown_config, monkeypatch):
        import lawnops.mcp_server as ms

        monkeypatch.setattr(ms, "_config", lambda: markdown_config)

        added = json.loads(ms.treatment_add(date="2026-03-01", area="front", product="Prodiamine", cost=25))
        assert added["added"] is True

        note_path = markdown_config["lawn_log"]["markdown"]["note"]
        from pathlib import Path

        content = Path(note_path).read_text()
        assert "## Treatments" in content
        assert "Prodiamine" in content

        listed = json.loads(ms.treatment_list())
        assert listed["count"] == 1
        assert listed["treatments"][0] == {
            "id": 1,
            "date": "2026-03-01",
            "treatment_area": "front",
            "product": "Prodiamine",
            "method": None,
            "cost": 25.0,
            "notes": None,
        }

    def test_no_write_tool_touches_sqlite(self, markdown_config, monkeypatch):
        from pathlib import Path

        import lawnops.mcp_server as ms

        monkeypatch.setattr(ms, "_config", lambda: markdown_config)

        ms.treatment_add(date="2026-03-01", area="front", product="Prodiamine", cost=25)
        ms.product_add(name="Widget", category="herbicide", qty=2)
        ms.mowing_add(date="2026-03-05", cost=50)
        ms.purchase_add(date="2026-03-01", item="Fert", cost=30, category="product")
        ms.equipment_add(name="Mower", cost=300)

        db_path = Path(markdown_config["database"]["path"])
        assert not db_path.exists()

    def test_missing_path_raises_and_never_falls_back_to_sqlite(self, tmp_path, monkeypatch):
        import lawnops.mcp_server as ms

        db_path = tmp_path / "lawnops.db"
        config = {"database": {"path": str(db_path)}, "lawn_log": {"backend": "markdown", "markdown": {}}}
        monkeypatch.setattr(ms, "_config", lambda: config)

        with pytest.raises(RuntimeError):
            log_store.append_row(config, "treatments", {"date": "2026-01-01", "area": "x", "product": "y"})

        result = json.loads(ms.treatment_add(date="2026-01-01", area="x", product="y"))
        assert "error" in result
        assert not db_path.exists()

    def test_per_entity_routing_and_custom_heading(self, tmp_path, monkeypatch):
        import lawnops.mcp_server as ms

        main_note = tmp_path / "main.md"
        equip_note = tmp_path / "equipment.md"
        config = {
            "database": {"path": str(tmp_path / "lawnops.db")},
            "lawn_log": {
                "backend": "markdown",
                "markdown": {
                    "note": str(main_note),
                    "entities": {"equipment": str(equip_note)},
                    "section_headings": {"mowing_visits": "Mowing Log"},
                },
            },
            "mowing": {"schedule_day": "friday", "default_provider": "Test Mowing"},
        }
        monkeypatch.setattr(ms, "_config", lambda: config)

        ms.equipment_add(name="Trimmer", cost=100)
        ms.treatment_add(date="2026-01-01", area="front", product="X")
        ms.mowing_add(date="2026-01-02", cost=40)

        assert equip_note.exists()
        assert "Trimmer" in equip_note.read_text()
        assert "## Treatments" not in equip_note.read_text()

        main_content = main_note.read_text()
        assert "## Treatments" in main_content
        assert "## Mowing Log" in main_content
        assert "## Mowing\n" not in main_content
        assert "Trimmer" not in main_content


# ── Parametrized: identical report totals across both backends ──


@pytest.mark.parametrize("backend", ["sqlite", "markdown"])
class TestReportParityAcrossBackends:
    def _config(self, backend, tmp_path):
        db = tmp_path / "lawnops.db"
        config = {
            "database": {"path": str(db)},
            "mowing": {"schedule_day": "friday", "default_provider": "Test Mowing"},
        }
        if backend == "markdown":
            note = tmp_path / "log.md"
            config["lawn_log"] = {"backend": "markdown", "markdown": {"note": str(note)}}
        return config

    def _seed(self, config):
        log_store.append_row(
            config, "purchases", {"date": "2026-03-01", "item": "Fert", "category": "product", "cost": 30}
        )
        log_store.append_row(
            config, "purchases", {"date": "2026-03-10", "item": "Mower blade", "category": "equipment", "cost": 20}
        )
        log_store.append_row(config, "mowing_visits", {"date": "2026-03-05", "provider": "Test Mowing", "cost": 50})
        log_store.append_row(config, "mowing_visits", {"date": "2026-03-12", "provider": "Test Mowing", "cost": 50})
        log_store.append_row(
            config, "products", {"name": "Widget", "category": "herbicide", "qty_on_hand": 0, "cost_each": 10}
        )
        log_store.append_row(
            config, "treatments", {"date": "2026-03-01", "area": "front", "product": "Widget", "cost": 25}
        )

    def test_spend_report_total(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        self._seed(config)
        rows = log_store.read_table(config, "purchases")
        _, grand_total, _, _ = log_compute.spend_report(rows, 2026)
        assert grand_total == 50.0

    def test_mowing_summary_total(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        self._seed(config)
        rows = log_store.read_table(config, "mowing_visits")
        _, total_visits, total_cost, _ = log_compute.mowing_summary(rows, 2026)
        assert total_visits == 2
        assert total_cost == 100.0

    def test_product_alerts_count(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        self._seed(config)
        products = log_store.read_table(config, "products")
        treatments = log_store.read_table(config, "treatments")
        alerts = log_compute.reorder_alerts(products, treatments)
        assert len(alerts) == 1
        assert alerts[0]["name"] == "Widget"
        assert alerts[0]["treatment_count"] == 1


# ── log-export migration ──


class TestLogExport:
    def _seeded_sqlite_config(self, tmp_path):
        db = tmp_path / "lawnops.db"
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO treatments (date, treatment_area, product, method, amount, soil_temp_f, cost, notes) "
            "VALUES ('2026-03-01', 'front', 'Prodiamine', 'spray', '2oz', 62, 25.0, 'n1')"
        )
        conn.execute(
            "INSERT INTO products (name, category, qty_on_hand, unit, last_ordered, cost_each, source, notes) "
            "VALUES ('Widget', 'herbicide', 2, 'bottle', '2026-01-01', 10.0, 'Amazon', NULL)"
        )
        conn.execute(
            "INSERT INTO mowing_visits (date, provider, cost, notes) VALUES ('2026-03-05', 'Test Mowing', 50.0, NULL)"
        )
        conn.commit()
        conn.close()
        note = tmp_path / "log.md"
        return {
            "database": {"path": str(db)},
            "lawn_log": {"backend": "markdown", "markdown": {"note": str(note)}},
        }

    def test_preview_writes_nothing(self, tmp_path):
        from lawnops.log_migrate import export_log

        config = self._seeded_sqlite_config(tmp_path)
        counts = export_log(config, preview=True)
        assert counts["treatments"] == 1
        assert counts["products"] == 1
        assert counts["mowing_visits"] == 1
        note_path = config["lawn_log"]["markdown"]["note"]
        from pathlib import Path

        assert not Path(note_path).exists()

    def test_export_round_trips_losslessly(self, tmp_path):
        from lawnops.log_migrate import export_log

        config = self._seeded_sqlite_config(tmp_path)
        sqlite_rows = {
            entity: log_store._backend({"database": config["database"]}).read_table(entity)
            for entity in ("treatments", "products", "mowing_visits")
        }

        export_log(config, preview=False)

        for entity, expected_rows in sqlite_rows.items():
            markdown_rows = log_store.read_table(config, entity)
            assert len(markdown_rows) == len(expected_rows)
            for expected, actual in zip(expected_rows, markdown_rows):
                for key in expected:
                    if key == "id":
                        continue
                    assert actual[key] == expected[key], f"{entity}.{key}: {actual[key]!r} != {expected[key]!r}"

    def test_export_is_idempotent(self, tmp_path):
        from pathlib import Path

        from lawnops.log_migrate import export_log

        config = self._seeded_sqlite_config(tmp_path)
        export_log(config, preview=False)
        first = Path(config["lawn_log"]["markdown"]["note"]).read_text()
        export_log(config, preview=False)
        second = Path(config["lawn_log"]["markdown"]["note"]).read_text()
        assert first == second


# ── Malformed markdown fails loud ──


class TestMalformedMarkdownRaises:
    def test_wrong_column_count_raises(self, tmp_path):
        note = tmp_path / "log.md"
        note.write_text(
            "## Treatments\n"
            "| date | area | product | method | amount | soil_temp_f | cost | notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 2026-03-01 | front | Prodiamine |\n"
        )
        config = {"lawn_log": {"backend": "markdown", "markdown": {"note": str(note)}}}
        with pytest.raises(RuntimeError, match="Malformed row"):
            log_store.read_table(config, "treatments")

    def test_wrong_header_raises(self, tmp_path):
        note = tmp_path / "log.md"
        note.write_text(
            "## Treatments\n| when | where | what |\n| --- | --- | --- |\n| 2026-03-01 | front | Prodiamine |\n"
        )
        config = {"lawn_log": {"backend": "markdown", "markdown": {"note": str(note)}}}
        with pytest.raises(RuntimeError, match="Column mismatch"):
            log_store.read_table(config, "treatments")


# ── Code review fix: MAJOR 1 -- schema defaults parity across backends ──


@pytest.mark.parametrize("backend", ["sqlite", "markdown"])
class TestSchemaDefaultsParity:
    def _config(self, backend, tmp_path):
        db = tmp_path / "lawnops.db"
        config = {"database": {"path": str(db)}}
        if backend == "markdown":
            config["lawn_log"] = {"backend": "markdown", "markdown": {"note": str(tmp_path / "log.md")}}
        return config

    def test_equipment_add_defaults_status_active_on_both_backends(self, backend, tmp_path, monkeypatch):
        import lawnops.mcp_server as ms

        config = self._config(backend, tmp_path)
        monkeypatch.setattr(ms, "_config", lambda: config)

        ms.equipment_add(name="Mower", cost=300)
        listed = json.loads(ms.equipment_list())
        assert listed["equipment"][0]["status"] == "active"

    def test_purchase_add_defaults_qty_on_both_backends(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        row = log_store.append_row(config, "purchases", {"date": "2026-01-01", "item": "Fert", "cost": 30})
        assert row["qty"] == 1

    def test_product_add_defaults_qty_and_unit_on_both_backends(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        row = log_store.append_row(config, "products", {"name": "Widget"})
        assert row["qty_on_hand"] == 0
        assert row["unit"] == "bag"


# ── Code review fix: MAJOR 2 -- unreachable parent dir fails loud ──


class TestUnreachableParentDirRaises:
    def test_missing_parent_dir_raises_and_creates_nothing(self, tmp_path):
        note = tmp_path / "does_not_exist" / "sub" / "Lawn Log.md"
        config = {"lawn_log": {"backend": "markdown", "markdown": {"note": str(note)}}}

        with pytest.raises(RuntimeError, match="parent directory does not exist"):
            log_store.append_row(config, "treatments", {"date": "2026-01-01", "area": "x", "product": "y"})

        assert not note.parent.exists()
        assert not note.exists()

    def test_existing_parent_dir_still_creates_the_note_file(self, tmp_path):
        note = tmp_path / "Lawn Log.md"
        config = {"lawn_log": {"backend": "markdown", "markdown": {"note": str(note)}}}

        log_store.append_row(config, "treatments", {"date": "2026-01-01", "area": "x", "product": "y"})

        assert note.exists()


# ── Code review fix: MAJOR 3 -- pipe/newline escaping ──


class TestPipeAndNewlineEscaping:
    def test_pipe_and_newline_in_notes_round_trip_without_wedging_reads(self, tmp_path):
        note = tmp_path / "log.md"
        config = {"lawn_log": {"backend": "markdown", "markdown": {"note": str(note)}}}
        raw_notes = "spot spray | retreat in 2wk\nthen mow"

        log_store.append_row(
            config, "treatments", {"date": "2026-03-01", "area": "front", "product": "X", "notes": raw_notes}
        )

        rows = log_store.read_table(config, "treatments")
        assert rows[0]["notes"] == "spot spray | retreat in 2wk then mow"

        # A second, independent read must not raise "Malformed row" -- the
        # persisted "\|" must not be mistaken for a real column boundary.
        rows_again = log_store.read_table(config, "treatments")
        assert len(rows_again) == 1

        # And the store must still accept further writes to the same entity.
        log_store.append_row(config, "treatments", {"date": "2026-04-01", "area": "back", "product": "Y"})
        assert len(log_store.read_table(config, "treatments")) == 2


# ── Code review fix: MAJOR 4 -- reorder_alerts excludes NULL qty_on_hand ──


class TestReorderAlertsExcludesNullQty:
    def test_none_qty_on_hand_produces_no_alert_on_both_backends(self):
        products = [
            {
                "id": 1,
                "name": "Widget",
                "category": "herbicide",
                "qty_on_hand": None,
                "unit": "bag",
                "last_ordered": None,
                "cost_each": 10,
                "source": None,
            }
        ]
        alerts = log_compute.reorder_alerts(products, [])
        assert alerts == []

    def test_zero_qty_on_hand_still_produces_an_alert(self):
        products = [
            {
                "id": 1,
                "name": "Widget",
                "category": "herbicide",
                "qty_on_hand": 0,
                "unit": "bag",
                "last_ordered": None,
                "cost_each": 10,
                "source": None,
            }
        ]
        alerts = log_compute.reorder_alerts(products, [])
        assert len(alerts) == 1
        assert alerts[0]["name"] == "Widget"

    def test_negative_qty_on_hand_produces_an_alert(self):
        products = [
            {
                "id": 1,
                "name": "Widget",
                "category": "herbicide",
                "qty_on_hand": -2,
                "unit": "bag",
                "last_ordered": None,
                "cost_each": 10,
                "source": None,
            }
        ]
        alerts = log_compute.reorder_alerts(products, [])
        assert len(alerts) == 1
        assert alerts[0]["name"] == "Widget"


# ── Code review fix: MINOR 5 -- spend_report all-null-cost category total ──


class TestSpendReportAllNullCostCategory:
    def test_category_total_is_none_when_every_cost_is_null(self):
        purchases = [
            {"id": 1, "date": "2026-01-01", "item": "A", "category": "product", "cost": None},
            {"id": 2, "date": "2026-01-02", "item": "B", "category": "product", "cost": None},
            {"id": 3, "date": "2026-01-03", "item": "C", "category": "equipment", "cost": 50},
            {"id": 4, "date": "2026-01-04", "item": "D", "category": "chemical", "cost": 100},
        ]
        cat_rows, grand_total, _, _ = log_compute.spend_report(purchases, 2026)

        by_category = {c["category"]: c["total"] for c in cat_rows}
        assert by_category["product"] is None
        assert by_category["equipment"] == 50.0
        assert by_category["chemical"] == 100.0
        # Grand total sums only non-null costs.
        assert grand_total == 150.0
        # Ordering mirrors SQLite ORDER BY total DESC with NULLS last:
        # real totals descending, the all-null category last.
        assert [c["category"] for c in cat_rows] == ["chemical", "equipment", "product"]
        assert cat_rows[-1]["total"] is None
