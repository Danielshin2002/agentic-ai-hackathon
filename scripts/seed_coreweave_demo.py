"""Seed synthetic CoreWeave-style procurement data into MotherDuck.

This data is intentionally simulated for demo/presentation use. It is not
CoreWeave operational data.
"""

from __future__ import annotations

import argparse
import math
import os
from datetime import UTC, datetime, timedelta

import duckdb
from dotenv import load_dotenv


INVENTORY_HISTORY_DAYS = 150
DEPLOYMENT_HISTORY_MONTHS = 30


COMPONENTS = [
    {
        "sku": "HBM3E",
        "component_name": "HBM3E 24GB memory stack",
        "category": "accelerator_memory",
        "current_stock": 12400,
        "daily_burn_rate": 310.0,
        "lead_time_days": 42,
        "safe_threshold_days": 45,
        "unit_cost_usd": 420.0,
        "coreweave_use_case": "GPU cluster memory supply for AI training capacity",
        "criticality": "high",
    },
    {
        "sku": "B200-SXM",
        "component_name": "NVIDIA B200 SXM accelerator",
        "category": "gpu",
        "current_stock": 820,
        "daily_burn_rate": 22.0,
        "lead_time_days": 105,
        "safe_threshold_days": 75,
        "unit_cost_usd": 38000.0,
        "coreweave_use_case": "Blackwell GPU node deployments",
        "criticality": "critical",
    },
    {
        "sku": "H100-SXM",
        "component_name": "NVIDIA H100 SXM accelerator",
        "category": "gpu",
        "current_stock": 1450,
        "daily_burn_rate": 18.0,
        "lead_time_days": 70,
        "safe_threshold_days": 60,
        "unit_cost_usd": 30000.0,
        "coreweave_use_case": "Existing HGX fleet expansion and replacement pool",
        "criticality": "high",
    },
    {
        "sku": "CX7-400G",
        "component_name": "NVIDIA ConnectX-7 400G adapter",
        "category": "networking",
        "current_stock": 6600,
        "daily_burn_rate": 155.0,
        "lead_time_days": 56,
        "safe_threshold_days": 50,
        "unit_cost_usd": 1450.0,
        "coreweave_use_case": "Leaf-spine GPU cluster networking",
        "criticality": "medium",
    },
    {
        "sku": "NVSWITCH-4",
        "component_name": "NVIDIA NVLink Switch module",
        "category": "networking",
        "current_stock": 410,
        "daily_burn_rate": 12.0,
        "lead_time_days": 84,
        "safe_threshold_days": 60,
        "unit_cost_usd": 8700.0,
        "coreweave_use_case": "HGX baseboard fabric and service spares",
        "criticality": "critical",
    },
    {
        "sku": "CDU-120KW",
        "component_name": "120kW liquid-cooling CDU",
        "category": "datacenter_infrastructure",
        "current_stock": 130,
        "daily_burn_rate": 4.2,
        "lead_time_days": 98,
        "safe_threshold_days": 65,
        "unit_cost_usd": 78000.0,
        "coreweave_use_case": "High-density liquid-cooled GPU racks",
        "criticality": "high",
    },
    {
        "sku": "GB200-NVL72",
        "component_name": "NVIDIA GB200 NVL72 rack assembly",
        "category": "rack_scale_system",
        "current_stock": 72,
        "daily_burn_rate": 2.4,
        "lead_time_days": 140,
        "safe_threshold_days": 90,
        "unit_cost_usd": 3100000.0,
        "coreweave_use_case": "Rack-scale Blackwell cluster expansion",
        "criticality": "critical",
    },
    {
        "sku": "800G-OSFP",
        "component_name": "800G OSFP optical transceiver",
        "category": "networking",
        "current_stock": 9200,
        "daily_burn_rate": 245.0,
        "lead_time_days": 63,
        "safe_threshold_days": 55,
        "unit_cost_usd": 820.0,
        "coreweave_use_case": "High-radix fabric links for dense GPU clusters",
        "criticality": "high",
    },
    {
        "sku": "EPYC-9755",
        "component_name": "AMD EPYC 9755 host CPU",
        "category": "server_cpu",
        "current_stock": 2600,
        "daily_burn_rate": 48.0,
        "lead_time_days": 49,
        "safe_threshold_days": 45,
        "unit_cost_usd": 11800.0,
        "coreweave_use_case": "GPU server host processors and control-plane nodes",
        "criticality": "medium",
    },
    {
        "sku": "4TB-NVME",
        "component_name": "4TB enterprise NVMe SSD",
        "category": "storage",
        "current_stock": 18600,
        "daily_burn_rate": 420.0,
        "lead_time_days": 35,
        "safe_threshold_days": 40,
        "unit_cost_usd": 390.0,
        "coreweave_use_case": "Local scratch storage for training and inference nodes",
        "criticality": "medium",
    },
]


