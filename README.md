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
├── scripts/
│   ├── seed_coreweave_demo.py      # Seeds synthetic CoreWeave demo data
│   └── ingest_coreweave_gdelt_news.py # Pulls GDELT news + persists risk rating
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
| `MOTHERDUCK_TOKEN`    | yes for CoreWeave demo | Enables MotherDuck persistence. The base app falls back to local `.local.duckdb` when unset. |
| `MOTHERDUCK_DATABASE` | no       | MotherDuck database name, default `md:procurement`; use `md:beaver` for the current demo DB. |
| `NEWS_API_KEY`        | no       | Not needed for GDELT; reserved for swapping in another news provider. |

Example `.env` for the current CoreWeave demo:

```bash
ANTHROPIC_API_KEY=...
CLAUDE_MODEL=claude-opus-4-7
MOTHERDUCK_TOKEN=...
MOTHERDUCK_DATABASE=md:beaver
NEWS_API_KEY=
```

Do not commit `.env`; it is intentionally ignored by git.

---

## Run

### CoreWeave demo data

The CoreWeave presentation path uses synthetic inventory, supplier, and demand
data in MotherDuck. It writes to the `demo_coreweave` schema and marks rows with
`is_synthetic = true`.

Seed or refresh the demo tables:

```bash
.venv/bin/python scripts/seed_coreweave_demo.py
```

Created tables:

| Table | Purpose |
|-------|---------|
| `demo_coreweave.components` | Simulated CoreWeave-relevant parts such as `HBM3E`, `B200-SXM`, `H100-SXM`, `CX7-400G`, `NVSWITCH-4`, and `CDU-120KW`. |
| `demo_coreweave.inventory_snapshots` | 90-day synthetic stock history, latest stock, inbound units, allocated units, and facility/region. |
| `demo_coreweave.supplier_candidates` | Simulated supplier alternatives with compatibility, capacity, timeline, geography, and recommendation. |
| `demo_coreweave.deployment_stats` | 18 months of synthetic deployment/demand history. |

### Live GDELT news risk

Fetch recent GDELT news for a CoreWeave demo component, score it with the
`NewsAgent`, and persist the rating to MotherDuck:

```bash
.venv/bin/python scripts/ingest_coreweave_gdelt_news.py
```

Default behavior:

- selects the component with the lowest latest days of coverage from
  `demo_coreweave.inventory_snapshots`
- fetches the past 1 day of GDELT news
- asks Claude to produce a VaR-style `risk_score`
- writes raw articles to `demo_coreweave.gdelt_articles`
- writes the scored result to `demo_coreweave.news_risk_ratings`

Useful flags:

```bash
# Score a specific component.
.venv/bin/python scripts/ingest_coreweave_gdelt_news.py --sku HBM3E

# Use a wider lookback window.
.venv/bin/python scripts/ingest_coreweave_gdelt_news.py --sku HBM3E --days 7

# Limit article count.
.venv/bin/python scripts/ingest_coreweave_gdelt_news.py --sku HBM3E --days 1 --max-records 10

# Override the generated GDELT query.
.venv/bin/python scripts/ingest_coreweave_gdelt_news.py \
  --sku HBM3E \
  --query '("HBM3E" OR "SK Hynix") (shortage OR capacity OR delay)'
```

GDELT is a public API and does not require `NEWS_API_KEY`. It rate-limits
requests, so the ingestion script includes retry/backoff handling; avoid
hammering it in a tight loop during demos.

Example successful output:

```text
run_id=HBM3E-20260418181608
selected_sku=HBM3E
inventory_status=critical
days_of_coverage=40.0
gdelt_articles=14
risk_score=68.5
trend=rising
supply_adjustment_factor=1.55
persisted=demo_coreweave.news_risk_ratings
```

Quick MotherDuck checks:

```sql
SELECT *
FROM demo_coreweave.news_risk_ratings
ORDER BY created_at DESC
LIMIT 5;

SELECT sku, count(*) AS articles
FROM demo_coreweave.gdelt_articles
GROUP BY sku
ORDER BY sku;
```

**One-shot CLI**

```bash
.venv/bin/python main.py "Should we buy more HBM3E now?"
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

CoreWeave demo endpoints for Lovable:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/coreweave/components` | Component list with latest inventory and latest news-risk summary. |
| GET | `/coreweave/inventory/{sku}` | Latest inventory summary plus stock history. Optional `?limit=90`. |
| GET | `/coreweave/suppliers/{sku}` | Supplier candidates ranked by composite score. |
| GET | `/coreweave/news-risk/{sku}` | Latest and recent news-risk ratings. Optional `?limit=10`. |
| GET | `/coreweave/articles/{sku}` | Recent persisted GDELT articles. Optional `?limit=25&run_id=...`. |
| POST | `/coreweave/ingest-news` | Refresh GDELT news, score with `NewsAgent`, and persist the rating. |

Example:

```bash
curl -X POST http://localhost:8000/decide \
  -H 'Content-Type: application/json' \
  -d '{"query": "Should we buy more HBM3E now?"}'
```

CoreWeave news refresh example:

```bash
curl -X POST http://localhost:8000/coreweave/ingest-news \
  -H 'Content-Type: application/json' \
  -d '{"sku": "HBM3E", "days": 1, "max_records": 10}'
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

1. **Wire the orchestrator to MotherDuck demo tables** instead of the current
   stubs in `src/data_sources.py`, so `/decide` can use
   `demo_coreweave.components`, `inventory_snapshots`, `supplier_candidates`,
   and `deployment_stats` directly.
2. **Swap synthetic data for real integrations** when available:
   - Inventory: ERP / warehouse snapshot query.
   - News: GDELT / NewsAPI / internal supply-chain feed.
   - Suppliers: procurement DB.
   - Deployment stats: BI warehouse.
3. **Point persistence at MotherDuck** by setting `MOTHERDUCK_TOKEN`. The
   decision schema is created automatically on first run; the CoreWeave demo
   scripts create their own `demo_coreweave` tables.
4. **Wire the Lovable dashboard** at the three REST endpoints above. The
   `risk_trendline` endpoint is built for charting the VaR-style score over time.
5. **Add a scheduler** (cron / Temporal / Airflow) to re-run the orchestrator
   when a `next_trigger_condition` fires.
6. **Instrument prompt caching hits** — every agent already marks its system
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
