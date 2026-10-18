from __future__ import annotations

from ...config import settings
from .base import LLMProvider, ProviderError, ProviderTurn, ToolCall
from .deterministic import DeterministicProvider

_cache: dict[str, LLMProvider] = {}


def get_provider(name: str | None = None) -> LLMProvider:
    name = name or settings.effective_llm_provider
    if name in _cache:
        return _cache[name]
    if name == "anthropic":
        try:
            from .anthropic_provider import AnthropicProvider

            provider: LLMProvider = AnthropicProvider()
        except Exception as exc:  # missing key or SDK: degrade safely rather than failing every request
            import logging

            logging.getLogger("lantern").warning("Claude API provider unavailable (%s); using the deterministic provider", exc)
            provider = DeterministicProvider()
    else:
        provider = DeterministicProvider()
    _cache[name] = provider
    return provider


def reset_providers() -> None:
    _cache.clear()


__all__ = ["LLMProvider", "ProviderError", "ProviderTurn", "ToolCall", "DeterministicProvider", "get_provider", "reset_providers"]
