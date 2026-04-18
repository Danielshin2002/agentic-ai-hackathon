"""CLI entry point: run the orchestrator once and print the decision.

Usage:
    python main.py "Should we buy more HBM3E now?"
"""

from __future__ import annotations

import asyncio
import sys

from src.db import MotherDuckStore
from src.orchestrator import Orchestrator


async def _main(query: str) -> None:
    orch = Orchestrator()
    decision = await orch.run(query)
    store = MotherDuckStore()
    row_id = store.save_decision(decision)
    print(decision.model_dump_json(indent=2))
    print(f"\npersisted id={row_id} (mode={store.mode})")


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) or "Should we buy more HBM3E now?"
    asyncio.run(_main(query))
