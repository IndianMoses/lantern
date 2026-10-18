"""Model provider abstraction.

The runner speaks one message format (Anthropic style content blocks) to
every provider:

    {"role": "user" | "assistant", "content": [block, ...]}
    block = {"type": "text", "text": ...}
          | {"type": "tool_use", "id": ..., "name": ..., "input": {...}}
          | {"type": "tool_result", "tool_use_id": ..., "content": "json text"}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ProviderTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    stop_reason: str = "end_turn"
    raw: Any = None


class ProviderError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str
    model: str | None

    def complete(self, *, system: str, messages: list[dict], tools: list[dict], max_tokens: int, context: dict | None = None) -> ProviderTurn: ...
