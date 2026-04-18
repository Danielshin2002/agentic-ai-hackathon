"""MotherDuck (DuckDB) persistence for decisions + risk trendlines.

Falls back to a local DuckDB file when MOTHERDUCK_TOKEN is unset, so the demo
runs offline.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import duckdb

from ..models import ProcurementDecision


SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    created_at TIMESTAMP,
    query TEXT,
    sku TEXT,
    risk_score DOUBLE,
    immediate_order_qty INTEGER,
    alternative_supplier TEXT,
    monitor_metric TEXT,
    next_trigger_condition TEXT,
    rationale TEXT,
    payload JSON
);

CREATE SEQUENCE IF NOT EXISTS decisions_id_seq;

CREATE TABLE IF NOT EXISTS risk_trendline (
    sku TEXT,
    observed_at TIMESTAMP,
    risk_score DOUBLE,
    news_window_days INTEGER,
    supply_adjustment_factor DOUBLE
);
"""


class MotherDuckStore:
    def __init__(self, database: Optional[str] = None, token: Optional[str] = None):
        token = token or os.getenv("MOTHERDUCK_TOKEN")
        database = database or os.getenv("MOTHERDUCK_DATABASE", "md:procurement")
        if token:
            os.environ["motherduck_token"] = token
            self.con = duckdb.connect(database)
            self.mode = "motherduck"
        else:
            path = Path(".local.duckdb")
            self.con = duckdb.connect(str(path))
            self.mode = "local"
        self.con.execute(SCHEMA)

    def save_decision(self, decision: ProcurementDecision) -> int:
        payload = decision.model_dump_json()
        row_id = self.con.execute("SELECT nextval('decisions_id_seq')").fetchone()[0]
        self.con.execute(
            """
            INSERT INTO decisions VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                row_id,
                decision.created_at,
                decision.query,
                decision.sku,
                decision.risk_score,
                decision.immediate_order_qty,
                decision.alternative_supplier,
                decision.monitor_metric,
                decision.next_trigger_condition,
                decision.rationale,
                payload,
            ],
        )
        self.con.execute(
            """
            INSERT INTO risk_trendline VALUES (?, ?, ?, ?, ?)
            """,
            [
                decision.sku,
                decision.created_at,
                decision.news.risk_score,
                decision.news.window_days,
                decision.news.supply_adjustment_factor,
            ],
        )
        return row_id

    def recent_decisions(self, sku: Optional[str] = None, limit: int = 20) -> list[dict]:
        if sku:
            rows = self.con.execute(
                "SELECT payload FROM decisions WHERE sku = ? ORDER BY created_at DESC LIMIT ?",
                [sku, limit],
            ).fetchall()
        else:
            rows = self.con.execute(
                "SELECT payload FROM decisions ORDER BY created_at DESC LIMIT ?", [limit]
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def risk_trendline(self, sku: str, limit: int = 100) -> list[dict]:
        rows = self.con.execute(
            """
            SELECT observed_at, risk_score, news_window_days, supply_adjustment_factor
            FROM risk_trendline
            WHERE sku = ?
            ORDER BY observed_at DESC
            LIMIT ?
            """,
            [sku, limit],
        ).fetchall()
        return [
            {
                "observed_at": r[0].isoformat() if r[0] else None,
                "risk_score": r[1],
                "news_window_days": r[2],
                "supply_adjustment_factor": r[3],
            }
            for r in rows
        ]
