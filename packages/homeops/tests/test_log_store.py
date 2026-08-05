"""Tests for the home_log backend abstraction (issue #58).

Covers: log_store dispatch, the sqlite/markdown backends, log_compute's
shared report logic, and the rewired MCP tools (via direct function calls
with a monkeypatched homeops.mcp_server._config, matching the pattern used
for lawnops' equivalent #57 suite).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from homeops import log_compute, log_store
from homeops.db.schema import SCHEMA_SQL


@pytest.fixture
def sqlite_config(tmp_path):
    """Zero-config home_log -- no `home_log` key at all, matching a real install."""
    db = tmp_path / "homeops.db"
    return {"database": {"path": str(db)}}


@pytest.fixture
def markdown_config(tmp_path):
    note = tmp_path / "Home Log.md"
    db = tmp_path / "homeops.db"  # must never be created under this config
    return {
        "database": {"path": str(db)},
        "home_log": {"backend": "markdown", "markdown": {"note": str(note)}},
    }


# ── log_store dispatch ──


class TestBackendDispatch:
    def test_default_backend_is_sqlite(self, sqlite_config):
        from homeops.log_backends.sqlite_backend import SqliteBackend

        assert isinstance(log_store._backend(sqlite_config), SqliteBackend)

    def test_explicit_markdown_backend(self, markdown_config):
        from homeops.log_backends.markdown_backend import MarkdownBackend

        assert isinstance(log_store._backend(markdown_config), MarkdownBackend)

    def test_unknown_backend_raises(self, sqlite_config):
        sqlite_config["home_log"] = {"backend": "sheets"}
        with pytest.raises(RuntimeError, match="Unknown home_log.backend"):
            log_store._backend(sqlite_config)


# ── Default install: sqlite, zero config, unchanged behavior ──


class TestSqliteDefaultUnchanged:
    def test_task_add_and_list_round_trip(self, sqlite_config):
        log_store.append_row(sqlite_config, "tasks", {"name": "HVAC Filter", "category": "hvac", "interval_days": 90})
        rows = log_store.read_table(sqlite_config, "tasks")
        tasks = log_compute.list_tasks(rows)
        assert len(tasks) == 1
        assert tasks[0]["name"] == "HVAC Filter"
        assert tasks[0]["active"] == 1
        assert tasks[0]["next_due"] is None
        assert tasks[0]["overdue"] is False

    def test_task_done_updates_row_and_appends_log(self, sqlite_config, monkeypatch):
        log_store.append_row(
            sqlite_config, "tasks", {"name": "Gutter Cleaning", "category": "gutters", "interval_days": 180}
        )

        import homeops.mcp_server as ms

        monkeypatch.setattr(ms, "_config", lambda: sqlite_config)
        result = json.loads(ms.task_done(name="Gutter Cleaning", date="2026-03-01", cost=150, provider="Acme"))

        assert result["name"] == "Gutter Cleaning"
        assert result["next_due"] == "2026-08-28"

        tasks = log_store.read_table(sqlite_config, "tasks")
        assert tasks[0]["last_done"] == "2026-03-01"
        assert tasks[0]["next_due"] == "2026-08-28"

        log_rows = log_store.read_table(sqlite_config, "task_log")
        assert len(log_rows) == 1
        assert log_rows[0]["task"] == "Gutter Cleaning"
        assert log_rows[0]["cost"] == 150

        costs = log_store.read_table(sqlite_config, "costs")
        assert len(costs) == 1
        assert costs[0]["source"] == "task_log"
        assert costs[0]["amount"] == 150

    def test_delete_pest_treatment_by_id(self, sqlite_config):
        log_store.append_row(
            sqlite_config, "pest_treatments", {"date": "2026-03-01", "area": "perimeter", "product": "Cyzmic CS"}
        )
        rows = log_store.read_table(sqlite_config, "pest_treatments")
        deleted = log_store.delete_row(sqlite_config, "pest_treatments", {"id": rows[0]["id"]})
        assert deleted == 1
        assert log_store.read_table(sqlite_config, "pest_treatments") == []


# ── MCP tool wiring (both backends) ──


class TestMcpToolsSqliteBackend:
    def test_task_add_then_list(self, sqlite_config, monkeypatch):
        import homeops.mcp_server as ms

        monkeypatch.setattr(ms, "_config", lambda: sqlite_config)

        added = json.loads(ms.task_add(name="HVAC Filter", category="hvac", interval="90d"))
        assert added["interval_days"] == 90

        listed = json.loads(ms.task_list())
        assert listed["count"] == 1
        assert listed["tasks"][0]["name"] == "HVAC Filter"


class TestMcpToolsMarkdownBackend:
    def test_task_add_writes_markdown_and_list_returns_it(self, markdown_config, monkeypatch):
        import homeops.mcp_server as ms

        monkeypatch.setattr(ms, "_config", lambda: markdown_config)

        added = json.loads(ms.task_add(name="HVAC Filter", category="hvac", interval="90d"))
        assert added == {"name": "HVAC Filter", "category": "hvac", "interval_days": 90}

        note_path = markdown_config["home_log"]["markdown"]["note"]
        content = Path(note_path).read_text()
        assert "## Tasks" in content
        assert "HVAC Filter" in content

        listed = json.loads(ms.task_list())
        assert listed["count"] == 1
        assert listed["tasks"][0]["name"] == "HVAC Filter"
        assert listed["tasks"][0]["active"] == 1

    def test_task_done_updates_row_and_appends_log_under_markdown(self, markdown_config, monkeypatch):
        import homeops.mcp_server as ms

        monkeypatch.setattr(ms, "_config", lambda: markdown_config)

        ms.task_add(name="Gutter Cleaning", category="gutters", interval="180d")
        result = json.loads(ms.task_done(name="Gutter Cleaning", date="2026-03-01", cost=150, provider="Acme"))
        assert result["next_due"] == "2026-08-28"

        tasks = json.loads(ms.task_list())["tasks"]
        assert tasks[0]["last_done"] == "2026-03-01"
        assert tasks[0]["next_due"] == "2026-08-28"

        history = json.loads(ms.task_history(task_name="Gutter Cleaning"))
        assert history["count"] == 1
        assert history["history"][0]["task_name"] == "Gutter Cleaning"
        assert history["history"][0]["cost"] == 150

        note_path = markdown_config["home_log"]["markdown"]["note"]
        content = Path(note_path).read_text()
        assert "## Task Log" in content

    def test_no_write_tool_touches_sqlite(self, markdown_config, monkeypatch):
        import homeops.mcp_server as ms

        monkeypatch.setattr(ms, "_config", lambda: markdown_config)

        ms.task_add(name="Gutter Cleaning", category="gutters", interval="180d")
        ms.task_done(name="Gutter Cleaning", date="2026-03-01", cost=50)
        ms.pest_add(date="2026-03-01", area="perimeter", product="Cyzmic CS", cost=40)
        ms.cost_add(date="2026-03-01", category="hvac", amount=277, description="Filter")
        ms.utility_add(date="2026-03", utility_type="electric", amount=285.50)

        db_path = Path(markdown_config["database"]["path"])
        assert not db_path.exists()

    def test_missing_path_raises_and_never_falls_back_to_sqlite(self, tmp_path, monkeypatch):
        import homeops.mcp_server as ms

        db_path = tmp_path / "homeops.db"
        config = {"database": {"path": str(db_path)}, "home_log": {"backend": "markdown", "markdown": {}}}
        monkeypatch.setattr(ms, "_config", lambda: config)

        with pytest.raises(RuntimeError):
            log_store.append_row(config, "tasks", {"name": "X", "category": "hvac", "interval_days": 30})

        result = json.loads(ms.task_add(name="X", category="hvac", interval="30d"))
        assert "error" in result
        assert not db_path.exists()

    def test_per_entity_routing_and_custom_heading(self, tmp_path, monkeypatch):
        import homeops.mcp_server as ms

        main_note = tmp_path / "main.md"
        appliance_note = tmp_path / "appliances.md"
        config = {
            "database": {"path": str(tmp_path / "homeops.db")},
            "home_log": {
                "backend": "markdown",
                "markdown": {
                    "note": str(main_note),
                    "entities": {"appliances": str(appliance_note)},
                    "section_headings": {"pest_treatments": "Pest Log"},
                },
            },
        }
        monkeypatch.setattr(ms, "_config", lambda: config)

        log_store.append_row(config, "appliances", {"name": "Water Heater", "category": "plumbing"})
        ms.task_add(name="Gutter Cleaning", category="gutters", interval="180d")
        ms.pest_add(date="2026-01-02", area="perimeter", product="X")

        assert appliance_note.exists()
        assert "Water Heater" in appliance_note.read_text()
        assert "## Tasks" not in appliance_note.read_text()

        main_content = main_note.read_text()
        assert "## Tasks" in main_content
        assert "## Pest Log" in main_content
        assert "## Pest\n" not in main_content
        assert "Water Heater" not in main_content


# ── Parametrized: identical report totals across both backends ──


@pytest.mark.parametrize("backend", ["sqlite", "markdown"])
class TestReportParityAcrossBackends:
    def _config(self, backend, tmp_path):
        db = tmp_path / "homeops.db"
        config = {"database": {"path": str(db)}}
        if backend == "markdown":
            note = tmp_path / "log.md"
            config["home_log"] = {"backend": "markdown", "markdown": {"note": str(note)}}
        return config

    def _seed(self, config):
        log_store.append_row(
            config,
            "appliances",
            {
                "name": "Water Heater",
                "category": "plumbing",
                "purchase_date": "2020-01-01",
                "expected_lifespan_years": 12,
                "replacement_cost": 2400,
            },
        )
        log_store.append_row(
            config, "tasks", {"name": "HVAC Filter", "category": "hvac", "interval_days": 90, "next_due": "2020-01-01"}
        )
        log_store.append_row(
            config, "costs", {"date": "2026-03-01", "category": "hvac", "amount": 277, "description": "Filter"}
        )
        log_store.append_row(
            config, "costs", {"date": "2026-03-10", "category": "pest", "amount": 45, "description": "Spray"}
        )
        log_store.append_row(config, "utility_bills", {"bill_date": "2026-03", "type": "electric", "amount": 285.50})
        log_store.append_row(config, "utility_bills", {"bill_date": "2026-02", "type": "electric", "amount": 250.00})

    def test_sinking_fund_plan_total(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        self._seed(config)
        appliances = log_compute.list_appliances(log_store.read_table(config, "appliances"))
        plan = log_compute.appliance_sinking_fund_plan(appliances)
        assert plan["count"] == 1
        assert plan["plans"][0]["name"] == "Water Heater"

    def test_cost_summary_total(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        self._seed(config)
        rows = log_store.read_table(config, "costs")
        summary = log_compute.get_cost_summary(rows, 2026)
        by_category = {s["category"]: s["total"] for s in summary}
        assert by_category["hvac"] == 277
        assert by_category["pest"] == 45

    def test_utility_trend(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        self._seed(config)
        rows = log_store.read_table(config, "utility_bills")
        trend = log_compute.get_utility_trend(rows, "electric", months=12)
        assert [b["bill_date"] for b in trend] == ["2026-02", "2026-03"]
        assert trend[-1]["amount"] == 285.50

    def test_task_overdue(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        self._seed(config)
        rows = log_store.read_table(config, "tasks")
        overdue = log_compute.get_overdue(rows)
        assert len(overdue) == 1
        assert overdue[0]["name"] == "HVAC Filter"


# ── log-export migration ──


class TestLogExport:
    def _seeded_sqlite_config(self, tmp_path):
        db = tmp_path / "homeops.db"
        conn = sqlite3.connect(db)
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO tasks (name, category, interval_days, last_done, next_due, active, notes) "
            "VALUES ('Gutter Cleaning', 'gutters', 180, '2026-03-01', '2026-08-28', 1, NULL)"
        )
        conn.execute(
            "INSERT INTO task_log (task_id, date, cost, provider, notes) "
            "VALUES ((SELECT id FROM tasks WHERE name = 'Gutter Cleaning'), '2026-03-01', 150.0, 'Acme', NULL)"
        )
        conn.execute(
            "INSERT INTO pest_treatments (date, area, product, method, notes, cost) "
            "VALUES ('2026-03-01', 'perimeter', 'Cyzmic CS', 'spray', NULL, 40.0)"
        )
        conn.commit()
        conn.close()
        note = tmp_path / "log.md"
        return {
            "database": {"path": str(db)},
            "home_log": {"backend": "markdown", "markdown": {"note": str(note)}},
        }

    def test_preview_writes_nothing(self, tmp_path):
        from homeops.log_migrate import export_log

        config = self._seeded_sqlite_config(tmp_path)
        counts = export_log(config, preview=True)
        assert counts["tasks"] == 1
        assert counts["task_log"] == 1
        assert counts["pest_treatments"] == 1
        note_path = config["home_log"]["markdown"]["note"]
        assert not Path(note_path).exists()

    def test_export_round_trips_losslessly_task_log_rejoined_by_name(self, tmp_path):
        from homeops.log_migrate import export_log
        from homeops.log_schema import ENTITIES

        config = self._seeded_sqlite_config(tmp_path)
        sqlite_rows = {
            entity: log_store._backend({"database": config["database"]}).read_table(entity)
            for entity in ("tasks", "task_log", "pest_treatments")
        }

        export_log(config, preview=False)

        for entity, expected_rows in sqlite_rows.items():
            # Passthrough metadata (created_at, task_id, source_id) is
            # sqlite-only and deliberately not stored in the markdown table
            # (log_schema.EntitySchema.passthrough), so it is excluded here:
            # the export is lossless for the entity's real data columns, not
            # for sqlite implementation metadata that has no markdown home.
            skip = {"id", *ENTITIES[entity].passthrough}
            markdown_rows = log_store.read_table(config, entity)
            assert len(markdown_rows) == len(expected_rows)
            for expected, actual in zip(expected_rows, markdown_rows):
                for key in expected:
                    if key in skip:
                        continue
                    assert actual[key] == expected[key], f"{entity}.{key}: {actual[key]!r} != {expected[key]!r}"

        # task_log's "task" column must carry the task's name, not sqlite's id.
        log_rows = log_store.read_table(config, "task_log")
        assert log_rows[0]["task"] == "Gutter Cleaning"

    def test_export_is_idempotent(self, tmp_path):
        from homeops.log_migrate import export_log

        config = self._seeded_sqlite_config(tmp_path)
        export_log(config, preview=False)
        first = Path(config["home_log"]["markdown"]["note"]).read_text()
        export_log(config, preview=False)
        second = Path(config["home_log"]["markdown"]["note"]).read_text()
        assert first == second


# ── Malformed markdown fails loud ──


class TestMalformedMarkdownRaises:
    def test_wrong_column_count_raises(self, tmp_path):
        note = tmp_path / "log.md"
        note.write_text(
            "## Tasks\n"
            "| name | category | interval_days | last_done | next_due | active | notes |\n"
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| HVAC Filter | hvac | 90 |\n"
        )
        config = {"home_log": {"backend": "markdown", "markdown": {"note": str(note)}}}
        with pytest.raises(RuntimeError, match="Malformed row"):
            log_store.read_table(config, "tasks")

    def test_wrong_header_raises(self, tmp_path):
        note = tmp_path / "log.md"
        note.write_text("## Tasks\n| when | where | what |\n| --- | --- | --- |\n| x | y | z |\n")
        config = {"home_log": {"backend": "markdown", "markdown": {"note": str(note)}}}
        with pytest.raises(RuntimeError, match="Column mismatch"):
            log_store.read_table(config, "tasks")


# ── Schema defaults parity ──


@pytest.mark.parametrize("backend", ["sqlite", "markdown"])
class TestSchemaDefaultsParity:
    def _config(self, backend, tmp_path):
        db = tmp_path / "homeops.db"
        config = {"database": {"path": str(db)}}
        if backend == "markdown":
            config["home_log"] = {"backend": "markdown", "markdown": {"note": str(tmp_path / "log.md")}}
        return config

    def test_task_active_defaults_to_1_on_both_backends(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        row = log_store.append_row(config, "tasks", {"name": "X", "category": "hvac", "interval_days": 30})
        assert row["active"] == 1

    def test_cost_source_defaults_to_manual_on_both_backends(self, backend, tmp_path):
        config = self._config(backend, tmp_path)
        row = log_store.append_row(
            config, "costs", {"date": "2026-01-01", "category": "hvac", "amount": 100, "description": "x"}
        )
        assert row["source"] == "manual"


# ── Unreachable parent dir fails loud ──


class TestUnreachableParentDirRaises:
    def test_missing_parent_dir_raises_and_creates_nothing(self, tmp_path):
        note = tmp_path / "does_not_exist" / "sub" / "Home Log.md"
        config = {"home_log": {"backend": "markdown", "markdown": {"note": str(note)}}}

        with pytest.raises(RuntimeError, match="parent directory does not exist"):
            log_store.append_row(config, "tasks", {"name": "X", "category": "hvac", "interval_days": 30})

        assert not note.parent.exists()
        assert not note.exists()

    def test_existing_parent_dir_still_creates_the_note_file(self, tmp_path):
        note = tmp_path / "Home Log.md"
        config = {"home_log": {"backend": "markdown", "markdown": {"note": str(note)}}}

        log_store.append_row(config, "tasks", {"name": "X", "category": "hvac", "interval_days": 30})

        assert note.exists()


# ── Pipe/newline escaping ──


class TestPipeAndNewlineEscaping:
    def test_pipe_and_newline_in_notes_round_trip_without_wedging_reads(self, tmp_path):
        note = tmp_path / "log.md"
        config = {"home_log": {"backend": "markdown", "markdown": {"note": str(note)}}}
        raw_notes = "spot spray | retreat in 2wk\nthen recheck"

        log_store.append_row(
            config, "pest_treatments", {"date": "2026-03-01", "area": "perimeter", "product": "X", "notes": raw_notes}
        )

        rows = log_store.read_table(config, "pest_treatments")
        assert rows[0]["notes"] == "spot spray | retreat in 2wk then recheck"

        rows_again = log_store.read_table(config, "pest_treatments")
        assert len(rows_again) == 1

        log_store.append_row(config, "pest_treatments", {"date": "2026-04-01", "area": "interior", "product": "Y"})
        assert len(log_store.read_table(config, "pest_treatments")) == 2


# ── NULL-safe aggregation (mirrors SQL SUM/AVG ignoring NULL) ──


class TestCostSummaryAllNullAmountCategory:
    def test_category_total_is_none_when_every_amount_is_null(self):
        costs = [
            {"id": 1, "date": "2026-01-01", "category": "hvac", "amount": None},
            {"id": 2, "date": "2026-01-02", "category": "hvac", "amount": None},
            {"id": 3, "date": "2026-01-03", "category": "pest", "amount": 50},
        ]
        summary = log_compute.get_cost_summary(costs, "2026")

        by_category = {s["category"]: s["total"] for s in summary}
        assert by_category["hvac"] is None
        assert by_category["pest"] == 50
        # NULLS-last ordering: real totals descending, all-null category last.
        assert [s["category"] for s in summary] == ["pest", "hvac"]


# ── Pre-#58 JSON shape parity (code review CRITICAL/MAJOR follow-up) ──
#
# The default sqlite backend's read-tool output must be byte-identical (key
# set and values) to what the pre-#58 direct-SQL db/*.py functions returned.
# created_at (every entity), costs.source_id, and task_log/task_history's
# task_id are sqlite-populated passthrough metadata (log_schema.
# EntitySchema.passthrough): present with a real value on sqlite, present
# with value None on markdown, never stored in the markdown table itself.


class TestPreIssue58KeySetParity:
    TASK_KEYS = {
        "id",
        "name",
        "category",
        "interval_days",
        "last_done",
        "next_due",
        "notes",
        "active",
        "created_at",
        "days_until",
        "overdue",
    }
    TASK_HISTORY_KEYS = {"id", "task_id", "date", "cost", "provider", "notes", "created_at", "task_name"}
    PEST_KEYS = {"id", "date", "area", "product", "method", "cost", "notes", "created_at"}
    PROVIDER_KEYS = {"id", "name", "category", "phone", "email", "typical_cost", "active", "notes", "created_at"}
    APPLIANCE_KEYS = {
        "id",
        "name",
        "category",
        "brand",
        "model",
        "purchase_date",
        "warranty_end",
        "expected_lifespan_years",
        "replacement_cost",
        "location",
        "notes",
        "created_at",
        "age_years",
        "warranty_active",
        "warranty_days_left",
        "remaining_lifespan_years",
    }
    UTILITY_BILL_KEYS = {"id", "bill_date", "type", "amount", "usage", "notes", "created_at"}
    COST_KEYS = {
        "id",
        "date",
        "category",
        "amount",
        "provider",
        "description",
        "notes",
        "source",
        "source_id",
        "created_at",
    }

    def _sqlite_config(self, db_dir):
        return {"database": {"path": str(db_dir / "homeops.db")}}

    def _markdown_config(self, md_dir):
        return {
            "database": {"path": str(md_dir / "homeops.db")},
            "home_log": {"backend": "markdown", "markdown": {"note": str(md_dir / "log.md")}},
        }

    def _seed(self, config):
        log_store.append_row(
            config,
            "tasks",
            {"name": "HVAC Filter", "category": "hvac", "interval_days": 90, "next_due": "2020-01-01"},
        )
        log_store.append_row(
            config, "task_log", {"task": "HVAC Filter", "date": "2026-03-01", "cost": 25, "provider": "Acme"}
        )
        log_store.append_row(
            config, "pest_treatments", {"date": "2026-03-01", "area": "perimeter", "product": "Cyzmic CS"}
        )
        log_store.append_row(config, "providers", {"name": "Acme HVAC", "category": "hvac", "typical_cost": 100})
        log_store.append_row(config, "appliances", {"name": "Water Heater", "category": "plumbing"})
        log_store.append_row(config, "utility_bills", {"bill_date": "2026-03", "type": "electric", "amount": 285.50})
        log_store.append_row(
            config,
            "costs",
            {"date": "2026-03-01", "category": "hvac", "amount": 100, "description": "x", "provider": "Acme HVAC"},
        )

    def test_sqlite_read_tool_key_sets_match_pre_58_shape(self, tmp_path, monkeypatch):
        import homeops.mcp_server as ms

        config = self._sqlite_config(tmp_path)
        self._seed(config)
        monkeypatch.setattr(ms, "_config", lambda: config)

        tasks = json.loads(ms.task_list())["tasks"]
        assert set(tasks[0]) == self.TASK_KEYS
        assert tasks[0]["created_at"] is not None

        overdue = json.loads(ms.task_overdue())["tasks"]
        assert len(overdue) == 1
        assert set(overdue[0]) == self.TASK_KEYS
        assert overdue[0]["created_at"] is not None

        history = json.loads(ms.task_history(task_name="HVAC Filter"))["history"]
        assert set(history[0]) == self.TASK_HISTORY_KEYS
        assert history[0]["task_id"] is not None
        assert history[0]["created_at"] is not None

        pest = json.loads(ms.pest_history())["treatments"]
        assert set(pest[0]) == self.PEST_KEYS
        assert pest[0]["created_at"] is not None

        providers = json.loads(ms.provider_list())["providers"]
        assert set(providers[0]) == self.PROVIDER_KEYS
        assert providers[0]["created_at"] is not None

        detail = json.loads(ms.provider_detail(name="Acme HVAC"))
        assert set(detail) - {"cost_history"} == self.PROVIDER_KEYS
        assert detail["created_at"] is not None

        appliances = json.loads(ms.appliance_list())["appliances"]
        assert set(appliances[0]) == self.APPLIANCE_KEYS
        assert appliances[0]["created_at"] is not None

        bills = json.loads(ms.utility_trend(utility_type="electric"))["bills"]
        assert set(bills[0]) == self.UTILITY_BILL_KEYS
        assert bills[0]["created_at"] is not None

        costs = json.loads(ms.cost_history())["costs"]
        assert set(costs[0]) == self.COST_KEYS
        assert costs[0]["created_at"] is not None
        assert "source_id" in costs[0]

    def test_cross_backend_key_sets_match_with_passthrough_null_on_markdown(self, tmp_path_factory, monkeypatch):
        import homeops.mcp_server as ms

        sqlite_config = self._sqlite_config(tmp_path_factory.mktemp("sqlite"))
        self._seed(sqlite_config)
        monkeypatch.setattr(ms, "_config", lambda: sqlite_config)
        sqlite_tasks = json.loads(ms.task_list())["tasks"]
        sqlite_costs = json.loads(ms.cost_history())["costs"]
        sqlite_history = json.loads(ms.task_history(task_name="HVAC Filter"))["history"]

        markdown_config = self._markdown_config(tmp_path_factory.mktemp("markdown"))
        self._seed(markdown_config)
        monkeypatch.setattr(ms, "_config", lambda: markdown_config)
        markdown_tasks = json.loads(ms.task_list())["tasks"]
        markdown_costs = json.loads(ms.cost_history())["costs"]
        markdown_history = json.loads(ms.task_history(task_name="HVAC Filter"))["history"]

        assert set(sqlite_tasks[0]) == set(markdown_tasks[0])
        assert sqlite_tasks[0]["created_at"] is not None
        assert markdown_tasks[0]["created_at"] is None

        assert set(sqlite_costs[0]) == set(markdown_costs[0])
        assert sqlite_costs[0]["created_at"] is not None
        assert markdown_costs[0]["created_at"] is None
        assert markdown_costs[0]["source_id"] is None

        assert set(sqlite_history[0]) == set(markdown_history[0])
        assert sqlite_history[0]["task_id"] is not None
        assert markdown_history[0]["task_id"] is None
        assert sqlite_history[0]["created_at"] is not None
        assert markdown_history[0]["created_at"] is None


class TestTaskDoneCostsSourceId:
    """Code review MAJOR: task_done's dual-written costs row must set
    source_id to the newly-appended task_log row's id on sqlite (mirrors
    pre-#58 mark_done's `last_insert_rowid()`); None on markdown, which has
    no stable row id to give back."""

    def test_sqlite_costs_row_source_id_matches_new_task_log_id(self, tmp_path, monkeypatch):
        import homeops.mcp_server as ms

        config = {"database": {"path": str(tmp_path / "homeops.db")}}
        log_store.append_row(config, "tasks", {"name": "Gutter Cleaning", "category": "gutters", "interval_days": 180})
        monkeypatch.setattr(ms, "_config", lambda: config)

        json.loads(ms.task_done(name="Gutter Cleaning", date="2026-03-01", cost=150, provider="Acme"))

        log_rows = log_store.read_table(config, "task_log")
        assert len(log_rows) == 1
        task_log_id = log_rows[0]["id"]

        costs = log_store.read_table(config, "costs")
        assert len(costs) == 1
        assert costs[0]["source"] == "task_log"
        assert costs[0]["source_id"] is not None
        assert costs[0]["source_id"] == task_log_id

    def test_markdown_costs_row_source_id_is_none(self, tmp_path, monkeypatch):
        import homeops.mcp_server as ms

        config = {
            "database": {"path": str(tmp_path / "homeops.db")},
            "home_log": {"backend": "markdown", "markdown": {"note": str(tmp_path / "log.md")}},
        }
        monkeypatch.setattr(ms, "_config", lambda: config)

        ms.task_add(name="Gutter Cleaning", category="gutters", interval="180d")
        json.loads(ms.task_done(name="Gutter Cleaning", date="2026-03-01", cost=150, provider="Acme"))

        costs = log_store.read_table(config, "costs")
        assert len(costs) == 1
        assert costs[0]["source"] == "task_log"
        assert costs[0]["source_id"] is None


# ── HVAC and ynab_bridge ──


class TestHvacUntouched:
    def test_hvac_db_module_unaffected(self, sqlite_config):
        from homeops.db.hvac import get_hvac_trend

        assert get_hvac_trend(sqlite_config, None, 14) == []


class TestYnabBridgeRoutedThroughLogStore:
    """ynab_bridge.py IS rewired through log_store/log_compute (issue #58) so
    sinking_fund_plan/budget_overview compute equal results under both
    home_log backends -- see the module docstring for why this deviates
    from a literal "no diff to ynab_bridge.py" reading of the issue-58 spec
    acceptance list (which was inherited from lawnops #57's template and
    does not fit homeops' status.py/ynab_bridge.py composition modules)."""

    @pytest.mark.parametrize("backend", ["sqlite", "markdown"])
    def test_sinking_fund_plan_equal_across_backends(self, backend, tmp_path):
        from homeops.ynab_bridge import appliance_sinking_fund_plan

        db = tmp_path / "homeops.db"
        config = {"database": {"path": str(db)}}
        if backend == "markdown":
            config["home_log"] = {"backend": "markdown", "markdown": {"note": str(tmp_path / "log.md")}}

        log_store.append_row(
            config,
            "appliances",
            {
                "name": "Water Heater",
                "category": "plumbing",
                "purchase_date": "2020-01-01",
                "expected_lifespan_years": 12,
                "replacement_cost": 2400,
            },
        )
        plan = appliance_sinking_fund_plan(config)
        assert plan["count"] == 1
        assert plan["plans"][0]["name"] == "Water Heater"
