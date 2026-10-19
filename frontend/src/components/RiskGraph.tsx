import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";
import { useLang } from "../lib/i18n";
import { Icon } from "./Icons";

export interface GraphNode { key: string; type: string; label: Record<string, string>; status: string | null; ref_type: string | null; ref_id: string | null; data: Record<string, unknown> }
export interface GraphEdge { source: string; target: string; relation: string; provenance: { kind: string; source: string }; verified: boolean }
export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[] }

/** Layer per node type: a left to right reading from what is exposed, through the risk, to the wording that addresses it. */
const LAYERS: Record<string, number> = { customer: 0, asset: 0, claim: 0, need: 1, finding: 2, scenario: 2, action: 2, policy: 3, endorsement: 3, coverage_module: 4, exclusion: 4, limit: 4, deductible: 4, waiting_period: 4, condition: 4, definition: 4 };
const DEFAULT_VISIBLE = new Set(["customer", "asset", "need", "finding", "policy", "exclusion", "endorsement"]);
const CLAUSE_TYPES = new Set(["coverage_module", "exclusion", "limit", "deductible", "waiting_period", "condition", "definition"]);
const TYPE_LABELS: Record<string, { en: string; zh: string }> = {
  customer: { en: "You", zh: "你" }, asset: { en: "Assets and needs", zh: "资产与需求" }, need: { en: "Risks", zh: "风险" }, finding: { en: "Findings", zh: "发现" }, scenario: { en: "Scenarios", zh: "情景" }, action: { en: "Next steps", zh: "下一步" },
  policy: { en: "Policies", zh: "保单" }, endorsement: { en: "Endorsements", zh: "批单" }, coverage_module: { en: "Coverage clauses", zh: "保障条款" }, exclusion: { en: "Exclusions", zh: "除外责任" }, limit: { en: "Limits", zh: "限额" }, deductible: { en: "Excesses", zh: "免赔额" }, waiting_period: { en: "Waiting periods", zh: "等待期" }, condition: { en: "Conditions", zh: "条件" }, definition: { en: "Definitions", zh: "定义" }, claim: { en: "Claims", zh: "理赔" },
};
const RELATION_LABELS: Record<string, { en: string; zh: string }> = {
  OWNS_POLICY: { en: "owns policy", zh: "持有保单" }, OWNS_ASSET: { en: "owns", zh: "拥有" }, COVERS_ASSET: { en: "may cover", zh: "可能承保" }, COVERS_RISK: { en: "covers risk", zh: "承保风险" }, EXCLUDES_RISK: { en: "excludes risk", zh: "排除风险" }, CONTAINS_CLAUSE: { en: "contains clause", zh: "包含条款" },
  HAS_LIMIT: { en: "has limit", zh: "设有限额" }, HAS_DEDUCTIBLE: { en: "has excess", zh: "设有免赔额" }, HAS_WAITING_PERIOD: { en: "has waiting period", zh: "设有等待期" }, SUPPORTED_BY_EVIDENCE: { en: "supported by", zh: "依据" }, CONFLICTS_WITH: { en: "may conflict with", zh: "可能冲突" }, MAY_ADDRESS_GAP: { en: "may address", zh: "可能解决" },
  AFFECTS_SCENARIO: { en: "affects scenario", zh: "影响情景" }, REQUIRES_CLARIFICATION: { en: "needs clarifying with", zh: "需向其澄清" }, DEPENDS_ON: { en: "depends on", zh: "依赖于" }, ABOUT: { en: "about", zh: "关于" }, EXPOSED_TO: { en: "exposed to", zh: "面临" }, ADDRESSES: { en: "addresses", zh: "涉及" }, RELATES_TO: { en: "relates to", zh: "相关" }, CONDITIONS: { en: "conditions", zh: "附加条件" }, SUPERSEDES: { en: "supersedes", zh: "取代" },
};

const W = 1000;
const H = 620;

export function relationLabel(rel: string, lang: string): string {
  return RELATION_LABELS[rel]?.[lang as "en" | "zh"] || rel.toLowerCase().replace(/_/g, " ");
}
export function typeLabel(t: string, lang: string): string {
  return TYPE_LABELS[t]?.[lang as "en" | "zh"] || t;
}

interface Placed extends GraphNode { x: number; y: number; r: number }

