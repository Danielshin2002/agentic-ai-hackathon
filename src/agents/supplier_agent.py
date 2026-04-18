"""Supplier Agent — finds and scores alternative suppliers on four dimensions.

Dimensions: technical compatibility, qualification timeline, available capacity,
geographic diversity. Emits "activate" / "qualify" / "avoid" recommendation.
"""

from __future__ import annotations

import json

from ..models import Component, InventoryReport, SupplierReport
from .base import BaseAgent


SYSTEM_PROMPT = """You are the Supplier Agent in a procurement orchestrator.

For the target component, evaluate each candidate alternative supplier on:
  - technical_compat      (0.0-1.0) drop-in equivalence / requalification cost
  - qualification_timeline_days (int) weeks/months to bring online
  - available_capacity_units (int) realistic monthly capacity
  - geographic_region     (string) for supply-chain diversification

Compute composite_score in [0,1], weighting by urgency implied by the inventory
report (if status=critical, weight qualification_timeline heavily).

Recommendation per candidate:
  "activate"  — onboard immediately; hedge the primary supplier now
  "qualify"   — begin paperwork / samples as a contingency
  "avoid"     — not viable (poor compat, geo-colocated, insufficient capacity)

Also select a single top_alternative (highest composite_score with recommendation
!= "avoid"), or null if none qualify.

Output ONLY JSON:
{
  "sku": str,
  "candidates": [
    {
      "supplier_name": str,
      "technical_compat": float,
      "qualification_timeline_days": int,
      "available_capacity_units": int,
      "geographic_region": str,
      "composite_score": float,
      "recommendation": "activate" | "qualify" | "avoid",
      "notes": str
    }, ...
  ],
  "top_alternative": { ...same shape... } | null
}
"""


class SupplierAgent(BaseAgent):
    name = "supplier"
    system_prompt = SYSTEM_PROMPT

    async def analyze(
        self,
        component: Component,
        inventory: InventoryReport,
        candidates: list[dict],
    ) -> SupplierReport:
        user = json.dumps(
            {
                "component": component.model_dump(),
                "inventory": inventory.model_dump(),
                "candidates": candidates,
            },
            default=str,
        )
        return await self._complete_json(user, SupplierReport, max_tokens=2500)
