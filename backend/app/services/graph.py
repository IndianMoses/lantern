"""Coverage knowledge graph, stored in ordinary tables (graph_nodes,
graph_edges) and rebuilt for every analysis.

Nodes: customer, asset, need (risk), policy (with version and effective
dates), clause (coverage module, exclusion, limit, excess, waiting period,
condition), finding, scenario, action, claim. Edges carry provenance (the
fact, clause or rule that produced them) and whether they are verified, so an
inferred relationship is never shown as a verified fact.

The graph answers: which assets or needs are exposed, which policies and
clauses address each one, which exclusions may apply, where cover is
insufficient, which findings are verified and which are uncertain, what next
action could address each gap, and which policy version a claim depends on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import Claim, CustomerProfile, GraphEdge, GraphNode, Policy, PolicyClause, PolicyFact, User

CLAUSE_NODE_TYPES = {"coverage": "coverage_module", "exclusion": "exclusion", "limit": "limit", "condition": "condition", "definition": "definition", "claims_procedure": "condition", "general": "condition"}


@dataclass
class Graph:
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)

    def node(self, key: str, node_type: str, label: dict, *, status: str | None = None, ref_type: str | None = None, ref_id: str | None = None, **data) -> str:
        if key not in self.nodes:
            self.nodes[key] = {"key": key, "node_type": node_type, "label": label, "status": status, "ref_type": ref_type, "ref_id": ref_id, "data": data}
        else:
            n = self.nodes[key]
            if status:
                n["status"] = status
            n["data"].update(data)
        return key

    def edge(self, source: str, target: str, relation: str, *, source_ref: str, kind: str = "stated", verified: bool = False) -> None:
        if source not in self.nodes or target not in self.nodes:
            return
        if any(e["source_key"] == source and e["target_key"] == target and e["relation"] == relation for e in self.edges):
            return
        self.edges.append({"source_key": source, "target_key": target, "relation": relation, "provenance": {"kind": kind, "source": source_ref}, "verified": verified})


def build(user: User, profile: CustomerProfile, policies: list[tuple[Policy, list[PolicyClause]]], facts: dict[str, list[PolicyFact]], findings: list[dict], claims: list[Claim], *, contradictions: dict[str, list[dict]] | None = None) -> Graph:
    g = Graph()
    g.node("customer", "customer", {"en": user.full_name, "zh": user.full_name}, ref_type="user", ref_id=user.id)
    asset_keys: dict[int, str] = {}
    for i, a in enumerate(profile.assets or []):
        k = g.node(f"asset:{i}", "asset", {"en": a.get("label") or a.get("type", "asset"), "zh": a.get("label") or a.get("type", "asset")}, asset_type=a.get("type"), value=a.get("value"), currency=profile.currency)
        asset_keys[i] = k
        g.edge("customer", k, "OWNS_ASSET", source_ref="profile", kind="stated", verified=True)
    clause_index: dict[str, tuple[Policy, PolicyClause]] = {}
    by_number: dict[str, Policy] = {}
    for p, clauses in policies:
        by_number[p.policy_number] = p
        pk = g.node(f"policy:{p.id}", "policy", {"en": f"{p.provider_name} {p.product_name}", "zh": f"{p.provider_name}{p.product_name}"}, status=p.status, ref_type="policy", ref_id=p.id, number=p.policy_number, insurance_type=p.insurance_type, version=p.version, effective_from=p.start_date.isoformat(), effective_to=p.end_date.isoformat(), excess=p.excess_amount, currency=p.currency)
        g.edge("customer", pk, "OWNS_POLICY", source_ref=f"policy:{p.id}", kind="stated", verified=True)
        if p.supersedes_policy_id:
            g.node(f"policy:{p.supersedes_policy_id}", "policy", {"en": "Previous version", "zh": "上一版本"}, status="superseded", ref_type="policy", ref_id=p.supersedes_policy_id)
            g.edge(pk, f"policy:{p.supersedes_policy_id}", "SUPERSEDES", source_ref=f"policy:{p.id}", kind="stated", verified=True)
        for c in clauses:
            clause_index[c.id] = (p, c)
        for end in p.endorsements or []:
            ek = g.node(f"endorsement:{p.id}:{end.get('ref')}", "endorsement", {"en": end.get("title", "Endorsement"), "zh": end.get("title", "批单")}, ref_type="policy", ref_id=p.id, overrides=end.get("overrides_clause_ref"))
            g.edge(pk, ek, "CONTAINS_CLAUSE", source_ref=f"policy:{p.id}", kind="stated", verified=True)
    # policies covering assets, by type match (stated by the product type, verified through findings below)
    for i, a in enumerate(profile.assets or []):
        for p, _ in policies:
            if p.status != "active":
                continue
            if (a.get("type") in {"home", "contents", "jewellery", "laptop", "bicycle"} and p.insurance_type in {"home", "renters"}) or (a.get("type") == "car" and p.insurance_type == "motor") or (a.get("type") == "ebike" and p.insurance_type == "ebike") or (a.get("type") == "phone" and p.insurance_type == "gadget") or (a.get("type") == "pet" and p.insurance_type == "pet"):
                g.edge(f"policy:{p.id}", asset_keys[i], "COVERS_ASSET", source_ref="rule:product_type_match", kind="inferred", verified=False)

    def clause_node(cid: str) -> str | None:
        hit = clause_index.get(cid)
        if not hit:
            return None
        p, c = hit
        ntype = CLAUSE_NODE_TYPES.get(c.kind, "condition")
        tags = set(c.tags or [])
        if "excess" in tags:
            ntype = "deductible"
        elif "waiting_period" in tags:
            ntype = "waiting_period"
        k = g.node(f"clause:{c.id}", ntype, {"en": f"§{c.clause_ref} {c.title}", "zh": f"§{c.clause_ref} {c.title}"}, ref_type="clause", ref_id=c.id, citation=f"{p.policy_number} §{c.clause_ref}", kind=c.kind, amount=c.amount, policy_id=p.id, section=c.section, excerpt=" ".join((c.text or "").split())[:400], language=c.language, translations=c.translations or {}, title=c.title)
        g.edge(f"policy:{p.id}", k, "CONTAINS_CLAUSE", source_ref=f"clause:{c.id}", kind="stated", verified=True)
        if ntype == "limit":
            g.edge(f"policy:{p.id}", k, "HAS_LIMIT", source_ref=f"clause:{c.id}", kind="stated", verified=True)
        if ntype == "deductible":
            g.edge(f"policy:{p.id}", k, "HAS_DEDUCTIBLE", source_ref=f"clause:{c.id}", kind="stated", verified=True)
        if ntype == "waiting_period":
            g.edge(f"policy:{p.id}", k, "HAS_WAITING_PERIOD", source_ref=f"clause:{c.id}", kind="stated", verified=True)
        return k

    for f in findings:
        need = f["need"]
        nk = g.node(f"need:{need}", "need", f.get("label", {"en": need, "zh": need}), status=f.get("status"), need=need)
        fk = g.node(f"finding:{f.get('id') or need}", "finding", f.get("label", {"en": need, "zh": need}), status=f.get("status"), ref_type="finding", ref_id=f.get("id"), risk_priority=f.get("risk_priority"), evidence_confidence=f.get("evidence_confidence"), verification=(f.get("verification") or {}).get("overall"), kind=f.get("kind"), need=need)
        g.edge(fk, nk, "ABOUT", source_ref="analysis", kind="inferred", verified=bool((f.get("verification") or {}).get("overall") == "verified"))
        # which assets are exposed to this need
        trig = (f.get("triggered_by") or "").lower()
        for i, a in enumerate(profile.assets or []):
            label = (a.get("label") or "").lower()
            if label and (label in trig or trig in label) and f.get("status") in {"gap", "partial"}:
                g.edge(asset_keys[i], nk, "EXPOSED_TO", source_ref="rule:trigger_match", kind="inferred", verified=False)
        verified = (f.get("verification") or {}).get("overall") == "verified"
        for e in f.get("evidence", []):
            ck = clause_node(e.get("clause_id", ""))
            if not ck:
                continue
            kind = clause_index[e["clause_id"]][1].kind
            g.edge(fk, ck, "SUPPORTED_BY_EVIDENCE", source_ref=f"clause:{e['clause_id']}", kind="stated", verified=True)
            if kind == "coverage":
                g.edge(ck, nk, "COVERS_RISK", source_ref=f"clause:{e['clause_id']}", kind="inferred", verified=verified)
            elif kind == "exclusion":
                g.edge(ck, nk, "EXCLUDES_RISK", source_ref=f"clause:{e['clause_id']}", kind="inferred", verified=verified)
            else:
                g.edge(ck, nk, "CONDITIONS", source_ref=f"clause:{e['clause_id']}", kind="inferred", verified=verified)
        for x in (f.get("verification") or {}).get("contradictions", []):
            ck = clause_node(x.get("clause_id", "")) if x.get("clause_id") else None
            if ck:
                g.edge(ck, fk, "CONFLICTS_WITH", source_ref=f"clause:{x['clause_id']}", kind="inferred", verified=False)
        if f.get("scenario"):
            sk = g.node(f"scenario:{need}", "scenario", f["scenario"].get("description", {"en": need, "zh": need}), severity_band=f["scenario"].get("severity_band"), likelihood_note=f["scenario"].get("likelihood_note"))
            g.edge(fk, sk, "AFFECTS_SCENARIO", source_ref="analysis", kind="inferred", verified=False)
        if f.get("suggested_action") and f.get("status") in {"gap", "partial", "duplicate"}:
            ak = g.node(f"action:{need}", "action", f["suggested_action"], priority=f.get("risk_priority"))
            g.edge(ak, fk, "MAY_ADDRESS_GAP", source_ref="analysis", kind="inferred", verified=False)
        if f.get("requires_human_review") or f.get("kind") == "unresolved" or f.get("missing_information"):
            for pn in f.get("policies", []):
                p = by_number.get(pn)
                if p:
                    g.edge(fk, f"policy:{p.id}", "REQUIRES_CLARIFICATION", source_ref="analysis", kind="inferred", verified=False)
        for pn in f.get("policies", []):
            p = by_number.get(pn)
            if p:
                g.edge(f"policy:{p.id}", nk, "ADDRESSES" if f.get("status") in {"covered", "partial", "duplicate"} else "RELATES_TO", source_ref="analysis", kind="inferred", verified=verified)
    for c in claims:
        ck = g.node(f"claim:{c.id}", "claim", {"en": f"Claim {c.reference}", "zh": f"理赔 {c.reference}"}, status=c.status, ref_type="claim", ref_id=c.id, incident_date=c.incident_date.isoformat() if c.incident_date else None)
        if c.policy_id:
            g.edge(ck, f"policy:{c.policy_id}", "DEPENDS_ON", source_ref=f"claim:{c.id}", kind="stated", verified=True)
    return g


def persist(db: Session, analysis_id: str, user_id: str, g: Graph) -> None:
    db.execute(delete(GraphEdge).where(GraphEdge.analysis_id == analysis_id))
    db.execute(delete(GraphNode).where(GraphNode.analysis_id == analysis_id))
    for n in g.nodes.values():
        db.add(GraphNode(analysis_id=analysis_id, user_id=user_id, key=n["key"], node_type=n["node_type"], label=n["label"], status=n["status"], ref_type=n["ref_type"], ref_id=n["ref_id"], data=n["data"]))
    for e in g.edges:
        db.add(GraphEdge(analysis_id=analysis_id, source_key=e["source_key"], target_key=e["target_key"], relation=e["relation"], provenance=e["provenance"], verified=e["verified"]))
    db.flush()


def load(db: Session, analysis_id: str) -> dict:
    nodes = db.execute(select(GraphNode).where(GraphNode.analysis_id == analysis_id)).scalars().all()
    edges = db.execute(select(GraphEdge).where(GraphEdge.analysis_id == analysis_id)).scalars().all()
    return {
        "nodes": [{"key": n.key, "type": n.node_type, "label": n.label, "status": n.status, "ref_type": n.ref_type, "ref_id": n.ref_id, "data": n.data} for n in nodes],
        "edges": [{"source": e.source_key, "target": e.target_key, "relation": e.relation, "provenance": e.provenance, "verified": e.verified} for e in edges],
    }


def neighbours(graph: dict, key: str) -> list[dict]:
    """Keyboard accessible alternative to the picture: the relationships of one node."""
    out = []
    labels = {n["key"]: n for n in graph["nodes"]}
    for e in graph["edges"]:
        if e["source"] == key and e["target"] in labels:
            out.append({"direction": "out", "relation": e["relation"], "node": labels[e["target"]], "provenance": e["provenance"], "verified": e["verified"]})
        elif e["target"] == key and e["source"] in labels:
            out.append({"direction": "in", "relation": e["relation"], "node": labels[e["source"]], "provenance": e["provenance"], "verified": e["verified"]})
    return out
