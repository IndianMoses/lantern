"""Claude API provider (Messages API with tool use)."""
from __future__ import annotations

from typing import Any

from ...config import settings
from .base import ProviderError, ProviderTurn, ToolCall


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("the 'anthropic' package is not installed") from exc
        import anthropic

        self.model = model or settings.llm_model
        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=2)

    def complete(self, *, system: str, messages: list[dict], tools: list[dict], max_tokens: int, context: dict | None = None) -> ProviderTurn:
        import anthropic

        kwargs: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages}
        if tools:
            kwargs["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]} for t in tools]
        try:
            resp = self._client.messages.create(**kwargs)
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"model provider error {exc.status_code}: {getattr(exc, 'message', str(exc))[:200]}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError("could not reach the model provider") from exc
        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
        usage = {"input_tokens": getattr(resp.usage, "input_tokens", 0), "output_tokens": getattr(resp.usage, "output_tokens", 0)}
        return ProviderTurn(text="\n".join(text_parts).strip() or None, tool_calls=calls, usage=usage, stop_reason=resp.stop_reason or "end_turn", raw=resp)

    @staticmethod
    def assistant_content(turn: ProviderTurn) -> list[dict]:
        blocks: list[dict] = []
        if turn.text:
            blocks.append({"type": "text", "text": turn.text})
        for c in turn.tool_calls:
            blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
        return blocks
