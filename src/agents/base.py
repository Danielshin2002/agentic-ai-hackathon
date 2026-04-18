"""Base agent wrapping the Anthropic SDK with prompt caching + JSON parsing."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Type, TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-7")


class BaseAgent:
    """Thin Claude wrapper: cached system prompt + structured JSON output."""

    name: str = "base"
    system_prompt: str = ""

    def __init__(self, client: AsyncAnthropic, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model

    async def _complete_json(
        self,
        user_content: str,
        schema: Type[T],
        max_tokens: int = 1500,
    ) -> T:
        """Call Claude, extract JSON, validate against schema."""
        msg = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        payload = _extract_json(text)
        try:
            return schema.model_validate(payload)
        except ValidationError as e:
            raise ValueError(
                f"{self.name} returned invalid payload for {schema.__name__}: {e}\n---\n{text}"
            ) from e


def _extract_json(text: str) -> Any:
    """Find the first JSON object in a Claude response (handles ```json fences)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON object found in response: {text[:200]!r}")
    return json.loads(text[start : end + 1])
