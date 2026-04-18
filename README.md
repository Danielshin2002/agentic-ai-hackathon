# Procurement Multi-Agent Orchestrator

A procurement and inventory control system built on a Claude-powered multi-agent
orchestrator. It smooths component ordering based on forecasted demand plus
historical and current news-driven risk.

Given a natural-language question like **"Should we buy more HBM3E now?"**, the
orchestrator dispatches four specialist agents, aggregates their findings into a
composite risk score, and emits a concrete procurement action plus the next
trigger condition to watch.

---

## Architecture

```
User Query
    ↓
Orchestrator (analyzes intent, routes to SKU)
    ↓
Inventory Agent  ── sequential, runs first
    ↓ (feeds context into the fan-out)
    ├─► News Agent        ┐
    ├─► Supplier Agent    ├─ parallel (asyncio.gather)
    └─► Forecast Agent    ┘
                 ↓
      Orchestrator aggregates
                 ↓
      Composite risk score (0–100)
                 ↓
      Procurement strategy output
      ├── Place immediate order: X units
      ├── Lock in alternative supplier: Y
      └── Monitor metric: Z (next trigger condition)
                 ↓
        ┌────────┴────────┐
        ↓                 ↓
   MotherDuck       Lovable dashboard
  (decisions +      (FastAPI REST → UI)
   risk trendline)
```

### Agents

| Agent     | Role                                                                 | Key output                                                     |
|-----------|----------------------------------------------------------------------|----------------------------------------------------------------|
| Inventory | Days of coverage vs safe threshold from current + historical stock. | `status`, `recommended_order_qty`                              |
| News      | Dynamic lookback window from lead time; VaR-style risk score.       | `risk_score` (0–100), `trend`, `supply_adjustment_factor`      |
| Supplier  | Scores alternatives on compat × timeline × capacity × geography.    | `candidates[]`, `top_alternative`, `activate/qualify/avoid`    |
| Forecast  | x-day demand with VaR-95 and confidence; YoY growth and shortfall.  | `expected_demand`, `var_95`, `yoy_growth_rate`, `shortfall_risk` |
| Orchestrator | Intent parsing, parallel dispatch, composite risk, action plan.   | `ProcurementDecision`                                          |

### Sequencing

1. **Inventory runs first** because downstream agents condition on the coverage
   status (the News Agent expands its window when coverage is thin; the Supplier
   Agent up-weights qualification timeline when inventory is critical).
2. **News, Supplier, Forecast run in parallel** via `asyncio.gather`.
3. **Aggregator** (a small Claude call) produces the final decision payload.

---

## Repo structure

```
agentic-ai-hackathon/
├── README.md
├── requirements.txt
├── .env.example
├── main.py                        # CLI entry point (one-shot run)
└── src/
    ├── __init__.py
    ├── models.py                  # Pydantic schemas (Component, reports, decision)
    ├── data_sources.py            # Demo component/news/supplier/forecast data
    ├── orchestrator.py            # Orchestrator + aggregator
    ├── agents/
    │   ├── __init__.py
    │   ├── base.py                # Claude wrapper: prompt caching + JSON parsing
    │   ├── inventory_agent.py     # Runs first (sequential)
    │   ├── news_agent.py          # VaR-style risk score
    │   ├── supplier_agent.py      # 4-dimension scoring
    │   └── forecast_agent.py      # Demand forecasting with VaR
    ├── db/
    │   ├── __init__.py
    │   └── motherduck.py          # MotherDuck / local DuckDB persistence
    └── api/
        ├── __init__.py
        └── server.py              # FastAPI REST for Lovable dashboard
```

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY (required), MOTHERDUCK_TOKEN (optional)
```

**Environment variables**

| Variable              | Required | Purpose                                                       |
|-----------------------|----------|---------------------------------------------------------------|
| `ANTHROPIC_API_KEY`   | yes      | Claude API credentials.                                       |
| `CLAUDE_MODEL`        | no       | Defaults to `claude-opus-4-7`.                                |
| `MOTHERDUCK_TOKEN`    | no       | Enables MotherDuck; falls back to local `.local.duckdb`.      |
| `MOTHERDUCK_DATABASE` | no       | MotherDuck database name, default `md:procurement`.           |
| `NEWS_API_KEY`        | no       | Reserved for swapping the stub headline feed with a live one. |

---

## Run

**One-shot CLI**

```bash
python main.py "Should we buy more HBM3E now?"
```

Prints the full `ProcurementDecision` JSON and persists to DuckDB/MotherDuck.

**REST server (for the Lovable dashboard)**

```bash
uvicorn src.api.server:app --reload --port 8000
```

Endpoints:

| Method | Path              | Purpose                                        |
|--------|-------------------|------------------------------------------------|
| GET    | `/health`         | Liveness check + list of known SKUs.           |
| POST   | `/decide`         | Body: `{query, sku?}` → runs orchestrator.     |
| GET    | `/decisions`      | Recent persisted decisions (optional `?sku=`). |
| GET    | `/risk/{sku}`     | Risk trendline points for charting.            |

Example:

```bash
curl -X POST http://localhost:8000/decide \
  -H 'Content-Type: application/json' \
  -d '{"query": "Should we buy more HBM3E now?"}'
```

---

## Output shape (abridged)

```json
{
  "query": "Should we buy more HBM3E now?",
  "sku": "HBM3E",
  "risk_score": 72.5,
  "immediate_order_qty": 4800,
  "alternative_supplier": "Samsung Electronics",
  "monitor_metric": "news.risk_score",
  "next_trigger_condition": "news.risk_score > 80 OR coverage < 25 days",
  "rationale": "...",
  "inventory": { "...": "..." },
  "news":      { "risk_score": 68, "trend": "rising", "...": "..." },
  "supplier":  { "top_alternative": { "...": "..." } },
  "forecast":  { "expected_demand": 3400, "var_95": 4200, "...": "..." }
}
```

---

## What to do next

1. **Swap the stubs** in `src/data_sources.py` for real integrations:
   - Inventory: ERP / warehouse snapshot query.
   - News: NewsAPI / GDELT / internal supply-chain feed.
   - Suppliers: procurement DB.
   - Deployment stats: BI warehouse.
2. **Point persistence at MotherDuck** by setting `MOTHERDUCK_TOKEN`. Schema is
   created automatically on first run.
3. **Wire the Lovable dashboard** at the three REST endpoints above. The
   `risk_trendline` endpoint is built for charting the VaR-style score over time.
4. **Add a scheduler** (cron / Temporal / Airflow) to re-run the orchestrator
   when a `next_trigger_condition` fires.
5. **Instrument prompt caching hits** — every agent already marks its system
   prompt with `cache_control`, so repeated calls within 5 minutes will hit the
   cache.

---

## Design notes

- **Why Inventory first?** It's the only agent whose output materially reshapes
  the others' prompts (window length, urgency weighting, forecast horizon bias).
- **Why asyncio.gather for the rest?** They have no cross-dependencies given
  the shared inventory context — parallelism ~3x reduces wall time.
- **Why a dedicated Aggregator prompt?** Forces Claude to reconcile conflicting
  signals (e.g. healthy inventory + high news risk) using explicit weights
  rather than hand-rolled arithmetic, while keeping the final payload schema
  strict.
- **Why DuckDB / MotherDuck?** Single code path for local demos and production
  analytics; the risk trendline is a natural fit for columnar storage.
