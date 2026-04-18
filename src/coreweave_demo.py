"""CoreWeave demo data access and live news-risk ingestion."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any, Optional

import duckdb
import httpx
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from .agents import NewsAgent
from .agents.base import DEFAULT_MODEL
from .models import Component, InventoryReport


GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
SUPPLY_TERMS = "(shortage OR delay OR disruption OR capacity OR supply)"
SKU_QUERY_TERMS = {
    "HBM3E": '("HBM3E" OR "high bandwidth memory" OR "SK Hynix")',
    "B200-SXM": '("NVIDIA B200" OR "Blackwell GPU" OR "NVIDIA Blackwell")',
    "H100-SXM": '("NVIDIA H100" OR "H100 GPU" OR "HGX H100")',
    "CX7-400G": '("ConnectX-7" OR "400G adapter" OR "NVIDIA Networking")',
    "NVSWITCH-4": '("NVLink Switch" OR NVSwitch)',
    "CDU-120KW": '("cooling distribution unit" OR "data center liquid cooling")',
    "GB200-NVL72": '("GB200 NVL72" OR "NVIDIA GB200" OR "Blackwell rack")',
    "800G-OSFP": '("800G OSFP" OR "800G transceiver" OR "data center optics")',
    "EPYC-9755": '("AMD EPYC 9755" OR "EPYC Turin" OR "server CPU")',
    "4TB-NVME": '("enterprise NVMe SSD" OR "4TB NVMe" OR "data center SSD")',
}


def connect_motherduck() -> duckdb.DuckDBPyConnection:
    load_dotenv()
    token = os.getenv("MOTHERDUCK_TOKEN")
    database = os.getenv("MOTHERDUCK_DATABASE", "md:beaver")
    if not token:
        raise RuntimeError("MOTHERDUCK_TOKEN is not set; CoreWeave demo endpoints require MotherDuck")
    os.environ["motherduck_token"] = token
    return duckdb.connect(database)


def ensure_news_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS demo_coreweave")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS demo_coreweave.gdelt_articles (
            run_id TEXT,
            sku TEXT,
            fetched_at TIMESTAMP,
            lookback_days INTEGER,
            gdelt_query TEXT,
            published_at TEXT,
            title TEXT,
            url TEXT,
            domain TEXT,
            source_country TEXT,
            source_language TEXT,
            snippet TEXT,
            raw_payload JSON
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS demo_coreweave.news_risk_ratings (
            run_id TEXT PRIMARY KEY,
            created_at TIMESTAMP,
            sku TEXT,
            component_name TEXT,
            lookback_days INTEGER,
            article_count INTEGER,
            inventory_status TEXT,
            days_of_coverage DOUBLE,
            recommended_order_qty INTEGER,
            gdelt_query TEXT,
            risk_score DOUBLE,
            trend TEXT,
            supply_adjustment_factor DOUBLE,
            window_days INTEGER,
            key_events JSON,
            rationale TEXT,
            rating_payload JSON
        )
        """
    )


def _columns_to_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    return json.loads(value)


def _inventory_status(days_of_coverage: float, lead_time_days: int, safe_threshold_days: int) -> str:
    if days_of_coverage < lead_time_days:
        return "critical"
    if days_of_coverage < safe_threshold_days:
        return "low"
    return "healthy"


def _inventory_summary(row: dict[str, Any]) -> dict[str, Any]:
    days_of_coverage = round(float(row["stock"]) / float(row["daily_burn_rate"]), 2)
    status = _inventory_status(
        days_of_coverage,
        int(row["lead_time_days"]),
        int(row["safe_threshold_days"]),
    )
    target_stock = int(float(row["daily_burn_rate"]) * float(row["safe_threshold_days"]) * 2)
    recommended_order_qty = max(0, target_stock - int(row["stock"]))
    return {
        "sku": row["sku"],
        "observed_at": row["observed_at"],
        "stock": row["stock"],
        "inbound_units": row["inbound_units"],
        "allocated_units": row["allocated_units"],
        "reserved_units": row["reserved_units"],
        "region": row["region"],
        "facility": row["facility"],
        "daily_burn_rate": row["daily_burn_rate"],
        "lead_time_days": row["lead_time_days"],
        "safe_threshold_days": row["safe_threshold_days"],
        "days_of_coverage": days_of_coverage,
        "status": status,
        "recommended_order_qty": recommended_order_qty,
    }


