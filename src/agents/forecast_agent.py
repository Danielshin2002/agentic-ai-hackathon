"""Forecast Agent — projects x-day demand with VaR and confidence bands.

Horizon is driven by max(lead_times) across the affected component(s).
Uses historical deployment statistics to quantify YoY growth and flag where
demand is likely to outpace supply at recommended order quantity.
"""

from __future__ import annotations

import json

from ..models import Component, ForecastReport
from .base import BaseAgent


SYSTEM_PROMPT = """You are the Forecast Agent in a procurement orchestrator.

Inputs
- component (sku, daily_burn_rate, lead_time_days, ...)
- deployment_stats: list of {date, units_deployed} historical points
- horizon_days: forecast window; if missing, use max(lead_time_days, 60).

Method (VaR-style)
1. Compute recent burn trend and year-over-year growth_rate from deployment_stats.
2. Project expected_demand = trend-adjusted burn_rate * horizon_days.
3. Compute var_95 as the 95th-percentile upper bound of demand given historical
   volatility (assume rough log-normal if unknown; state your assumption briefly).
4. Confidence in [0,1] = inverse-proportional to volatility and data scarcity.
5. shortfall_risk = true if expected_demand + margin exceeds typical supply.

Quantify how increasing order quantity reduces risk — state this in rationale,
e.g. "ordering +15% drops 30-day shortfall probability from 18% to 6%".

Output ONLY JSON:
{
  "sku": str,
  "horizon_days": int,
  "expected_demand": int,
  "var_95": int,
  "confidence": float,           // 0.0-1.0
  "yoy_growth_rate": float,      // e.g. 0.18 = 18%
  "shortfall_risk": bool,
  "rationale": str
}
"""


class ForecastAgent(BaseAgent):
    name = "forecast"
    system_prompt = SYSTEM_PROMPT

    async def analyze(
        self,
        component: Component,
        deployment_stats: list[dict],
        horizon_days: int | None = None,
    ) -> ForecastReport:
        user = json.dumps(
            {
                "component": component.model_dump(),
                "deployment_stats": deployment_stats,
                "horizon_days": horizon_days or max(component.lead_time_days, 60),
            },
            default=str,
        )
        return await self._complete_json(user, ForecastReport, max_tokens=2000)
