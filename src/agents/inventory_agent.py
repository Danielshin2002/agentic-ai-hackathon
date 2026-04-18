"""Inventory Agent — sequential, runs first.

Analyzes current + historical stock against daily burn rate to compute days of
coverage, flag items below safe thresholds, and recommend reorder quantities.
Its output feeds the News, Supplier, and Forecast agents.
"""

from __future__ import annotations

import json

from ..models import Component, InventoryReport, InventorySnapshot
from .base import BaseAgent


SYSTEM_PROMPT = """You are the Inventory Agent in a procurement orchestrator.

Responsibilities
- Compute days of coverage = current_stock / daily_burn_rate.
- Flag status: "critical" (<lead_time), "low" (<safe_threshold_days), "healthy" otherwise.
- Recommend a reorder quantity that brings coverage to ~2x safe_threshold_days,
  rounded to a sensible lot size. Zero if healthy and no trend risk.
- Consider the historical snapshots: sustained downward drift increases urgency.

Output: return ONLY a JSON object matching this schema:
{
  "sku": str,
  "days_of_coverage": float,
  "status": "critical" | "low" | "healthy",
  "recommended_order_qty": int,
  "reasoning": str  // 1-2 sentence justification
}
No prose outside the JSON.
"""


class InventoryAgent(BaseAgent):
    name = "inventory"
    system_prompt = SYSTEM_PROMPT

    async def analyze(
        self,
        component: Component,
        history: list[InventorySnapshot],
    ) -> InventoryReport:
        user = json.dumps(
            {
                "component": component.model_dump(),
                "history": [s.model_dump(mode="json") for s in history],
            },
            default=str,
        )
        return await self._complete_json(user, InventoryReport)