def list_components() -> list[dict[str, Any]]:
    con = connect_motherduck()
    ensure_news_tables(con)
    rows = _columns_to_dicts(
        con.execute(
            """
            WITH latest_inventory AS (
                SELECT *
                FROM demo_coreweave.inventory_snapshots
                QUALIFY row_number() OVER (PARTITION BY sku ORDER BY observed_at DESC) = 1
            ),
            latest_risk AS (
                SELECT sku, risk_score, trend, supply_adjustment_factor, created_at AS risk_created_at
                FROM demo_coreweave.news_risk_ratings
                QUALIFY row_number() OVER (PARTITION BY sku ORDER BY created_at DESC) = 1
            )
            SELECT
                c.sku,
                c.component_name,
                c.category,
                c.current_stock,
                c.daily_burn_rate,
                c.lead_time_days,
                c.safe_threshold_days,
                c.unit_cost_usd,
                c.coreweave_use_case,
                c.criticality,
                c.is_synthetic,
                i.observed_at,
                i.stock,
                i.inbound_units,
                i.allocated_units,
                i.reserved_units,
                i.region,
                i.facility,
                r.risk_score,
                r.trend,
                r.supply_adjustment_factor,
                r.risk_created_at
            FROM demo_coreweave.components c
            LEFT JOIN latest_inventory i ON i.sku = c.sku
            LEFT JOIN latest_risk r ON r.sku = c.sku
            ORDER BY c.sku
            """
        )
    )
    for row in rows:
        if row["stock"] is not None:
            summary = _inventory_summary(row)
            row["days_of_coverage"] = summary["days_of_coverage"]
            row["inventory_status"] = summary["status"]
            row["recommended_order_qty"] = summary["recommended_order_qty"]
    return rows


def get_inventory(sku: str, limit: int = 90) -> dict[str, Any]:
    con = connect_motherduck()
    latest_rows = _columns_to_dicts(
        con.execute(
            """
            SELECT
                c.sku,
                c.component_name,
                c.category,
                c.daily_burn_rate,
                c.lead_time_days,
                c.safe_threshold_days,
                c.unit_cost_usd,
                c.coreweave_use_case,
                c.criticality,
                i.observed_at,
                i.stock,
                i.inbound_units,
                i.allocated_units,
                i.reserved_units,
                i.region,
                i.facility
            FROM demo_coreweave.components c
            JOIN demo_coreweave.inventory_snapshots i ON i.sku = c.sku
            WHERE c.sku = ?
            QUALIFY row_number() OVER (PARTITION BY c.sku ORDER BY i.observed_at DESC) = 1
            """,
            [sku],
        )
    )
    if not latest_rows:
        raise ValueError(f"No inventory found for SKU {sku!r}")

    history = _columns_to_dicts(
        con.execute(
            """
            SELECT
                observed_at,
                stock,
                inbound_units,
                allocated_units,
                reserved_units,
                region,
                facility
            FROM demo_coreweave.inventory_snapshots
            WHERE sku = ?
            ORDER BY observed_at DESC
            LIMIT ?
            """,
            [sku, limit],
        )
    )
    latest = latest_rows[0]
    return {
        "component": {
            "sku": latest["sku"],
            "component_name": latest["component_name"],
            "category": latest["category"],
            "unit_cost_usd": latest["unit_cost_usd"],
            "coreweave_use_case": latest["coreweave_use_case"],
            "criticality": latest["criticality"],
        },
        "latest": _inventory_summary(latest),
        "history": list(reversed(history)),
    }


def get_suppliers(sku: str) -> list[dict[str, Any]]:
    con = connect_motherduck()
    return _columns_to_dicts(
        con.execute(
            """
            SELECT
                sku,
                supplier_name,
                supplier_role,
                technical_compat,
                qualification_timeline_days,
                available_capacity_units,
                geographic_region,
                composite_score,
                recommendation,
                notes,
                is_synthetic,
                updated_at
            FROM demo_coreweave.supplier_candidates
            WHERE sku = ?
            ORDER BY composite_score DESC, available_capacity_units DESC
            """,
            [sku],
        )
    )