SUPPLIER_CANDIDATES = [
    {
        "sku": "HBM3E",
        "supplier_name": "Samsung Electronics",
        "supplier_role": "alternate",
        "technical_compat": 0.92,
        "qualification_timeline_days": 90,
        "available_capacity_units": 5200,
        "geographic_region": "South Korea",
        "composite_score": 0.79,
        "recommendation": "activate",
        "notes": "Strong technical fit; qualification risk remains around thermal profile and customer allocation.",
    },
    {
        "sku": "HBM3E",
        "supplier_name": "Micron",
        "supplier_role": "alternate",
        "technical_compat": 0.86,
        "qualification_timeline_days": 120,
        "available_capacity_units": 3100,
        "geographic_region": "USA/Taiwan",
        "composite_score": 0.69,
        "recommendation": "qualify",
        "notes": "Useful geography diversification; likely BOM and validation work required.",
    },
    {
        "sku": "B200-SXM",
        "supplier_name": "NVIDIA allocation channel A",
        "supplier_role": "primary",
        "technical_compat": 1.0,
        "qualification_timeline_days": 0,
        "available_capacity_units": 620,
        "geographic_region": "USA/Taiwan",
        "composite_score": 0.83,
        "recommendation": "activate",
        "notes": "Highest compatibility; capacity constrained by launch allocation and packaging availability.",
    },
    {
        "sku": "B200-SXM",
        "supplier_name": "NVIDIA allocation channel B",
        "supplier_role": "secondary",
        "technical_compat": 1.0,
        "qualification_timeline_days": 14,
        "available_capacity_units": 260,
        "geographic_region": "USA",
        "composite_score": 0.74,
        "recommendation": "activate",
        "notes": "Good emergency buffer if commercial terms are locked early.",
    },
    {
        "sku": "H100-SXM",
        "supplier_name": "NVIDIA certified distributor",
        "supplier_role": "primary",
        "technical_compat": 1.0,
        "qualification_timeline_days": 0,
        "available_capacity_units": 780,
        "geographic_region": "USA/Taiwan",
        "composite_score": 0.81,
        "recommendation": "activate",
        "notes": "Mature supply path; watch for service-spares competition with installed fleet.",
    },
    {
        "sku": "H100-SXM",
        "supplier_name": "Cloud marketplace resale pool",
        "supplier_role": "contingency",
        "technical_compat": 0.95,
        "qualification_timeline_days": 30,
        "available_capacity_units": 210,
        "geographic_region": "USA/EU",
        "composite_score": 0.58,
        "recommendation": "qualify",
        "notes": "Fastest contingency source, but variable warranty and provenance risk.",
    },
    {
        "sku": "CX7-400G",
        "supplier_name": "NVIDIA Networking",
        "supplier_role": "primary",
        "technical_compat": 1.0,
        "qualification_timeline_days": 0,
        "available_capacity_units": 9200,
        "geographic_region": "Israel/Taiwan",
        "composite_score": 0.88,
        "recommendation": "activate",
        "notes": "Primary validated adapter; capacity looks adequate if lead-time buffers are respected.",
    },
    {
        "sku": "CX7-400G",
        "supplier_name": "Broadcom 400G NIC option",
        "supplier_role": "alternate",
        "technical_compat": 0.72,
        "qualification_timeline_days": 150,
        "available_capacity_units": 4800,
        "geographic_region": "USA/Taiwan",
        "composite_score": 0.52,
        "recommendation": "qualify",
        "notes": "Potential fallback for non-NVLink fabrics; driver and telemetry integrations need validation.",
    },
    {
        "sku": "NVSWITCH-4",
        "supplier_name": "NVIDIA HGX service channel",
        "supplier_role": "primary",
        "technical_compat": 1.0,
        "qualification_timeline_days": 0,
        "available_capacity_units": 220,
        "geographic_region": "USA/Taiwan",
        "composite_score": 0.76,
        "recommendation": "activate",
        "notes": "Validated only-source path; buffer is thin relative to growth and repair demand.",
    },
    {
        "sku": "NVSWITCH-4",
        "supplier_name": "OEM board-level repair partner",
        "supplier_role": "contingency",
        "technical_compat": 0.68,
        "qualification_timeline_days": 110,
        "available_capacity_units": 90,
        "geographic_region": "USA",
        "composite_score": 0.43,
        "recommendation": "qualify",
        "notes": "Repair/rework route only; not a true new-build substitute.",
    },
    {
        "sku": "CDU-120KW",
        "supplier_name": "CoolIT Systems",
        "supplier_role": "primary",
        "technical_compat": 0.94,
        "qualification_timeline_days": 45,
        "available_capacity_units": 86,
        "geographic_region": "North America",
        "composite_score": 0.71,
        "recommendation": "activate",
        "notes": "Strong rack integration fit; capacity constrained by pump and heat-exchanger subcomponents.",
    },
    {
        "sku": "CDU-120KW",
        "supplier_name": "Vertiv liquid cooling",
        "supplier_role": "alternate",
        "technical_compat": 0.82,
        "qualification_timeline_days": 95,
        "available_capacity_units": 140,
        "geographic_region": "USA/EU",
        "composite_score": 0.64,
        "recommendation": "qualify",
        "notes": "Good infrastructure diversification; facility commissioning checklist differs.",
    },
    {
        "sku": "GB200-NVL72",
        "supplier_name": "NVIDIA direct rack allocation",
        "supplier_role": "primary",
        "technical_compat": 1.0,
        "qualification_timeline_days": 0,
        "available_capacity_units": 34,
        "geographic_region": "USA/Taiwan",
        "composite_score": 0.78,
        "recommendation": "activate",
        "notes": "Only validated new-build source; availability depends on rack-scale allocation timing.",
    },
    {
        "sku": "GB200-NVL72",
        "supplier_name": "OEM integration reserve pool",
        "supplier_role": "contingency",
        "technical_compat": 0.91,
        "qualification_timeline_days": 45,
        "available_capacity_units": 12,
        "geographic_region": "USA/Mexico",
        "composite_score": 0.59,
        "recommendation": "qualify",
        "notes": "Useful for schedule rescue, but integration windows and rack acceptance tests are tighter.",
    },
    {
        "sku": "800G-OSFP",
        "supplier_name": "Coherent optics",
        "supplier_role": "primary",
        "technical_compat": 0.96,
        "qualification_timeline_days": 21,
        "available_capacity_units": 6800,
        "geographic_region": "USA/Thailand",
        "composite_score": 0.82,
        "recommendation": "activate",
        "notes": "Strong fit for short-reach data-center optics; watch packaging capacity and lead times.",
    },
    {
        "sku": "800G-OSFP",
        "supplier_name": "Lumentum optics",
        "supplier_role": "alternate",
        "technical_compat": 0.89,
        "qualification_timeline_days": 60,
        "available_capacity_units": 4200,
        "geographic_region": "USA/Asia",
        "composite_score": 0.68,
        "recommendation": "qualify",
        "notes": "Good second source for 800G links; firmware compatibility testing still required.",
    },
    {
        "sku": "EPYC-9755",
        "supplier_name": "AMD enterprise channel",
        "supplier_role": "primary",
        "technical_compat": 1.0,
        "qualification_timeline_days": 0,
        "available_capacity_units": 1800,
        "geographic_region": "USA/Taiwan",
        "composite_score": 0.84,
        "recommendation": "activate",
        "notes": "Validated host CPU path; watch substrate allocation during server platform ramps.",
    },
    {
        "sku": "EPYC-9755",
        "supplier_name": "Tier-1 server OEM buffer",
        "supplier_role": "secondary",
        "technical_compat": 0.98,
        "qualification_timeline_days": 14,
        "available_capacity_units": 520,
        "geographic_region": "USA",
        "composite_score": 0.71,
        "recommendation": "activate",
        "notes": "Smaller buffer but fast to deploy for validated server designs.",
    },
    {
        "sku": "4TB-NVME",
        "supplier_name": "Samsung Semiconductor",
        "supplier_role": "primary",
        "technical_compat": 0.97,
        "qualification_timeline_days": 14,
        "available_capacity_units": 16000,
        "geographic_region": "South Korea/USA",
        "composite_score": 0.86,
        "recommendation": "activate",
        "notes": "High-volume supply and good endurance match for local scratch workloads.",
    },
    {
        "sku": "4TB-NVME",
        "supplier_name": "Solidigm enterprise SSD",
        "supplier_role": "alternate",
        "technical_compat": 0.91,
        "qualification_timeline_days": 45,
        "available_capacity_units": 9800,
        "geographic_region": "USA/Asia",
        "composite_score": 0.73,
        "recommendation": "qualify",
        "notes": "Good capacity diversification; thermal and firmware telemetry need platform validation.",
    },
]


