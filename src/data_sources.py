"""Sample/demo data sources for hackathon runs.

Swap these with real pulls (ERP, news API, supplier DB) in production.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .models import Component, InventorySnapshot


_COMPONENTS: dict[str, Component] = {
    "HBM3E": Component(
        sku="HBM3E",
        name="SK Hynix HBM3E 24GB stack",
        current_stock=1200,
        daily_burn_rate=85.0,
        lead_time_days=42,
        safe_threshold_days=45,
        unit_cost_usd=420.0,
    ),
    "H100-GPU": Component(
        sku="H100-GPU",
        name="NVIDIA H100 SXM5 GPU",
        current_stock=320,
        daily_burn_rate=12.0,
        lead_time_days=90,
        safe_threshold_days=60,
        unit_cost_usd=30000.0,
    ),
    "CoWoS-S": Component(
        sku="CoWoS-S",
        name="TSMC CoWoS-S substrate",
        current_stock=800,
        daily_burn_rate=40.0,
        lead_time_days=70,
        safe_threshold_days=50,
        unit_cost_usd=1200.0,
    ),
}


_HEADLINES: dict[str, list[dict]] = {
    "HBM3E": [
        {
            "date": "2026-04-01",
            "title": "SK Hynix expands HBM3E Icheon fab output ahead of schedule",
            "snippet": "Capacity up 20% QoQ; yields stabilizing above 70%.",
        },
        {
            "date": "2026-04-09",
            "title": "Typhoon disrupts South Korean logistics corridor",
            "snippet": "Icheon outbound freight delayed 4-7 days.",
        },
        {
            "date": "2026-04-14",
            "title": "Samsung delays HBM3E qualification with top US customer",
            "snippet": "Tightens near-term supply; Hynix volumes oversubscribed.",
        },
    ],
    "H100-GPU": [
        {
            "date": "2026-03-22",
            "title": "TSMC CoWoS capacity allocated through Q3",
            "snippet": "NVIDIA H100/H200 packaging slots full; Blackwell ramp pressuring supply.",
        },
        {
            "date": "2026-04-08",
            "title": "US export controls tightened on advanced GPUs",
            "snippet": "Reallocation of H100 inventory away from restricted regions.",
        },
    ],
    "CoWoS-S": [
        {
            "date": "2026-04-03",
            "title": "TSMC CoWoS capacity doubling program on track for 2026",
            "snippet": "Zhunan + AP6 bring additional 30kwspm online mid-year.",
        },
    ],
}


_SUPPLIERS: dict[str, list[dict]] = {
    "HBM3E": [
        {
            "supplier_name": "Samsung Electronics",
            "technical_compat": 0.92,
            "qualification_timeline_days": 90,
            "available_capacity_units": 4000,
            "geographic_region": "South Korea",
            "notes": "Drop-in from electrical spec perspective; thermal profile differs.",
        },
        {
            "supplier_name": "Micron",
            "technical_compat": 0.85,
            "qualification_timeline_days": 120,
            "available_capacity_units": 2500,
            "geographic_region": "USA/Taiwan",
            "notes": "Geo-diversified but lower stack density; BOM change required.",
        },
    ],
    "H100-GPU": [
        {
            "supplier_name": "AMD MI300X",
            "technical_compat": 0.65,
            "qualification_timeline_days": 180,
            "available_capacity_units": 800,
            "geographic_region": "USA",
            "notes": "Software stack migration (ROCm) required.",
        },
    ],
    "CoWoS-S": [
        {
            "supplier_name": "ASE (FOCoS)",
            "technical_compat": 0.70,
            "qualification_timeline_days": 150,
            "available_capacity_units": 1500,
            "geographic_region": "Taiwan",
            "notes": "Alternative fan-out packaging; different thermal envelope.",
        },
        {
            "supplier_name": "Intel Foveros",
            "technical_compat": 0.55,
            "qualification_timeline_days": 240,
            "available_capacity_units": 600,
            "geographic_region": "USA",
            "notes": "Longer qualification; strategic geo-diversification value.",
        },
    ],
}


def _deployment_stats(sku: str, burn: float) -> list[dict]:
    """Rough monthly deployment history with 18% YoY growth baked in."""
    today = datetime.utcnow().replace(day=1)
    stats = []
    for i in range(18, 0, -1):
        month = today - timedelta(days=30 * i)
        growth = 1.0 + 0.18 * (18 - i) / 12
        stats.append({"date": month.date().isoformat(), "units_deployed": int(burn * 30 * growth)})
    return stats


def _inventory_history(sku: str, component: Component) -> list[InventorySnapshot]:
    """Synthesize 60 days of history trending toward current_stock."""
    today = datetime.utcnow()
    out: list[InventorySnapshot] = []
    stock = component.current_stock + int(component.daily_burn_rate * 60 * 0.6)
    for i in range(60, -1, -1):
        stock = max(0, stock - int(component.daily_burn_rate * 0.8))
        out.append(InventorySnapshot(sku=sku, date=today - timedelta(days=i), stock=stock))
    return out


def load_scenario(sku: str) -> dict:
    component = _COMPONENTS[sku]
    return {
        "component": component,
        "inventory_history": _inventory_history(sku, component),
        "headlines": _HEADLINES.get(sku, []),
        "supplier_candidates": _SUPPLIERS.get(sku, []),
        "deployment_stats": _deployment_stats(sku, component.daily_burn_rate),
    }


def list_skus() -> list[str]:
    return list(_COMPONENTS.keys())
