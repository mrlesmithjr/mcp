"""Admin API: read/write ynab-tools config.json and provide validation data."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ynab_tools.client import YNABClient
from ynab_tools.config import CONFIG_FILE, apply_config, atomic_write_json
from ynab_tools.db import get_connection

router = APIRouter()


@router.get("/admin/config")
def get_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {"config": {}, "path": str(CONFIG_FILE), "exists": False}
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    if config.get("access_token"):
        token = config["access_token"]
        config = dict(config)
        config["access_token"] = "****" + token[-4:] if len(token) > 4 else "****"
    return {"config": config, "path": str(CONFIG_FILE), "exists": True}


class ConfigPayload(BaseModel):
    config: dict[str, Any]


@router.put("/admin/config")
def put_config(payload: ConfigPayload) -> dict[str, Any]:
    config = dict(payload.config)
    token = config.get("access_token", "")
    if isinstance(token, str) and token.startswith("****"):
        # Masked value from GET - restore the real token from disk
        existing: dict = {}
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE) as f:
                existing = json.load(f)
        config["access_token"] = existing.get("access_token", "")
    elif isinstance(token, str) and token.strip() == "":
        # Explicitly blanked - reject rather than silently overwrite credentials
        raise HTTPException(status_code=400, detail="access_token cannot be set to an empty string.")
    atomic_write_json(CONFIG_FILE, config, dir_mode=0o700, file_mode=0o600)
    apply_config(config)
    return {"ok": True, "path": str(CONFIG_FILE)}


class BudgetsPayload(BaseModel):
    access_token: str | None = None


@router.post("/admin/budgets")
def list_budgets(payload: BudgetsPayload) -> dict[str, Any]:
    token = (payload.access_token or "").strip() or os.environ.get("YNAB_ACCESS_TOKEN", "")
    if not token:
        raise HTTPException(status_code=401, detail="No access token provided")
    try:
        client = YNABClient(token, "")
        plans = client.get_plans()
        return {"budgets": [{"id": p["id"], "name": p["name"]} for p in plans]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


class DeriveIncomePayload(BaseModel):
    paycheck_payees: str = ""


@router.post("/admin/derive-income")
def derive_income(payload: DeriveIncomePayload) -> dict[str, Any]:
    payees = [p.strip() for p in payload.paycheck_payees.split(",") if p.strip()]
    if not payees:
        raise HTTPException(status_code=400, detail="No paycheck payees configured")
    from ynab_tools.reports.paycheck import _detect_income_sources, _get_recurring_inflows

    conn = get_connection()
    try:
        inflows = _get_recurring_inflows(conn, months=24)
    finally:
        conn.close()
    sources = _detect_income_sources(inflows, payees)
    if not sources:
        raise HTTPException(status_code=404, detail="No recurring transactions found for configured payees")
    source = sources[0]
    regular_pay = round(source["regular_amount"])
    bonus_threshold = round(source["regular_amount"] * 1.5)
    return {
        "regular_pay": regular_pay,
        "bonus_threshold": bonus_threshold,
        "payee": source["payee"],
        "transaction_count": source["count"],
        "frequency": source["frequency"],
    }


def _compute_recommendations(
    groups: list[str],
    categories: list[str],
    accounts: list[str],
) -> dict[str, list[str]]:
    return {
        "retirement_401k": [a for a in accounts if re.search(r"401.?k", a, re.I)],
        "retirement_roth_ira": [a for a in accounts if re.search(r"roth", a, re.I)],
        "retirement_trad_ira": [
            a for a in accounts if re.search(r"traditional|trad\.?\s*ira", a, re.I) and not re.search(r"roth", a, re.I)
        ],
        "retirement_taxable": [
            a
            for a in accounts
            if re.search(r"brokerage|taxable|invest", a, re.I) and not re.search(r"401.?k|roth|ira", a, re.I)
        ],
        "ratio_housing": [c for c in categories if re.search(r"housing|mortgage|rent|home", c, re.I)],
        "ratio_auto": [c for c in categories if re.search(r"auto|car|vehicle|transport", c, re.I)],
        "ratio_debt": [c for c in categories if re.search(r"debt|loan|student", c, re.I)],
        "excluded_groups": [g for g in groups if g in {"Credit Card Payments", "Internal Master Category"}],
        "bonus_funded_groups": [
            g for g in groups if re.search(r"saving|sinking|retirement|annual|birthday|christmas|holiday", g, re.I)
        ],
        "bonus_funded_categories": [
            c
            for c in categories
            if re.search(r"saving|sinking|retirement|annual|birthday|christmas|holiday|vacation|trip", c, re.I)
        ],
        "excluded_categories": [c for c in categories if re.search(r"income|holding|interest|reward", c, re.I)],
        "subscription_categories": [c for c in categories if re.search(r"subscription", c, re.I)],
    }


@router.get("/admin/validation-data")
def get_validation_data() -> dict[str, Any]:
    conn = get_connection()
    try:
        latest_row = conn.execute("SELECT MAX(budget_month) AS m FROM budget_categories WHERE budgeted > 0").fetchone()
        latest_month = latest_row["m"] if latest_row and latest_row["m"] else None
        if not latest_month:
            return {"accounts": [], "groups": [], "categories": [], "payees": [], "category_group_map": {}}

        accounts = conn.execute("SELECT DISTINCT name FROM accounts WHERE deleted = 0 ORDER BY name").fetchall()
        groups = conn.execute(
            "SELECT DISTINCT category_group_name FROM budget_categories "
            "WHERE budget_month = ? AND deleted = 0 AND hidden = 0 "
            "AND category_group_name IS NOT NULL ORDER BY category_group_name",
            (latest_month,),
        ).fetchall()
        categories = conn.execute(
            "SELECT DISTINCT name FROM budget_categories "
            "WHERE budget_month = ? AND deleted = 0 AND hidden = 0 ORDER BY name",
            (latest_month,),
        ).fetchall()
        payees = conn.execute("SELECT name FROM payees WHERE deleted = 0 AND name IS NOT NULL ORDER BY name").fetchall()

        cat_group_rows = conn.execute(
            "SELECT DISTINCT name, category_group_name FROM budget_categories "
            "WHERE budget_month = ? AND deleted = 0 AND hidden = 0 "
            "AND category_group_name IS NOT NULL",
            (latest_month,),
        ).fetchall()
        category_group_map = {r["name"]: r["category_group_name"] for r in cat_group_rows}

        acct_list = [r["name"] for r in accounts]
        group_list = [r["category_group_name"] for r in groups]
        cat_list = [r["name"] for r in categories]
        return {
            "accounts": acct_list,
            "groups": group_list,
            "categories": cat_list,
            "payees": [r["name"] for r in payees],
            "category_group_map": category_group_map,
            "recommendations": _compute_recommendations(group_list, cat_list, acct_list),
        }
    except sqlite3.OperationalError:
        return {"accounts": [], "groups": [], "categories": [], "payees": [], "category_group_map": {}}
    finally:
        conn.close()