def _connect() -> duckdb.DuckDBPyConnection:
    load_dotenv()
    token = os.getenv("MOTHERDUCK_TOKEN")
    database = os.getenv("MOTHERDUCK_DATABASE", "md:beaver")
    if not token:
        raise RuntimeError("MOTHERDUCK_TOKEN is not set; refusing to seed local fallback by accident")
    os.environ["motherduck_token"] = token
    return duckdb.connect(database)


def _inventory_rows(now: datetime) -> list[tuple]:
    rows = []
    for component_index, component in enumerate(COMPONENTS):
        sku = component["sku"]
        current_stock = component["current_stock"]
        burn = component["daily_burn_rate"]
        lead = component["lead_time_days"]
        region = "US-East" if component_index % 2 == 0 else "US-Central"
        facility = f"CWV-DEMO-{component_index + 1:02d}"
        start_stock = int(current_stock + burn * INVENTORY_HISTORY_DAYS * 0.72)

        for days_ago in range(INVENTORY_HISTORY_DAYS, -1, -1):
            observed_at = now - timedelta(days=days_ago)
            days_elapsed = INVENTORY_HISTORY_DAYS - days_ago
            seasonal_noise = int(math.sin(days_elapsed / 5.0 + component_index) * burn * 0.45)
            replenishment = int(burn * 12) if days_elapsed in {28, 57, 78, 116, 142} else 0
            allocated_units = max(0, int(burn * (0.65 + (component_index % 3) * 0.08)))
            inbound_units = int(burn * lead * 0.18) if days_ago <= lead else int(burn * lead * 0.08)
            stock = max(
                0,
                start_stock - int(days_elapsed * burn * 0.72) + seasonal_noise + replenishment,
            )
            if days_ago == 0:
                stock = current_stock

            rows.append(
                (
                    sku,
                    observed_at,
                    stock,
                    inbound_units,
                    allocated_units,
                    max(0, int(stock * 0.14)),
                    region,
                    facility,
                    "synthetic_coreweave_demo",
                    True,
                )
            )
    return rows


