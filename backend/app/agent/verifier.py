"""Grounding verifier.

After the model produces its final text, every citation of the form
``[POLICY-NUMBER §ref]`` is checked against the set of clauses actually
retrieved during the run. Unknown citations are rewritten as unverified and
reported. Coverage claims with no verifiable citation get a caution banner.
This is a hard check in code; the prompt alone is not trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CITATION_RX = re.compile(r"\[([A-Z]{2,6}(?:-[A-Z0-9]+){1,5})\s*§\s*([0-9]{1,3}(?:\.[0-9]{1,3}){0,2})\]")
COVERAGE_ASSERTION_RX = re.compile(r"\b(is|are|isn't|is not|are not|will be|would be|not)\s+(covered|excluded|insured)\b|\b(covers|excludes|does not cover|doesn't cover|will pay|won't pay|will not pay)\b|(在保障范围|不在保障范围|会赔|不赔|不保|保障包括|承保)", re.I)


@dataclass
class VerificationResult:
    text: str
    valid: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    caution: bool = False

    @property
    def summary(self) -> dict:
        return {"citations_valid": len(self.valid), "citations_unverified": len(self.unverified), "unverified": self.unverified[:10], "caution_banner": self.caution}


def verify(text: str, retrieved_citations: set[str], *, language: str = "en", require_citation_for_coverage: bool = True) -> VerificationResult:
    valid: list[str] = []
    unverified: list[str] = []
    normalised = {c.replace(" ", "") for c in retrieved_citations}

    def repl(m: re.Match) -> str:
        cit = f"{m.group(1)} §{m.group(2)}"
        if cit.replace(" ", "") in normalised:
            valid.append(cit)
            return f"[{cit}]"
        unverified.append(cit)
        return f"[{cit} — {'unverified' if language != 'zh' else '未核实'}]"

    new_text = CITATION_RX.sub(repl, text or "")
    caution = False
    if require_citation_for_coverage and not valid and COVERAGE_ASSERTION_RX.search(new_text or ""):
        caution = True
        banner = (
            "⚠ I could not tie this answer to a specific clause of your wording, so treat it as general information rather than a reading of your policy. Ask me to search the wording or check with your insurer.\n\n"
            if language != "zh"
            else "⚠ 这个回答没有对应到你保单中的具体条款，请把它当作一般性信息而不是对你保单的解读。可以让我检索条款，或向保险公司确认。\n\n"
        )
        new_text = banner + new_text
    return VerificationResult(text=new_text, valid=sorted(set(valid)), unverified=sorted(set(unverified)), caution=caution)