function layout(nodes: GraphNode[], edges: GraphEdge[]): Placed[] {
  const byLayer = new Map<number, GraphNode[]>();
  for (const n of nodes) {
    const l = LAYERS[n.type] ?? 2;
    if (!byLayer.has(l)) byLayer.set(l, []);
    byLayer.get(l)!.push(n);
  }
  const layers = [...byLayer.keys()].sort((a, b) => a - b);
  const pos = new Map<string, Placed>();
  const colW = W / Math.max(1, layers.length);
  // first pass: evenly spaced in each column
  layers.forEach((l, li) => {
    const col = byLayer.get(l)!;
    col.forEach((n, i) => {
      pos.set(n.key, { ...n, x: colW * li + colW / 2, y: ((i + 1) * H) / (col.length + 1), r: n.type === "finding" ? 15 : n.type === "policy" || n.type === "customer" ? 17 : n.type === "need" ? 13 : 10 });
    });
  });
  // two barycenter sweeps to reduce crossings
  const neigh = new Map<string, string[]>();
  for (const e of edges) {
    neigh.set(e.source, [...(neigh.get(e.source) || []), e.target]);
    neigh.set(e.target, [...(neigh.get(e.target) || []), e.source]);
  }
  for (let sweep = 0; sweep < 3; sweep++) {
    for (const l of layers) {
      const col = byLayer.get(l)!;
      const scored = col.map((n) => {
        const ys = (neigh.get(n.key) || []).map((k) => pos.get(k)?.y).filter((y): y is number => typeof y === "number");
        return { n, score: ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : pos.get(n.key)!.y };
      });
      scored.sort((a, b) => a.score - b.score);
      scored.forEach((s, i) => {
        pos.get(s.n.key)!.y = ((i + 1) * H) / (col.length + 1);
      });
    }
  }
  return [...pos.values()];
}

function statusStroke(status: string | null | undefined): string {
  switch (status) {
    case "gap": return "var(--danger-text)";
    case "partial": return "var(--warning-text)";
    case "duplicate": return "var(--brand-700)";
    case "covered": return "var(--success-text)";
    case "expired": case "superseded": return "var(--border-strong)";
    default: return "var(--text-secondary)";
  }
}
function typeFill(t: string): string {
  if (t === "finding") return "var(--surface)";
  if (t === "policy") return "var(--brand-100)";
  if (t === "exclusion") return "var(--danger-bg)";
  if (t === "coverage_module") return "var(--success-bg)";
  if (t === "limit" || t === "deductible" || t === "waiting_period") return "var(--warning-bg)";
  if (t === "customer") return "var(--brand-900)";
  return "var(--grey-200)";
}