def _deployment_rows(now: datetime) -> list[tuple]:
    rows = []
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for component_index, component in enumerate(COMPONENTS):
        burn = component["daily_burn_rate"]
        for months_ago in range(DEPLOYMENT_HISTORY_MONTHS, 0, -1):
            observed_month = month_start - timedelta(days=30 * months_ago)
            growth_factor = 1.0 + 0.38 * (DEPLOYMENT_HISTORY_MONTHS - months_ago) / (
                DEPLOYMENT_HISTORY_MONTHS - 1
            )
            cyclic = 1.0 + 0.08 * math.sin(months_ago + component_index)
            units_deployed = int(burn * 30 * growth_factor * cyclic)
            rows.append(
                (
                    component["sku"],
                    observed_month.date().isoformat(),
                    units_deployed,
                    "synthetic_coreweave_demo",
                    True,
                )
            )
    return rows


def seed() -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    con = _connect()

    con.execute("CREATE SCHEMA IF NOT EXISTS demo_coreweave")
    con.execute(
        """
        CREATE OR REPLACE TABLE demo_coreweave.components (
            sku TEXT PRIMARY KEY,
            component_name TEXT,
            category TEXT,
            current_stock INTEGER,
            daily_burn_rate DOUBLE,
            lead_time_days INTEGER,
            safe_threshold_days INTEGER,
            unit_cost_usd DOUBLE,
            coreweave_use_case TEXT,
            criticality TEXT,
            data_label TEXT,
            is_synthetic BOOLEAN,
            updated_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE demo_coreweave.inventory_snapshots (
            sku TEXT,
            observed_at TIMESTAMP,
            stock INTEGER,
            inbound_units INTEGER,
            allocated_units INTEGER,
            reserved_units INTEGER,
            region TEXT,
            facility TEXT,
            data_label TEXT,
            is_synthetic BOOLEAN
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE demo_coreweave.supplier_candidates (
            sku TEXT,
            supplier_name TEXT,
            supplier_role TEXT,
            technical_compat DOUBLE,
            qualification_timeline_days INTEGER,
            available_capacity_units INTEGER,
            geographic_region TEXT,
            composite_score DOUBLE,
            recommendation TEXT,
            notes TEXT,
            data_label TEXT,
            is_synthetic BOOLEAN,
            updated_at TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TABLE demo_coreweave.deployment_stats (
            sku TEXT,
            observed_month DATE,
            units_deployed INTEGER,
            data_label TEXT,
            is_synthetic BOOLEAN
        )
        """
    )

    component_rows = [
        (
            c["sku"],
            c["component_name"],
            c["category"],
            c["current_stock"],
            c["daily_burn_rate"],
            c["lead_time_days"],
            c["safe_threshold_days"],
            c["unit_cost_usd"],
            c["coreweave_use_case"],
            c["criticality"],
            "synthetic_coreweave_demo",
            True,
            now,
        )
        for c in COMPONENTS
    ]
    supplier_rows = [
        (
            s["sku"],
            s["supplier_name"],
            s["supplier_role"],
            s["technical_compat"],
            s["qualification_timeline_days"],
            s["available_capacity_units"],
            s["geographic_region"],
            s["composite_score"],
            s["recommendation"],
            s["notes"],
            "synthetic_coreweave_demo",
            True,
            now,
        )
        for s in SUPPLIER_CANDIDATES
    ]

    con.executemany(
        "INSERT INTO demo_coreweave.components VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        component_rows,
    )
    con.executemany(
        "INSERT INTO demo_coreweave.inventory_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        _inventory_rows(now),
    )
    con.executemany(
        "INSERT INTO demo_coreweave.supplier_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        supplier_rows,
    )
    con.executemany(
        "INSERT INTO demo_coreweave.deployment_stats VALUES (?, ?, ?, ?, ?)",
        _deployment_rows(now),
    )

    counts = con.execute(
        """
        SELECT 'components' AS table_name, count(*) AS row_count FROM demo_coreweave.components
        UNION ALL
        SELECT 'inventory_snapshots', count(*) FROM demo_coreweave.inventory_snapshots
        UNION ALL
        SELECT 'supplier_candidates', count(*) FROM demo_coreweave.supplier_candidates
        UNION ALL
        SELECT 'deployment_stats', count(*) FROM demo_coreweave.deployment_stats
        ORDER BY table_name
        """
    ).fetchall()

    print("Seeded synthetic CoreWeave demo data into demo_coreweave:")
    for table_name, row_count in counts:
        print(f"- {table_name}: {row_count}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    seed()


if __name__ == "__main__":
    main()
