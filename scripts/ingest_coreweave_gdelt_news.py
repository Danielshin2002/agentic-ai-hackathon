"""Fetch GDELT news for a CoreWeave demo component and persist a risk rating.

Default behavior selects the component with the lowest days of inventory
coverage from demo_coreweave.inventory_snapshots. Use --sku to score a specific
component, and --days to choose the GDELT lookback window.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
from anthropic import AsyncAnthropic, AuthenticationError
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents import NewsAgent
from src.agents.base import DEFAULT_MODEL
from src.models import Component, InventoryReport


GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
SUPPLY_TERMS = "(shortage OR delay OR disruption OR capacity OR supply)"
SKU_QUERY_TERMS = {
    "HBM3E": '("HBM3E" OR "high bandwidth memory" OR "SK Hynix")',
    "B200-SXM": '("NVIDIA B200" OR "Blackwell GPU" OR "NVIDIA Blackwell")',
    "H100-SXM": '("NVIDIA H100" OR "H100 GPU" OR "HGX H100")',
    "CX7-400G": '("ConnectX-7" OR "400G adapter" OR "NVIDIA Networking")',
    "NVSWITCH-4": '("NVLink Switch" OR NVSwitch)',
    "CDU-120KW": '("cooling distribution unit" OR "data center liquid cooling")',
}


def connect_motherduck() -> duckdb.DuckDBPyConnection:
    load_dotenv()
    token = os.getenv("MOTHERDUCK_TOKEN")
    database = os.getenv("MOTHERDUCK_DATABASE", "md:beaver")
    if not token:
        raise RuntimeError("MOTHERDUCK_TOKEN is not set; refusing to use local fallback")
    os.environ["motherduck_token"] = token
    return duckdb.connect(database)


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
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


def select_component(con: duckdb.DuckDBPyConnection, sku: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if sku:
        row = con.execute(
            """
            SELECT
                c.sku,
                c.component_name,
                c.current_stock,
                c.daily_burn_rate,
                c.lead_time_days,
                c.safe_threshold_days,
                c.unit_cost_usd,
                i.observed_at,
                i.stock,
                i.inbound_units,
                i.allocated_units,
                i.reserved_units
            FROM demo_coreweave.components c
            JOIN demo_coreweave.inventory_snapshots i ON i.sku = c.sku
            WHERE c.sku = ?
            QUALIFY row_number() OVER (PARTITION BY c.sku ORDER BY i.observed_at DESC) = 1
            """,
            [sku],
        ).fetchone()
    else:
        row = con.execute(
            """
            SELECT
                c.sku,
                c.component_name,
                c.current_stock,
                c.daily_burn_rate,
                c.lead_time_days,
                c.safe_threshold_days,
                c.unit_cost_usd,
                i.observed_at,
                i.stock,
                i.inbound_units,
                i.allocated_units,
                i.reserved_units
            FROM demo_coreweave.components c
            JOIN demo_coreweave.inventory_snapshots i ON i.sku = c.sku
            QUALIFY row_number() OVER (PARTITION BY c.sku ORDER BY i.observed_at DESC) = 1
            ORDER BY i.stock / nullif(c.daily_burn_rate, 0) ASC
            LIMIT 1
            """
        ).fetchone()

    if not row:
        detail = f" for SKU {sku!r}" if sku else ""
        raise RuntimeError(f"No CoreWeave demo component inventory found{detail}")

    (
        selected_sku,
        component_name,
        current_stock,
        daily_burn_rate,
        lead_time_days,
        safe_threshold_days,
        unit_cost_usd,
        observed_at,
        stock,
        inbound_units,
        allocated_units,
        reserved_units,
    ) = row

    days_of_coverage = round(float(stock) / float(daily_burn_rate), 2)
    if days_of_coverage < float(lead_time_days):
        status = "critical"
    elif days_of_coverage < float(safe_threshold_days):
        status = "low"
    else:
        status = "healthy"

    target_stock = int(float(daily_burn_rate) * float(safe_threshold_days) * 2)
    recommended_order_qty = max(0, target_stock - int(stock))

    component = {
        "sku": selected_sku,
        "name": component_name,
        "current_stock": int(stock),
        "daily_burn_rate": float(daily_burn_rate),
        "lead_time_days": int(lead_time_days),
        "safe_threshold_days": int(safe_threshold_days),
        "unit_cost_usd": float(unit_cost_usd),
    }
    inventory = {
        "sku": selected_sku,
        "days_of_coverage": days_of_coverage,
        "status": status,
        "recommended_order_qty": recommended_order_qty,
        "reasoning": (
            f"Latest inventory snapshot at {observed_at}: stock={stock}, "
            f"inbound={inbound_units}, allocated={allocated_units}, reserved={reserved_units}."
        ),
    }
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
        for attempt in range(4):
            response = await client.get(GDELT_DOC_API_URL, params=params)
            if response.status_code != 429:
                response.raise_for_status()
                payload = response.json()
                break

            retry_after = response.headers.get("retry-after")
            if attempt == 3:
                response.raise_for_status()
            try:
                wait_seconds = int(retry_after) if retry_after else 6
            except ValueError:
                wait_seconds = 6
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


async def run(args: argparse.Namespace) -> None:
    if args.days < 1:
        raise ValueError("--days must be at least 1")
    if args.max_records < 1 or args.max_records > 250:
        raise ValueError("--max-records must be between 1 and 250")

    con = connect_motherduck()
    ensure_tables(con)
    component_payload, inventory_payload = select_component(con, args.sku)
    component = Component.model_validate(component_payload)
    inventory = InventoryReport.model_validate(inventory_payload)

    query = args.query or build_gdelt_query(component.sku, component.name)
    articles = await fetch_gdelt_articles(query, args.days, args.max_records)
    if not articles and not args.query:
        query = build_broader_gdelt_query(component.sku, component.name)
        articles = await fetch_gdelt_articles(query, args.days, args.max_records)
    headlines = [article_to_headline(article) for article in articles if article.get("title")]
    now = datetime.now(UTC).replace(microsecond=0)
    run_id = f"{component.sku}-{now.strftime('%Y%m%d%H%M%S')}"
    persist_articles(con, run_id, component.sku, now, args.days, query, articles)

    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    try:
        rating = await NewsAgent(client, os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)).analyze(
            component,
            inventory,
            headlines,
            requested_lookback_days=args.days,
        )
    except AuthenticationError as exc:
        raise RuntimeError(
            "Claude rejected ANTHROPIC_API_KEY, so GDELT articles were saved but "
            "no news risk rating was created. Update .env with a valid key and rerun."
        ) from exc

    rating_payload = rating.model_dump(mode="json")
    persist_rating(
        con,
        run_id,
        now,
        component,
        inventory,
        args.days,
        query,
        len(articles),
        rating_payload,
    )

    print(f"run_id={run_id}")
    print(f"selected_sku={component.sku}")
    print(f"inventory_status={inventory.status}")
    print(f"days_of_coverage={inventory.days_of_coverage}")
    print(f"gdelt_articles={len(articles)}")
    print(f"risk_score={rating.risk_score}")
    print(f"trend={rating.trend}")
    print(f"supply_adjustment_factor={rating.supply_adjustment_factor}")
    print(f"persisted=demo_coreweave.news_risk_ratings")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sku", help="CoreWeave demo SKU to score; defaults to lowest coverage")
    parser.add_argument("--days", type=int, default=1, help="GDELT lookback in days")
    parser.add_argument("--max-records", type=int, default=25, help="Maximum GDELT articles to fetch")
    parser.add_argument("--query", help="Override the generated GDELT query")
    return parser.parse_args()


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
