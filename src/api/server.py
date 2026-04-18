"""FastAPI service exposing orchestrator output to the Lovable dashboard."""

from __future__ import annotations

from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

from .. import coreweave_demo
from ..data_sources import list_skus
from ..db import MotherDuckStore
from ..models import OrchestratorQuery, ProcurementDecision
from ..orchestrator import Orchestrator


app = FastAPI(title="Procurement Orchestrator", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_orchestrator: Optional[Orchestrator] = None
_store: Optional[MotherDuckStore] = None


def orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


def store() -> MotherDuckStore:
    global _store
    if _store is None:
        _store = MotherDuckStore()
    return _store


class DecideResponse(BaseModel):
    decision: ProcurementDecision
    persisted_id: int


class CoreWeaveNewsIngestRequest(BaseModel):
    sku: Optional[str] = None
    days: int = Field(default=1, ge=1)
    max_records: int = Field(default=25, ge=1, le=250)
    query: Optional[str] = None


def _coreweave_error(exc: Exception) -> HTTPException:
    message = str(exc)
    if isinstance(exc, ValueError):
        return HTTPException(status_code=404, detail=message)
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=500, detail=message)
    return HTTPException(status_code=502, detail=message)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "skus": list_skus()}


@app.post("/decide", response_model=DecideResponse)
async def decide(body: OrchestratorQuery) -> DecideResponse:
    decision = await orchestrator().run(body.query, body.sku)
    row_id = store().save_decision(decision)
    return DecideResponse(decision=decision, persisted_id=row_id)


@app.get("/decisions")
def decisions(sku: Optional[str] = None, limit: int = 20) -> list[dict]:
    return store().recent_decisions(sku=sku, limit=limit)


@app.get("/risk/{sku}")
def risk_trendline(sku: str, limit: int = 100) -> list[dict]:
    return store().risk_trendline(sku, limit=limit)


@app.get("/coreweave/components")
def coreweave_components() -> list[dict]:
    try:
        return coreweave_demo.list_components()
    except Exception as exc:
        raise _coreweave_error(exc) from exc


@app.get("/coreweave/inventory/{sku}")
def coreweave_inventory(sku: str, limit: int = 90) -> dict:
    try:
        return coreweave_demo.get_inventory(sku, limit=limit)
    except Exception as exc:
        raise _coreweave_error(exc) from exc


@app.get("/coreweave/suppliers/{sku}")
def coreweave_suppliers(sku: str) -> list[dict]:
    try:
        return coreweave_demo.get_suppliers(sku)
    except Exception as exc:
        raise _coreweave_error(exc) from exc


@app.get("/coreweave/news-risk/{sku}")
def coreweave_news_risk(sku: str, limit: int = 10) -> dict:
    try:
        return coreweave_demo.get_news_risk(sku, limit=limit)
    except Exception as exc:
        raise _coreweave_error(exc) from exc


@app.get("/coreweave/articles/{sku}")
def coreweave_articles(sku: str, limit: int = 25, run_id: Optional[str] = None) -> list[dict]:
    try:
        return coreweave_demo.get_articles(sku, limit=limit, run_id=run_id)
    except Exception as exc:
        raise _coreweave_error(exc) from exc


@app.post("/coreweave/ingest-news")
async def coreweave_ingest_news(body: CoreWeaveNewsIngestRequest) -> dict:
    try:
        return await coreweave_demo.ingest_news_risk(
            sku=body.sku,
            days=body.days,
            max_records=body.max_records,
            query=body.query,
        )
    except Exception as exc:
        raise _coreweave_error(exc) from exc
