"""Orchestrator — parses user intent, dispatches sub-agents, aggregates results.

Sequencing
----------
1. Inventory Agent runs first (sequential).
2. News, Supplier, Forecast agents run in parallel, each given the inventory
   report as additional context.
3. Orchestrator aggregates into a single ProcurementDecision (risk score,
   immediate order qty, alternative supplier, next trigger condition).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

from anthropic import AsyncAnthropic

from .agents import ForecastAgent, InventoryAgent, NewsAgent, SupplierAgent
from .agents.base import DEFAULT_MODEL, BaseAgent, _extract_json
from .data_sources import load_scenario
from .models import (
    Component,
    ForecastReport,
    InventoryReport,
    NewsRiskReport,
    ProcurementDecision,
    SupplierReport,
)


AGGREGATOR_PROMPT = """You are the Procurement Orchestrator.

You receive the user's natural-language query plus four structured agent
reports (inventory, news, supplier, forecast). Produce a single procurement
decision.

Composite risk_score (0-100) combines:
  - inventory.status (critical→+40, low→+20, healthy→+0)
  - news.risk_score  (weight 0.4)
  - forecast.shortfall_risk (+15 if true) and low confidence (+10 if <0.5)
  - supplier.top_alternative absent when inventory is low/critical (+10)

immediate_order_qty =
  round(inventory.recommended_order_qty * news.supply_adjustment_factor)

alternative_supplier = supplier.top_alternative.supplier_name if its
  recommendation is "activate" OR risk_score >= 60, else null.

next_trigger_condition: one concrete, measurable threshold that, if crossed,
should trigger a re-run (e.g. "news.risk_score > 70", "coverage < 25 days",
"supplier X capacity drops below Y units").

Output ONLY JSON:
{
  "risk_score": float,                  // 0-100
  "immediate_order_qty": int,
  "alternative_supplier": str | null,
  "monitor_metric": str,
  "next_trigger_condition": str,
  "rationale": str                       // 2-4 sentences
}
"""


class Aggregator(BaseAgent):
    name = "aggregator"
    system_prompt = AGGREGATOR_PROMPT


class Orchestrator:
    def __init__(self, client: Optional[AsyncAnthropic] = None, model: str = DEFAULT_MODEL):
        self.client = client or AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.model = model
        self.inventory = InventoryAgent(self.client, model)
        self.news = NewsAgent(self.client, model)
        self.supplier = SupplierAgent(self.client, model)
        self.forecast = ForecastAgent(self.client, model)
        self.aggregator = Aggregator(self.client, model)

    async def run(self, query: str, sku: Optional[str] = None) -> ProcurementDecision:
        """Route a natural-language procurement query end-to-end."""
        sku = sku or await self._infer_sku(query)
        scenario = load_scenario(sku)
        component: Component = scenario["component"]

        # 1. Inventory first (sequential dependency).
        inventory_report: InventoryReport = await self.inventory.analyze(
            component, scenario["inventory_history"]
        )

        # 2. Fan out: news, supplier, forecast in parallel.
        news_task = self.news.analyze(component, inventory_report, scenario["headlines"])
        supplier_task = self.supplier.analyze(
            component, inventory_report, scenario["supplier_candidates"]
        )
        forecast_task = self.forecast.analyze(component, scenario["deployment_stats"])
        news_report, supplier_report, forecast_report = await asyncio.gather(
            news_task, supplier_task, forecast_task
        )

        # 3. Aggregate.
        decision_payload = await self._aggregate(
            query, inventory_report, news_report, supplier_report, forecast_report
        )
        return ProcurementDecision(
            query=query,
            sku=component.sku,
            risk_score=decision_payload["risk_score"],
            immediate_order_qty=decision_payload["immediate_order_qty"],
            alternative_supplier=decision_payload["alternative_supplier"],
            monitor_metric=decision_payload["monitor_metric"],
            next_trigger_condition=decision_payload["next_trigger_condition"],
            rationale=decision_payload["rationale"],
            inventory=inventory_report,
            news=news_report,
            supplier=supplier_report,
            forecast=forecast_report,
        )

    async def _aggregate(
        self,
        query: str,
        inventory: InventoryReport,
        news: NewsRiskReport,
        supplier: SupplierReport,
        forecast: ForecastReport,
    ) -> dict:
        user = json.dumps(
            {
                "query": query,
                "inventory": inventory.model_dump(),
                "news": news.model_dump(),
                "supplier": supplier.model_dump(),
                "forecast": forecast.model_dump(),
            },
            default=str,
        )
        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=[
                {
                    "type": "text",
                    "text": AGGREGATOR_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        return _extract_json(text)

    async def _infer_sku(self, query: str) -> str:
        """Ask Claude to pick a SKU from the scenario catalog when none supplied."""
        from .data_sources import list_skus

        catalog = list_skus()
        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=100,
            system="Pick the SKU from the provided catalog that best matches the user query. Reply with just the SKU string.",
            messages=[
                {
                    "role": "user",
                    "content": f"catalog: {catalog}\nquery: {query}\nSKU:",
                }
            ],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        for sku in catalog:
            if sku.lower() in text.lower():
                return sku
        return catalog[0]
