"""Shared pydantic data models for agents, reports, and decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


Status = Literal["critical", "low", "healthy"]
Trend = Literal["rising", "falling", "stable"]
SupplierAction = Literal["activate", "qualify", "avoid"]


class Component(BaseModel):
    sku: str
    name: str
    current_stock: int
    daily_burn_rate: float
    lead_time_days: int
    safe_threshold_days: int = 30
    unit_cost_usd: float = 0.0


class InventorySnapshot(BaseModel):
    """Historical stock reading used to detect trend anomalies."""

    sku: str
    date: datetime
    stock: int


class InventoryReport(BaseModel):
    sku: str
    days_of_coverage: float
    status: Status
    recommended_order_qty: int
    reasoning: str


class NewsRiskReport(BaseModel):
    sku: str
    window_days: int
    risk_score: float = Field(ge=0.0, le=100.0, description="VaR-style 0–100 score")
    trend: Trend
    key_events: list[str]
    supply_adjustment_factor: float = Field(
        description="Multiplier applied to base order qty (1.0 = no change)"
    )
    rationale: str


class SupplierScore(BaseModel):
    supplier_name: str
    technical_compat: float = Field(ge=0.0, le=1.0)
    qualification_timeline_days: int
    available_capacity_units: int
    geographic_region: str
    composite_score: float = Field(ge=0.0, le=1.0)
    recommendation: SupplierAction
    notes: str


class SupplierReport(BaseModel):
    sku: str
    candidates: list[SupplierScore]
    top_alternative: Optional[SupplierScore]


class ForecastReport(BaseModel):
    sku: str
    horizon_days: int
    expected_demand: int
    var_95: int = Field(description="95% VaR upper bound of demand")
    confidence: float = Field(ge=0.0, le=1.0)
    yoy_growth_rate: float
    shortfall_risk: bool
    rationale: str


class ProcurementDecision(BaseModel):
    query: str
    sku: str
    risk_score: float = Field(ge=0.0, le=100.0)
    immediate_order_qty: int
    alternative_supplier: Optional[str]
    monitor_metric: str
    next_trigger_condition: str
    rationale: str
    inventory: InventoryReport
    news: NewsRiskReport
    supplier: SupplierReport
    forecast: ForecastReport
    created_at: datetime = Field(default_factory=datetime.utcnow)


class OrchestratorQuery(BaseModel):
    query: str
    sku: Optional[str] = None