export function RiskGraph({ graph, selected, onSelect, highlightKeys }: { graph: GraphData; selected: string | null; onSelect: (key: string | null) => void; highlightKeys?: Set<string> }) {
  const { lang, t } = useLang();
  const [visible, setVisible] = useState<Set<string>>(new Set(DEFAULT_VISIBLE));
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [view, setView] = useState({ x: 0, y: 0, w: W, h: H });
  const [mode, setMode] = useState<"graph" | "list">(() => (typeof window !== "undefined" && window.innerWidth < 900 ? "list" : "graph"));
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const types = useMemo(() => [...new Set(graph.nodes.map((n) => n.type))].sort((a, b) => (LAYERS[a] ?? 2) - (LAYERS[b] ?? 2)), [graph.nodes]);
  // nodes of hidden types still appear while they are connected to the selected node (focus mode)
  const focusKeys = useMemo(() => {
    const s = new Set<string>();
    if (!selected) return s;
    for (const e of graph.edges) {
      if (e.source === selected) s.add(e.target);
      if (e.target === selected) s.add(e.source);
    }
    return s;
  }, [selected, graph.edges]);
  const shownNodes = useMemo(() => graph.nodes.filter((n) => visible.has(n.type) || n.key === selected || (CLAUSE_TYPES.has(n.type) && focusKeys.has(n.key))), [graph.nodes, visible, selected, focusKeys]);
  const shownKeys = useMemo(() => new Set(shownNodes.map((n) => n.key)), [shownNodes]);
  const shownEdges = useMemo(() => graph.edges.filter((e) => shownKeys.has(e.source) && shownKeys.has(e.target) && (!verifiedOnly || e.verified)), [graph.edges, shownKeys, verifiedOnly]);
  const placed = useMemo(() => layout(shownNodes, shownEdges), [shownNodes, shownEdges]);
  const byKey = useMemo(() => new Map(placed.map((p) => [p.key, p])), [placed]);
  const connected = useMemo(() => {
    const s = new Set<string>();
    if (!selected) return s;
    for (const e of shownEdges) {
      if (e.source === selected) s.add(e.target);
      if (e.target === selected) s.add(e.source);
    }
    return s;
  }, [selected, shownEdges]);

  useEffect(() => {
    const h = () => setMode((m) => (window.innerWidth < 900 && m === "graph" ? "list" : m));
    window.addEventListener("resize", h);
    return () => window.removeEventListener("resize", h);
  }, []);

  const zoom = (factor: number, cx = view.x + view.w / 2, cy = view.y + view.h / 2) => {
    setView((v) => {
      const w = Math.min(W * 2.5, Math.max(W / 5, v.w * factor));
      const h = (w * H) / W;
      return { x: cx - ((cx - v.x) * w) / v.w, y: cy - ((cy - v.y) * h) / v.h, w, h };
    });
  };
  const onWheel = (e: WheelEvent<SVGSVGElement>) => {
    e.preventDefault();
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const cx = view.x + ((e.clientX - rect.left) / rect.width) * view.w;
    const cy = view.y + ((e.clientY - rect.top) / rect.height) * view.h;
    zoom(e.deltaY > 0 ? 1.12 : 0.9, cx, cy);
  };
  const onPointerDown = (e: PointerEvent<SVGSVGElement>) => {
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent<SVGSVGElement>) => {
    if (!drag.current) return;
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const dx = ((e.clientX - drag.current.x) / rect.width) * view.w;
    const dy = ((e.clientY - drag.current.y) / rect.height) * view.h;
    setView((v) => ({ ...v, x: drag.current!.vx - dx, y: drag.current!.vy - dy }));
  };
  const onPointerUp = () => { drag.current = null; };
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    const step = view.w * 0.08;
    if (e.key === "ArrowLeft") setView((v) => ({ ...v, x: v.x - step }));
    else if (e.key === "ArrowRight") setView((v) => ({ ...v, x: v.x + step }));
    else if (e.key === "ArrowUp") setView((v) => ({ ...v, y: v.y - step }));
    else if (e.key === "ArrowDown") setView((v) => ({ ...v, y: v.y + step }));
    else if (e.key === "+" || e.key === "=") zoom(0.85);
    else if (e.key === "-") zoom(1.18);
    else if (e.key === "Escape") onSelect(null);
    else return;
    e.preventDefault();
  };
  const toggleType = (ty: string) => setVisible((s) => { const n = new Set(s); if (n.has(ty)) n.delete(ty); else n.add(ty); return n; });

  const listNodes = shownNodes.filter((n) => ["finding", "need", "policy", "asset"].includes(n.type));

  return (
    <div className="graph-wrap">
      <div className="graph-toolbar" role="toolbar" aria-label={t("risk_graph")}>
        <div className="row" style={{ gap: 4 }}>
          <button className={`btn sm ${mode === "graph" ? "primary" : ""}`} onClick={() => setMode("graph")} aria-pressed={mode === "graph"}><Icon name="graph" size={14} /> {t("graph_view")}</button>
          <button className={`btn sm ${mode === "list" ? "primary" : ""}`} onClick={() => setMode("list")} aria-pressed={mode === "list"}><Icon name="list" size={14} /> {t("list_view")}</button>
        </div>
        {mode === "graph" && (
          <div className="row" style={{ gap: 4 }}>
            <button className="btn sm icon" onClick={() => zoom(0.85)} aria-label={t("zoom_in")}><Icon name="zoomIn" size={14} /></button>
            <button className="btn sm icon" onClick={() => zoom(1.18)} aria-label={t("zoom_out")}><Icon name="zoomOut" size={14} /></button>
            <button className="btn sm icon" onClick={() => setView({ x: 0, y: 0, w: W, h: H })} aria-label={t("reset_view")}><Icon name="refresh" size={14} /></button>
          </div>
        )}
        <label><input type="checkbox" checked={verifiedOnly} onChange={(e) => setVerifiedOnly(e.target.checked)} /> {lang === "zh" ? "仅显示已核实关系" : "Verified relationships only"}</label>
        <details style={{ marginLeft: "auto" }}>
          <summary className="btn sm" style={{ listStyle: "none" }}><Icon name="filter" size={14} /> {t("filter")}</summary>
          <div className="card" style={{ position: "absolute", right: 8, zIndex: 5, padding: 10, display: "grid", gap: 4, marginTop: 4 }}>
            {types.map((ty) => (
              <label key={ty}><input type="checkbox" checked={visible.has(ty)} onChange={() => toggleType(ty)} /> {typeLabel(ty, lang)} <span className="muted">({graph.nodes.filter((n) => n.type === ty).length})</span></label>
            ))}
          </div>
        </details>
      </div>
      {mode === "graph" ? (
        <svg ref={svgRef} viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`} role="application" aria-label={t("risk_graph")} tabIndex={0} onWheel={onWheel} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerLeave={onPointerUp} onKeyDown={onKey} onClick={(e) => { if (e.target === e.currentTarget) onSelect(null); }}>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--border-strong)" /></marker>
          </defs>
          {shownEdges.map((e, i) => {
            const a = byKey.get(e.source);
            const b = byKey.get(e.target);
            if (!a || !b) return null;
            const active = selected && (e.source === selected || e.target === selected);
            const dim = selected && !active;
            const mx = (a.x + b.x) / 2;
            const d = `M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}`;
            const negative = e.relation === "EXCLUDES_RISK" || e.relation === "CONFLICTS_WITH";
            return (
              <g key={i} opacity={dim ? 0.12 : 1}>
                <path d={d} fill="none" stroke={negative ? "var(--danger-text)" : active ? "var(--brand-900)" : "var(--border-strong)"} strokeWidth={active ? 2 : 1.2} strokeDasharray={e.verified ? undefined : "4 3"} markerEnd="url(#arrow)" />
                {active && <text x={mx} y={(a.y + b.y) / 2 - 4} textAnchor="middle" style={{ fontSize: 10, fill: "var(--text-secondary)" }}>{relationLabel(e.relation, lang)}{e.verified ? "" : lang === "zh" ? "（推断）" : " (inferred)"}</text>}
              </g>
            );
          })}
          {placed.map((n) => {
            const isSel = n.key === selected;
            const dim = selected && !isSel && !connected.has(n.key);
            const hl = highlightKeys?.has(n.key);
            const label = n.label[lang] || n.label.en || n.key;
            const short = label.length > 26 ? label.slice(0, 25) + "…" : label;
            return (
              <g key={n.key} transform={`translate(${n.x},${n.y})`} opacity={dim ? 0.25 : 1} role="button" tabIndex={0} aria-label={`${typeLabel(n.type, lang)}: ${label}${n.status ? `, ${n.status}` : ""}`} aria-pressed={isSel} style={{ cursor: "pointer" }} onClick={(e) => { e.stopPropagation(); onSelect(isSel ? null : n.key); }} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); onSelect(isSel ? null : n.key); } }}>
                {n.type === "finding" || n.type === "policy" ? (
                  <rect x={-n.r * 1.4} y={-n.r} width={n.r * 2.8} height={n.r * 2} rx={6} fill={typeFill(n.type)} stroke={statusStroke(n.status)} strokeWidth={isSel || hl ? 3 : 1.5} />
                ) : (
                  <circle r={n.r} fill={typeFill(n.type)} stroke={statusStroke(n.status)} strokeWidth={isSel || hl ? 3 : 1.5} />
                )}
                <text y={n.r + 13} textAnchor="middle" style={{ fontSize: 11.5, fill: "var(--text-body)", fontWeight: isSel ? 600 : 400 }}>{short}</text>
              </g>
            );
          })}
        </svg>
      ) : (
        <ul className="rel-list" style={{ padding: "4px 12px", maxHeight: 520, overflow: "auto" }}>
          {listNodes.map((n) => {
            const rels = shownEdges.filter((e) => e.source === n.key || e.target === n.key);
            return (
              <li key={n.key}>
                <details open={n.key === selected} onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && onSelect(n.key)}>
                  <summary><span className="small muted">{typeLabel(n.type, lang)}</span> · {n.label[lang] || n.label.en}{n.status ? <span className="badge plain" style={{ marginLeft: 6 }}>{n.status}</span> : null}</summary>
                  <ul style={{ margin: "6px 0 0", paddingLeft: 16 }}>
                    {rels.map((e, i) => {
                      const other = graph.nodes.find((x) => x.key === (e.source === n.key ? e.target : e.source));
                      if (!other) return null;
                      return <li key={i} className="small"><button className="btn ghost sm" style={{ padding: 0 }} onClick={() => onSelect(other.key)}>{relationLabel(e.relation, lang)}: {other.label[lang] || other.label.en}</button> <span className="muted">{e.verified ? (lang === "zh" ? "已核实" : "verified") : lang === "zh" ? "推断" : "inferred"}</span></li>;
                    })}
                    {rels.length === 0 && <li className="small muted">—</li>}
                  </ul>
                </details>
              </li>
            );
          })}
        </ul>
      )}
      <div className="graph-legend">
        <span><i style={{ borderColor: "var(--danger-text)" }} /> {t("gap")}</span>
        <span><i style={{ borderColor: "var(--warning-text)" }} /> {t("partial")}</span>
        <span><i style={{ borderColor: "var(--brand-700)" }} /> {t("duplicate")}</span>
        <span><i style={{ borderColor: "var(--success-text)" }} /> {t("covered")}</span>
        <span style={{ borderLeft: "1px solid var(--border)", paddingLeft: 10 }}>{lang === "zh" ? "虚线 = 推断关系；实线 = 已核实" : "dashed = inferred, solid = verified"}</span>
      </div>
    </div>
  );
}
