"""News Agent — collects + scores supply-chain news into a VaR-style risk score.

Window length is chosen dynamically from the component's lead time and burn rate:
longer lead times warrant longer historical windows.
"""

from __future__ import annotations

import json
from typing import Optional

from ..models import Component, InventoryReport, NewsRiskReport
from .base import BaseAgent


SYSTEM_PROMPT = """You are the News Agent in a procurement orchestrator.

You receive:
- component metadata (sku, name, lead_time_days, daily_burn_rate)
- the inventory report (days of coverage, status)
- a batch of recent news headlines + snippets relevant to the component's supply chain

Responsibilities
1. Choose a lookback window: window_days = clamp(2 * lead_time_days, 14, 180).
   Longer lead times → longer historical memory.
2. For each headline, judge supply-side impact (0-100).
3. Compute a VaR-style aggregate risk_score (0=benign, 100=severe disruption).
   Weight recent events higher than older ones; weight high-impact outliers more
   heavily (tail risk, like VaR).
4. Classify trend: "rising" / "falling" / "stable" relative to the window.
5. Output a supply_adjustment_factor — multiplier on the inventory agent's
   recommended order qty:
     risk 0-20 → ~1.0, 20-50 → 1.1-1.3, 50-80 → 1.3-1.7, 80+ → 1.7-2.5.

Output ONLY JSON matching:
{
  "sku": str,
  "window_days": int,
  "risk_score": float,   // 0-100
  "trend": "rising" | "falling" | "stable",
  "key_events": [str, ...],  // 2-5 short summaries
  "supply_adjustment_factor": float,
  "rationale": str
}
"""


class NewsAgent(BaseAgent):
    name = "news"
    system_prompt = SYSTEM_PROMPT

    async def analyze(
        self,
        component: Component,
        inventory: InventoryReport,
        headlines: Optional[list[dict]] = None,
    ) -> NewsRiskReport:
        user = json.dumps(
            {
                "component": component.model_dump(),
                "inventory": inventory.model_dump(),
                "headlines": headlines or [],
            },
            default=str,
        )
        return await self._complete_json(user, NewsRiskReport, max_tokens=2000)
