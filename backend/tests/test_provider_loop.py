"""The model driven loop, exercised with a stubbed Claude client so the tool
calling plumbing, grounding verification and fallback are covered offline."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent import runner
from app.agent.providers import ProviderError
from app.agent.providers.anthropic_provider import AnthropicProvider


class _Block(SimpleNamespace):
    pass


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(content=step, stop_reason="tool_use" if any(b.type == "tool_use" for b in step) else "end_turn", usage=SimpleNamespace(input_tokens=100, output_tokens=50))


def make_provider(script):
    p = AnthropicProvider.__new__(AnthropicProvider)
    p.model = "fake-model"
    p._client = SimpleNamespace(messages=FakeMessages(script))
    return p


def test_model_loop_executes_tools_and_verifies_citations(alex, monkeypatch):
    script = [
        [_Block(type="text", text="Let me check the wording."), _Block(type="tool_use", id="t1", name="search_policy_wording", input={"query": "bike covered at work", "top_k": 4})],
        [_Block(type="text", text="Your bike is covered away from home up to £350 [NGM-HC-204481 §2.6], and theft requires a lock [NGM-HC-204481 §3.3]. There is also a clause about drones [NGM-HC-204481 §9.9].")],
    ]
    provider = make_provider(script)
    monkeypatch.setattr(runner, "get_provider", lambda: provider)
    out = alex.chat("Is my bike covered at work?")
    run = out["run"]
    assert run["provider"] == "anthropic" and run["model"] == "fake-model"
    assert [t["tool"] for t in run["tool_executions"]] == ["search_policy_wording"]
    assert run["grounding"]["citations_valid"] == 2 and run["grounding"]["unverified"] == ["NGM-HC-204481 §9.9"]
    assert "§9.9 — unverified" in out["message"]["content"]
    assert any(b["type"] == "warning" and b["code"] == "unverified_citations" for b in out["message"]["blocks"])
    # the tool result was fed back to the model as a parseable JSON tool_result block
    second_call = provider._client.messages.calls[1]
    last_user = second_call["messages"][-1]
    assert last_user["role"] == "user" and last_user["content"][0]["type"] == "tool_result"
    assert "tools" in second_call and all("input_schema" in t for t in second_call["tools"])


def test_model_cannot_call_human_only_or_unknown_tools(alex, monkeypatch):
    script = [
        [_Block(type="tool_use", id="t1", name="record_decision", input={"claim_id": "x", "outcome": "approved"})],
        [_Block(type="tool_use", id="t2", name="insurer_get_claim", input={"claim_id": "x"})],
        [_Block(type="text", text="I was not able to do that.")],
    ]
    provider = make_provider(script)
    monkeypatch.setattr(runner, "get_provider", lambda: provider)
    out = alex.chat("Approve my claim")
    statuses = [(t["tool"], t["status"]) for t in out["run"]["tool_executions"]]
    assert statuses == [("record_decision", "denied"), ("insurer_get_claim", "denied")]


def test_coverage_claim_without_citation_gets_caution_banner(alex, monkeypatch):
    provider = make_provider([[_Block(type="text", text="Yes, your laptop is covered everywhere you take it.")]])
    monkeypatch.setattr(runner, "get_provider", lambda: provider)
    out = alex.chat("Is my laptop covered in cafés?")
    assert out["message"]["content"].startswith("⚠") and out["run"]["grounding"]["caution_banner"] is True


def test_provider_failure_falls_back_to_offline_provider(alex, monkeypatch):
    provider = make_provider([ProviderError("model provider error 529")])
    monkeypatch.setattr(runner, "get_provider", lambda: provider)
    out = alex.chat("Summarise all my policies")
    assert out["run"]["provider"] == "anthropic->deterministic" and out["run"]["status"] == "completed"
    assert "NGM-HC-204481" in out["message"]["content"]


@pytest.mark.parametrize("name", ["submit_claim", "send_communication", "respond_to_information_request"])
def test_tier_two_tools_exposed_to_model_are_approval_gated(name):
    from app.agent.tools import REGISTRY

    assert REGISTRY[name].tier == 2