def get_news_risk(sku: str, limit: int = 10) -> dict[str, Any]:
    con = connect_motherduck()
    ensure_news_tables(con)
    ratings = _columns_to_dicts(
        con.execute(
            """
            SELECT
                run_id,
                created_at,
                sku,
                component_name,
                lookback_days,
                article_count,
                inventory_status,
                days_of_coverage,
                recommended_order_qty,
                gdelt_query,
                risk_score,
                trend,
                supply_adjustment_factor,
                window_days,
                key_events,
                rationale
            FROM demo_coreweave.news_risk_ratings
            WHERE sku = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [sku, limit],
        )
    )
    for rating in ratings:
        rating["key_events"] = _parse_json(rating["key_events"])
    return {"latest": ratings[0] if ratings else None, "ratings": ratings}


def get_articles(sku: str, limit: int = 25, run_id: Optional[str] = None) -> list[dict[str, Any]]:
    con = connect_motherduck()
    ensure_news_tables(con)
    if run_id:
        rows = _columns_to_dicts(
            con.execute(
                """
                SELECT
                    run_id,
                    sku,
                    fetched_at,
                    lookback_days,
                    gdelt_query,
                    published_at,
                    title,
                    url,
                    domain,
                    source_country,
                    source_language,
                    snippet
                FROM demo_coreweave.gdelt_articles
                WHERE sku = ? AND run_id = ?
                ORDER BY published_at DESC
                LIMIT ?
                """,
                [sku, run_id, limit],
            )
        )
    else:
        rows = _columns_to_dicts(
            con.execute(
                """
                SELECT
                    run_id,
                    sku,
                    fetched_at,
                    lookback_days,
                    gdelt_query,
                    published_at,
                    title,
                    url,
                    domain,
                    source_country,
                    source_language,
                    snippet
                FROM demo_coreweave.gdelt_articles
                WHERE sku = ?
                ORDER BY fetched_at DESC, published_at DESC
                LIMIT ?
                """,
                [sku, limit],
            )
        )
    return rows


def select_component(sku: Optional[str] = None) -> tuple[Component, InventoryReport]:
    con = connect_motherduck()
    if sku:
        rows = _columns_to_dicts(
            con.execute(
                """
                SELECT
                    c.sku,
                    c.component_name,
                    c.daily_burn_rate,
                    c.lead_time_days,
                    c.safe_threshold_days,
                    c.unit_cost_usd,
                    i.observed_at,
                    i.stock,
                    i.inbound_units,
                    i.allocated_units,
                    i.reserved_units,
                    i.region,
                    i.facility
                FROM demo_coreweave.components c
                JOIN demo_coreweave.inventory_snapshots i ON i.sku = c.sku
                WHERE c.sku = ?
                QUALIFY row_number() OVER (PARTITION BY c.sku ORDER BY i.observed_at DESC) = 1
                """,
                [sku],
            )
        )
    else:
        rows = _columns_to_dicts(
            con.execute(
                """
                SELECT
                    c.sku,
                    c.component_name,
                    c.daily_burn_rate,
                    c.lead_time_days,
                    c.safe_threshold_days,
                    c.unit_cost_usd,
                    i.observed_at,
                    i.stock,
                    i.inbound_units,
                    i.allocated_units,
                    i.reserved_units,
                    i.region,
                    i.facility
                FROM demo_coreweave.components c
                JOIN demo_coreweave.inventory_snapshots i ON i.sku = c.sku
                QUALIFY row_number() OVER (PARTITION BY c.sku ORDER BY i.observed_at DESC) = 1
                ORDER BY i.stock / nullif(c.daily_burn_rate, 0) ASC
                LIMIT 1
                """
            )
        )

    if not rows:
        detail = f" for SKU {sku!r}" if sku else ""
        raise ValueError(f"No CoreWeave demo component inventory found{detail}")

    row = rows[0]
    summary = _inventory_summary(row)
    component = Component(
        sku=row["sku"],
        name=row["component_name"],
        current_stock=int(row["stock"]),
        daily_burn_rate=float(row["daily_burn_rate"]),
        lead_time_days=int(row["lead_time_days"]),
        safe_threshold_days=int(row["safe_threshold_days"]),
        unit_cost_usd=float(row["unit_cost_usd"]),
    )
    inventory = InventoryReport(
        sku=row["sku"],
        days_of_coverage=summary["days_of_coverage"],
        status=summary["status"],
        recommended_order_qty=summary["recommended_order_qty"],
        reasoning=(
            f"Latest inventory snapshot at {row['observed_at']}: stock={row['stock']}, "
            f"inbound={row['inbound_units']}, allocated={row['allocated_units']}, "
            f"reserved={row['reserved_units']}."
        ),
    )
    return component, inventory


def build_gdelt_query(sku: str, component_name: str) -> str:
    component_terms = SKU_QUERY_TERMS.get(sku)
    if component_terms:
        return f"{component_terms} {SUPPLY_TERMS}"
    return f'("{sku}" OR "{component_name}") {SUPPLY_TERMS}'


def build_broader_gdelt_query(sku: str, component_name: str) -> str:
    return SKU_QUERY_TERMS.get(sku) or f'("{sku}" OR "{component_name}")'


async def fetch_gdelt_articles(query: str, days: int, max_records: int) -> list[dict[str, Any]]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "sort": "datedesc",
        "maxrecords": max_records,
        "timespan": f"{days}d",
    }
    headers = {"User-Agent": "agentic-ai-hackathon-coreweave-demo/0.1"}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
        for attempt in range(6):
            response = await client.get(GDELT_DOC_API_URL, params=params)
            if response.status_code != 429:
                response.raise_for_status()
                payload = response.json()
                break

            retry_after = response.headers.get("retry-after")
            if attempt == 5:
                return []
            try:
                wait_seconds = int(retry_after) if retry_after else 6 + attempt * 2
            except ValueError:
                wait_seconds = 6 + attempt * 2
            await asyncio.sleep(min(wait_seconds, 15))
        else:
            payload = {}

    articles = payload.get("articles", [])
    if not isinstance(articles, list):
        return []
    return articles


def article_to_headline(article: dict[str, Any]) -> dict[str, str]:
    title = str(article.get("title") or "").strip()
    domain = str(article.get("domain") or "").strip()
    source_country = str(article.get("sourcecountry") or "").strip()
    source_language = str(article.get("language") or "").strip()
    url = str(article.get("url") or "").strip()
    seendate = str(article.get("seendate") or "").strip()
    snippet_parts = [part for part in [domain, source_country, source_language, url] if part]
    return {
        "date": seendate,
        "title": title,
        "snippet": " | ".join(snippet_parts),
    }


def persist_articles(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    sku: str,
    fetched_at: datetime,
    days: int,
    query: str,
    articles: list[dict[str, Any]],
) -> None:
    rows = []
    for article in articles:
        rows.append(
            (
                run_id,
                sku,
                fetched_at,
                days,
                query,
                article.get("seendate"),
                article.get("title"),
                article.get("url"),
                article.get("domain"),
                article.get("sourcecountry"),
                article.get("language"),
                None,
                json.dumps(article),
            )
        )
    if rows:
        con.executemany(
            """
            INSERT INTO demo_coreweave.gdelt_articles VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            rows,
        )


def persist_rating(
    con: duckdb.DuckDBPyConnection,
    run_id: str,
    created_at: datetime,
    component: Component,
    inventory: InventoryReport,
    days: int,
    query: str,
    article_count: int,
    rating_payload: dict[str, Any],
) -> None:
    con.execute(
        """
        INSERT INTO demo_coreweave.news_risk_ratings VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            run_id,
            created_at,
            component.sku,
            component.name,
            days,
            article_count,
            inventory.status,
            inventory.days_of_coverage,
            inventory.recommended_order_qty,
            query,
            rating_payload["risk_score"],
            rating_payload["trend"],
            rating_payload["supply_adjustment_factor"],
            rating_payload["window_days"],
            json.dumps(rating_payload["key_events"]),
            rating_payload["rationale"],
            json.dumps(rating_payload),
        ],
    )


async def ingest_news_risk(
    sku: Optional[str] = None,
    days: int = 1,
    max_records: int = 25,
    query: Optional[str] = None,
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")
    if max_records < 1 or max_records > 250:
        raise ValueError("max_records must be between 1 and 250")

    con = connect_motherduck()
    ensure_news_tables(con)
    component, inventory = select_component(sku)

    gdelt_query = query or build_gdelt_query(component.sku, component.name)
    articles = await fetch_gdelt_articles(gdelt_query, days, max_records)
    if not articles and not query:
        gdelt_query = build_broader_gdelt_query(component.sku, component.name)
        articles = await fetch_gdelt_articles(gdelt_query, days, max_records)

    headlines = [article_to_headline(article) for article in articles if article.get("title")]
    now = datetime.now(UTC).replace(microsecond=0)
    run_id = f"{component.sku}-{now.strftime('%Y%m%d%H%M%S')}"
    persist_articles(con, run_id, component.sku, now, days, gdelt_query, articles)

    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    rating = await NewsAgent(client, os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)).analyze(
        component,
        inventory,
        headlines,
        requested_lookback_days=days,
    )
    rating_payload = rating.model_dump(mode="json")
    persist_rating(
        con,
        run_id,
        now,
        component,
        inventory,
        days,
        gdelt_query,
        len(articles),
        rating_payload,
    )

    return {
        "run_id": run_id,
        "selected_sku": component.sku,
        "inventory_status": inventory.status,
        "days_of_coverage": inventory.days_of_coverage,
        "gdelt_articles": len(articles),
        "warning": None if articles else "No GDELT articles were returned for this run.",
        "risk_score": rating.risk_score,
        "trend": rating.trend,
        "supply_adjustment_factor": rating.supply_adjustment_factor,
        "rating": rating_payload,
        "persisted": "demo_coreweave.news_risk_ratings",
    }
