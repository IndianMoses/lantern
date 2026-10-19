"""Deterministic coverage and payout engine.

Language models may *propose* a rule; only a rule that passed validation is
executed here, and every result carries the inputs, each intermediate step and
the conditions that remain unresolved. Nothing in this module estimates
probabilities, fills gaps with assumptions or labels an output as an approved
payout: results are illustrative readings of the stated terms.

Supported calculation methods (anything else is rejected, not guessed):

* ``limit_then_excess``  payable = min(loss, limit) − excess, floored at 0
* ``excess_then_limit``  payable = min(loss − excess, limit), floored at 0
* ``proportion``         payable = min(loss, limit) × percent / 100 − excess
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

CalculationMethod = Literal["limit_then_excess", "excess_then_limit", "proportion", "unsupported"]


class CoverageRule(BaseModel):
    """A typed rule derived from policy facts. ``interpretation_status`` must be
    ``validated`` before the rule may feed a calculation."""

    rule_id: str
    policy_id: str
    policy_number: str
    covered_event: str
    subject: str
    currency: str = Field(min_length=3, max_length=3)
    limit: float | None = None
    sub_limit: float | None = None
    excess: float | None = None
    percent: float | None = None
    conditions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    evidence_clause_ids: list[str] = Field(default_factory=list)
    evidence_fact_ids: list[str] = Field(default_factory=list)
    effective_from: date | None = None
    effective_to: date | None = None
    calculation_method: CalculationMethod = "limit_then_excess"
    interpretation_status: Literal["validated", "needs_review", "rejected"] = "needs_review"
    version: int = 1

    @field_validator("limit", "sub_limit", "excess", "percent")
    @classmethod
    def _non_negative(cls, v: float | None) -> float | None:
        if v is not None and v < 0:
            raise ValueError("amounts cannot be negative")
        return v


class UnsupportedCalculation(ValueError):
    pass


class RuleNotValidated(ValueError):
    pass


@dataclass
class CalculationResult:
    rule_id: str
    method: str
    currency: str
    inputs: dict
    steps: list[dict]
    payable: float | None
    uncovered: float | None
    unresolved_conditions: list[str] = field(default_factory=list)
    status: str = "illustrative"  # illustrative | unresolved | rejected
    label: dict = field(default_factory=lambda: {"en": "Illustrative reading of the stated terms, not an approved payout or a promise of settlement.", "zh": "按条款字面得出的示意金额，不是核准赔付，也不是任何承诺。"})

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "method": self.method, "currency": self.currency, "inputs": self.inputs, "steps": self.steps, "payable": self.payable, "uncovered": self.uncovered, "unresolved_conditions": self.unresolved_conditions, "status": self.status, "label": self.label}


def validate_rule(data: dict) -> tuple[CoverageRule | None, list[str]]:
    """Schema validation plus the checks a rule must pass before activation."""
    try:
        rule = CoverageRule.model_validate(data)
    except ValidationError as exc:
        return None, [f"schema: {e['loc']}: {e['msg']}" for e in exc.errors()]
    problems: list[str] = []
    if not rule.evidence_clause_ids:
        problems.append("no supporting clause")
    if rule.calculation_method == "unsupported":
        problems.append("calculation method not supported")
    if rule.limit is None and rule.sub_limit is None:
        problems.append("no limit stated")
    if rule.excess is None:
        problems.append("no excess stated")
    if rule.calculation_method == "proportion" and rule.percent is None:
        problems.append("percentage missing")
    return rule, problems


def calculate(rule: CoverageRule, eligible_loss: float, *, incident_date: date | None = None, currency: str | None = None) -> CalculationResult:
    """Apply a validated rule to an eligible loss. Raises on unsupported input
    rather than guessing; returns an ``unresolved`` result when conditions
    are outstanding."""
    if rule.interpretation_status != "validated":
        raise RuleNotValidated(f"rule {rule.rule_id} is {rule.interpretation_status}; it cannot be used for a calculation")
    if rule.calculation_method == "unsupported":
        raise UnsupportedCalculation(f"rule {rule.rule_id} has no supported calculation method")
    if currency and currency.upper() != rule.currency.upper():
        raise UnsupportedCalculation(f"currency mismatch: loss in {currency}, rule in {rule.currency}")
    if eligible_loss is None or eligible_loss < 0:
        raise UnsupportedCalculation("eligible loss must be a non negative number")

    steps: list[dict] = []
    unresolved: list[str] = list(rule.conditions)
    if incident_date and rule.effective_from and rule.effective_to and not (rule.effective_from <= incident_date <= rule.effective_to):
        unresolved.append(f"incident date {incident_date.isoformat()} is outside the policy period {rule.effective_from.isoformat()} to {rule.effective_to.isoformat()}")
    cap = min(x for x in (rule.limit, rule.sub_limit) if x is not None)
    excess = rule.excess or 0.0
    steps.append({"step": "eligible_loss", "value": round(eligible_loss, 2)})
    steps.append({"step": "applicable_cap", "value": cap, "detail": "lower of limit and sub limit"})
    if rule.calculation_method == "limit_then_excess":
        capped = min(eligible_loss, cap)
        steps.append({"step": "apply_cap", "value": round(capped, 2)})
        payable = max(0.0, capped - excess)
        steps.append({"step": "deduct_excess", "excess": excess, "value": round(payable, 2)})
    elif rule.calculation_method == "excess_then_limit":
        after_excess = max(0.0, eligible_loss - excess)
        steps.append({"step": "deduct_excess", "excess": excess, "value": round(after_excess, 2)})
        payable = min(after_excess, cap)
        steps.append({"step": "apply_cap", "value": round(payable, 2)})
    else:  # proportion
        capped = min(eligible_loss, cap)
        steps.append({"step": "apply_cap", "value": round(capped, 2)})
        prop = capped * (rule.percent or 0) / 100.0
        steps.append({"step": "apply_percentage", "percent": rule.percent, "value": round(prop, 2)})
        payable = max(0.0, prop - excess)
        steps.append({"step": "deduct_excess", "excess": excess, "value": round(payable, 2)})
    uncovered = round(max(0.0, eligible_loss - payable), 2)
    steps.append({"step": "uncovered_portion", "value": uncovered})
    status = "unresolved" if unresolved else "illustrative"
    return CalculationResult(rule_id=rule.rule_id, method=rule.calculation_method, currency=rule.currency, inputs={"eligible_loss": eligible_loss, "limit": rule.limit, "sub_limit": rule.sub_limit, "excess": rule.excess, "percent": rule.percent, "incident_date": incident_date.isoformat() if incident_date else None, "rule_version": rule.version}, steps=steps, payable=round(payable, 2), uncovered=uncovered, unresolved_conditions=unresolved, status=status)
