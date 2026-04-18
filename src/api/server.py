"""FastAPI service exposing orchestrator output to the Lovable dashboard."""

from __future__ import annotations

from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()

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
