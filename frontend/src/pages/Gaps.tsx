import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { type Finding } from "../components/Blocks";
import { Icon } from "../components/Icons";
import { CustomerShell } from "../components/Layout";
import { CitationChip, CitationProvider, RichText, type ClauseRef } from "../components/Markdown";
import { RiskGraph, relationLabel, typeLabel, type GraphData, type GraphNode } from "../components/RiskGraph";
import { Badge, Empty, Spinner, StatusBadge, useToast } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { fmtDateTime, money } from "../lib/format";
import { useLang } from "../lib/i18n";

interface Stage { key: string; status: "pending" | "running" | "done" | "failed" | "skipped"; started_at: string | null; ended_at: string | null; detail: Record<string, unknown> }
interface Job { id: string; status: string; mode: string; stages: Stage[]; models: Record<string, ModelMeta>; analysis_id: string | null; error: string | null }
interface ModelMeta { provider: string; model: string; fallback?: boolean; error?: string; latency_ms?: number; findings?: number; endpoint?: string }
export interface RichFinding extends Finding {
  id: string; key?: string; kind: string; risk_priority: string; evidence_confidence: string; priority_rationale?: { factor: string; value: unknown; weight: number }[];
  verification: { overall: string; source_exists: boolean; evidence_support: string; contradictions: { citation: string; kind: string; title?: string; effect: string }[]; rule_consistency: string; unmatched_figures: number[]; independent_check: string; independent_reason?: string; escalation: string[]; evidence: { citation: string; support: string; relationship?: string; quoted_text?: string }[] };
  assumptions: string[]; missing_information: string[]; scenario: { description: Record<string, string>; severity_band: string; likelihood_note: string } | null;
  calculation: { status: string; payable?: number; uncovered?: number; currency?: string; steps?: { step: string; value?: number; excess?: number; detail?: string }[]; label?: Record<string, string>; basis?: Record<string, string>; unresolved_conditions?: string[]; rule?: { limit?: number; excess?: number; calculation_method?: string } } | null;
  models: { proposed_by: string[]; rationale?: string; judge?: { verdict: string; reason: string; model: string }; challenges?: { type: string; note: string }[] };
  requires_human_review: boolean; review_status: string;
}
interface Analysis { id: string; created_at: string; findings: RichFinding[]; summary: { counts: Record<string, number>; by_priority?: Record<string, number>; policies_considered: string[]; policies_excluded?: { policy_number: string; status: string }[]; assets_considered: number; activities_considered: number; facts_extracted?: number; rejected?: { key: string; need: string; reason: string }[]; mode?: string; missing_evidence?: string[]; next_steps?: string[] }; disclaimer: Record<string, string>; mode: string; models: Record<string, ModelMeta>; graph: GraphData; pipeline_version?: string; job_id?: string | null }

const PRIORITY_LABEL: Record<string, { en: string; zh: string }> = {
  critical_to_investigate: { en: "Critical to investigate", zh: "需优先核查" }, high: { en: "High priority", zh: "高优先级" }, medium: { en: "Medium priority", zh: "中等优先级" }, low: { en: "Low priority", zh: "低优先级" }, information_needed: { en: "Information needed", zh: "需补充信息" },
};
const CONF_LABEL: Record<string, { en: string; zh: string }> = { high: { en: "High confidence", zh: "证据充分" }, medium: { en: "Medium confidence", zh: "证据中等" }, low: { en: "Low confidence", zh: "证据不足" } };
const VERIF_LABEL: Record<string, { en: string; zh: string }> = { verified: { en: "Verified", zh: "已核实" }, qualified: { en: "Verified with qualifications", zh: "已核实（有限定）" }, requires_review: { en: "Needs a person", zh: "需人工复核" }, rejected: { en: "Rejected", zh: "已剔除" } };
const KIND_LABEL: Record<string, { en: string; zh: string }> = { policy_fact: { en: "Policy fact", zh: "保单事实" }, potential_gap: { en: "Potential gap", zh: "潜在缺口" }, hypothetical_scenario: { en: "Hypothetical scenario", zh: "假设情景" }, unresolved: { en: "Unresolved interpretation", zh: "未解决的解释" } };
const STAGE_KEYS = ["customer_context", "extraction", "graph", "analysts", "challenge", "judge", "verification", "calculation", "report"];

function PriorityChip({ p }: { p: string }) {
  const { lang } = useLang();
  return <span className={`priority ${p}`}>{PRIORITY_LABEL[p]?.[lang] || p}</span>;
}

export function StagesPanel({ job, compact }: { job: Job | null; compact?: boolean }) {
  const { t, lang } = useLang();
  const stages = job?.stages?.length ? job.stages : STAGE_KEYS.map((k) => ({ key: k, status: "pending", started_at: null, ended_at: null, detail: {} }) as Stage);
  return (
    <div className="stages" aria-live="polite">
      {stages.map((s) => {
        const d = s.detail || {};
        const extra = s.key === "analysts" && d.A ? `A: ${(d.A as ModelMeta).model}${(d.A as ModelMeta).fallback ? (lang === "zh" ? "（已回退）" : " (fallback)") : ""} · B: ${(d.B as ModelMeta).model}${(d.B as ModelMeta).fallback ? (lang === "zh" ? "（已回退）" : " (fallback)") : ""}`
          : s.key === "extraction" && d.facts != null ? `${d.facts} ${lang === "zh" ? "条事实" : "facts"}${d.not_found ? ` · ${d.not_found} ${lang === "zh" ? "项未找到" : "not found"}` : ""}`
            : s.key === "judge" && d.accepted != null ? `${d.accepted} ${lang === "zh" ? "接受" : "accepted"} · ${d.revise} ${lang === "zh" ? "修订" : "revised"} · ${d.rejected} ${lang === "zh" ? "剔除" : "rejected"} · ${d.unresolved} ${lang === "zh" ? "未解决" : "unresolved"}`
              : s.key === "verification" && d.verified != null ? `${d.verified} ${lang === "zh" ? "已核实" : "verified"} · ${d.qualified} ${lang === "zh" ? "有限定" : "qualified"} · ${d.requires_review} ${lang === "zh" ? "需复核" : "need review"}`
                : s.key === "challenge" && d.challenges != null ? `${d.challenges} ${lang === "zh" ? "项质疑" : "challenges"} · ${d.additional_findings} ${lang === "zh" ? "项补充" : "additional"}`
                  : s.key === "calculation" && d.illustrative != null ? `${d.illustrative} ${lang === "zh" ? "项示意计算" : "illustrative calculations"}`
                    : s.key === "report" && d.findings != null ? `${d.findings} ${lang === "zh" ? "项发现" : "findings"} · ${d.nodes} ${lang === "zh" ? "个节点" : "nodes"}` : "";
        return (
          <div key={s.key} className={`stage ${s.status}`}>
            <span className="dot" aria-hidden="true">{s.status === "done" ? <Icon name="check" size={11} /> : s.status === "failed" ? <Icon name="x" size={11} /> : null}</span>
            <span><span>{t(`stage_${s.key}`)}</span>{!compact && extra && <span className="small muted"> · {extra}</span>}{s.status === "failed" && d.error ? <span className="small" style={{ color: "var(--danger-text)" }}> · {String(d.error)}</span> : null}</span>
            <span className="when">{s.status === "running" ? (lang === "zh" ? "进行中" : "running") : s.status === "done" && s.started_at && s.ended_at ? `${Math.max(0, new Date(s.ended_at).getTime() - new Date(s.started_at).getTime())} ms` : ""}</span>
          </div>
        );
      })}
    </div>
  );
}

function FindingCard({ f, selected, onSelect, onReview, currency }: { f: RichFinding; selected: boolean; onSelect: () => void; onReview: (f: RichFinding) => void; currency: string }) {
  const { t, lang, pick } = useLang();
  const [open, setOpen] = useState(selected);
  useEffect(() => { if (selected) setOpen(true); }, [selected]);
  const v = f.verification;
  const calc = f.calculation;
  return (
    <article className={`finding-card ${selected ? "selected" : ""}`} id={`finding-${f.id}`} aria-expanded={open}>
      <div className="head">
        <button onClick={() => { setOpen((o) => !o); onSelect(); }} aria-controls={`finding-detail-${f.id}`}>
          <div className="row" style={{ gap: 8 }}>
            <strong>{pick(f.label)}</strong>
            <StatusBadge status={f.status} label={t(f.status)} />
            <span className="small muted">{KIND_LABEL[f.kind]?.[lang] || f.kind}</span>
          </div>
          <div className="meta">
            <PriorityChip p={f.risk_priority} />
            <Badge tone={f.evidence_confidence === "high" ? "ok" : f.evidence_confidence === "medium" ? "" : "warn"} plain>{CONF_LABEL[f.evidence_confidence]?.[lang]}</Badge>
            <Badge tone={v.overall === "verified" ? "ok" : v.overall === "requires_review" ? "warn" : ""} plain>{VERIF_LABEL[v.overall]?.[lang] || v.overall}</Badge>
            {f.review_status === "requested" && <Badge tone="accent" plain>{t("review_requested")}</Badge>}
          </div>
          <p style={{ margin: "8px 0 0" }}><RichText text={pick(f.explanation)} /></p>
        </button>
        <Icon name={open ? "chevronUp" : "chevronDown"} size={16} />
      </div>
      {open && (
        <div className="detail" id={`finding-detail-${f.id}`}>
          {f.evidence.length > 0 && (
            <div><div className="small muted">{t("evidence")}</div><div className="row" style={{ gap: 6, marginTop: 4 }}>{f.evidence.map((e) => <span key={e.citation} className="row" style={{ gap: 4 }}><CitationChip citation={e.citation} /><span className="small">{e.title}{(e as { note?: string }).note === "qualification" ? <span className="muted"> ({lang === "zh" ? "限定条款" : "qualification"})</span> : ""}</span></span>)}</div></div>
          )}
          {f.policies.length > 0 && <div><span className="small muted">{lang === "zh" ? "相关保单：" : "Policies: "}</span><span className="mono">{f.policies.join(", ")}</span></div>}
          {calc && calc.status === "illustrative" && (
            <div className="alert info">
              <div><b>{lang === "zh" ? "示意金额" : "Illustrative figures"}:</b> {lang === "zh" ? "可赔" : "payable"} {money(calc.payable, calc.currency || currency, lang)} · {lang === "zh" ? "未覆盖" : "uncovered"} {money(calc.uncovered, calc.currency || currency, lang)}</div>
              <div className="small muted">{pick(calc.basis)} {calc.rule?.calculation_method ? `· ${calc.rule.calculation_method.replace(/_/g, " ")}` : ""}</div>
              <details className="small" style={{ marginTop: 4 }}><summary>{lang === "zh" ? "计算步骤" : "Calculation trace"}</summary><ol style={{ margin: "4px 0 0", paddingLeft: 18 }}>{calc.steps?.map((s, i) => <li key={i}>{s.step.replace(/_/g, " ")}{s.excess != null ? ` (excess ${s.excess})` : ""}: {s.value}</li>)}</ol></details>
              <div className="small" style={{ color: "var(--warning-text)", marginTop: 4 }}>{pick(calc.label)}</div>
            </div>
          )}
          {calc && calc.status === "unresolved" && <div className="alert warn small">{pick(calc.label)} {calc.unresolved_conditions?.join("; ")}</div>}
          {f.scenario && <div><div className="small muted">{lang === "zh" ? "假设情景" : "Hypothetical scenario"} · {f.scenario.severity_band} · {f.scenario.likelihood_note}</div><p className="small" style={{ margin: "2px 0 0" }}>{pick(f.scenario.description)}</p></div>}
          {f.assumptions.length > 0 && <div><div className="small muted">{t("assumptions")}</div><ul className="small" style={{ margin: "2px 0 0", paddingLeft: 18 }}>{f.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></div>}
          {f.missing_information.length > 0 && <div><div className="small muted">{t("missing_information")}</div><ul className="small" style={{ margin: "2px 0 0", paddingLeft: 18 }}>{f.missing_information.map((a, i) => <li key={i}>{a}</li>)}</ul></div>}
          {v.contradictions.length > 0 && <div><div className="small muted">{lang === "zh" ? "可能影响该结论的其他条款" : "Other clauses that may alter this reading"}</div><div className="row" style={{ gap: 6, marginTop: 4 }}>{v.contradictions.map((c) => <span key={c.citation} className="row" style={{ gap: 4 }}><CitationChip citation={c.citation} /><span className="small muted">{c.effect}</span></span>)}</div></div>}
          <div>
            <div className="small muted">{t("verification")}</div>
            <div className="row small" style={{ gap: 6, marginTop: 4 }}>
              <Badge tone={v.source_exists ? "ok" : "bad"} plain>A {lang === "zh" ? "来源存在" : "source exists"}: {v.source_exists ? "✓" : "✗"}</Badge>
              <Badge tone={v.evidence_support === "supported" ? "ok" : v.evidence_support === "unsupported" ? "bad" : ""} plain>B {lang === "zh" ? "证据支持" : "support"}: {v.evidence_support.replace(/_/g, " ")}</Badge>
              <Badge tone={v.contradictions.length ? "warn" : "ok"} plain>C {lang === "zh" ? "矛盾" : "contradictions"}: {v.contradictions.length}</Badge>
              <Badge tone={v.rule_consistency === "passed" ? "ok" : "warn"} plain>D {lang === "zh" ? "数值一致" : "figures"}: {v.rule_consistency}</Badge>
              <Badge plain>E {lang === "zh" ? "裁定" : "judge"}: {v.independent_check}</Badge>
              <Badge tone={v.escalation.length ? "warn" : "ok"} plain>F {lang === "zh" ? "升级" : "escalation"}: {v.escalation.length ? v.escalation.join("; ") : "—"}</Badge>
            </div>
          </div>
          <div className="small muted">
            {lang === "zh" ? "来源：" : "Proposed by "}{f.models.proposed_by.map((m) => `Model ${m}`).join(", ")}{f.models.judge ? ` · ${lang === "zh" ? "裁定" : "judge"}: ${f.models.judge.verdict}${f.models.judge.reason ? ` (${f.models.judge.reason})` : ""}` : ""}
            {f.models.challenges && f.models.challenges.length > 0 && <div>{lang === "zh" ? "质疑：" : "Challenges: "}{f.models.challenges.map((c) => `${c.type.replace(/_/g, " ")}: ${c.note}`).join("; ")}</div>}
          </div>
          {f.priority_rationale && <details className="small"><summary>{lang === "zh" ? "优先级依据" : "Why this priority"}</summary><ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{f.priority_rationale.map((r, i) => <li key={i}>{r.factor.replace(/_/g, " ")}: {String(r.value)} (weight {r.weight})</li>)}</ul></details>}
          <div className="row between" style={{ marginTop: 4 }}>
            <div className="small"><b>{t("next_step")}:</b> {pick(f.suggested_action)}</div>
            <div className="row" style={{ gap: 6 }}>
              <Link className="btn sm" to={`/assistant?q=${encodeURIComponent(lang === "zh" ? `帮我写一封给保险公司的询问信：${pick(f.label)}` : `Draft a question to my insurer about: ${pick(f.label)}`)}`}><Icon name="chat" size={14} /> {lang === "zh" ? "让助手起草问题" : "Ask the insurer"}</Link>
              {f.review_status !== "requested" ? <button className="btn sm" onClick={() => onReview(f)}><Icon name="user" size={14} /> {t("request_review")}</button> : <span className="small muted">{t("review_requested")}</span>}
            </div>
          </div>
        </div>
      )}
    </article>
  );
}

function NodePanel({ node, graph, onSelect, analysisId, findings, onJumpToFinding }: { node: GraphNode; graph: GraphData; onSelect: (k: string) => void; analysisId: string; findings: RichFinding[]; onJumpToFinding: (id: string) => void }) {
  const { lang, t } = useLang();
  const rels = graph.edges.filter((e) => e.source === node.key || e.target === node.key);
  const label = node.label[lang] || node.label.en;
  const finding = node.type === "finding" ? findings.find((f) => f.id === node.ref_id) : null;
  const d = node.data as Record<string, string | number | undefined>;
  return (
    <div className="node-panel" aria-live="polite">
      <div className="row between"><h4>{label}</h4><span className="small muted">{typeLabel(node.type, lang)}</span></div>
      {node.status && <div className="row" style={{ gap: 6 }}><StatusBadge status={node.status} label={t(node.status) === node.status ? node.status : t(node.status)} />{finding && <PriorityChip p={finding.risk_priority} />}{finding && <Badge plain>{VERIF_LABEL[finding.verification.overall]?.[lang]}</Badge>}</div>}
      {node.type === "policy" && <div className="small" style={{ marginTop: 6 }}><span className="mono">{d.number}</span> · v{d.version} · {d.effective_from} – {d.effective_to}{d.excess != null ? ` · ${t("excess")} ${d.excess}` : ""} {node.ref_id && <Link to={`/policies/${node.ref_id}`}>{t("view")}</Link>}</div>}
      {node.type === "asset" && <div className="small" style={{ marginTop: 6 }}>{d.asset_type} {d.value != null ? `· ${money(Number(d.value), String(d.currency || "GBP"), lang)}` : ""}</div>}
      {(node.type === "coverage_module" || node.type === "exclusion" || node.type === "limit" || node.type === "deductible" || node.type === "waiting_period" || node.type === "condition" || node.type === "definition") && (
        <div className="small" style={{ marginTop: 6 }}><CitationChip citation={String(d.citation)} /> <span className="muted">{d.section}</span><p style={{ margin: "4px 0 0", whiteSpace: "pre-wrap" }}>{d.excerpt}</p></div>
      )}
      {node.type === "claim" && node.ref_id && <div className="small" style={{ marginTop: 6 }}><Link to={`/claims/${node.ref_id}`}>{t("view")}</Link> · {d.incident_date}</div>}
      {finding && (
        <div className="small" style={{ marginTop: 6 }}>
          <p style={{ margin: 0 }}>{finding.explanation[lang] || finding.explanation.en}</p>
          <div className="row" style={{ gap: 6, marginTop: 4 }}><span className="muted">{CONF_LABEL[finding.evidence_confidence]?.[lang]}</span>{finding.assumptions.length > 0 && <span className="muted">· {finding.assumptions.length} {t("assumptions").toLowerCase()}</span>}</div>
          <div><b>{t("next_step")}:</b> {finding.suggested_action[lang] || finding.suggested_action.en}</div>
          <button className="btn sm" style={{ marginTop: 6 }} onClick={() => onJumpToFinding(finding.id)}>{lang === "zh" ? "在列表中查看" : "Show in findings"}</button>
        </div>
      )}
      <div className="small muted" style={{ marginTop: 8 }}>{t("relationships")} ({rels.length})</div>
      <ul className="small" style={{ margin: "4px 0 0", paddingLeft: 16, maxHeight: 160, overflow: "auto" }}>
        {rels.map((e, i) => {
          const otherKey = e.source === node.key ? e.target : e.source;
          const other = graph.nodes.find((n) => n.key === otherKey);
          if (!other) return null;
          return <li key={i}><button className="btn ghost sm" style={{ padding: 0 }} onClick={() => onSelect(other.key)}>{e.source === node.key ? "" : "← "}{relationLabel(e.relation, lang)}: {other.label[lang] || other.label.en}</button> <span className="muted">{e.verified ? (lang === "zh" ? "已核实" : "verified") : `${lang === "zh" ? "推断" : "inferred"} · ${e.provenance.source}`}</span></li>;
        })}
      </ul>
      <div className="small muted" style={{ marginTop: 6 }}>{lang === "zh" ? "键盘可访问列表：" : "Keyboard accessible list: "}<Link to={`/gaps/${analysisId}`}>{t("list_view")}</Link></div>
    </div>
  );
}

export default function GapsPage() {
  const { t, lang, pick } = useLang();
  const toast = useToast();
  const { analysisId } = useParams();
  const [analysis, setAnalysis] = useState<Analysis | null | undefined>(undefined);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<string>("issues");
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedFinding, setSelectedFinding] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  const loadLatest = useCallback(async () => {
    const r = analysisId ? await api.get<{ analysis: Analysis }>(`/api/gaps/${analysisId}`) : await api.get<{ analysis: Analysis | null }>("/api/gaps/latest");
    setAnalysis(r.analysis);
    if (r.analysis?.job_id) api.get<{ job: Job }>(`/api/gaps/jobs/${r.analysis.job_id}`).then((j) => setJob(j.job)).catch(() => {});
  }, [analysisId]);
  useEffect(() => { loadLatest(); }, [loadLatest, lang]);
  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  const run = async () => {
    setBusy(true);
    setSelected(null);
    try {
      const r = await api.post<{ job: Job; analysis: Analysis | null }>("/api/gaps/analyze", {});
      setJob(r.job);
      if (r.analysis) {
        setAnalysis(r.analysis);
        setBusy(false);
        return;
      }
      pollRef.current = window.setInterval(async () => {
        try {
          const j = await api.get<{ job: Job }>(`/api/gaps/jobs/${r.job.id}`);
          setJob(j.job);
          if (j.job.status === "completed" && j.job.analysis_id) {
            window.clearInterval(pollRef.current!);
            const a = await api.get<{ analysis: Analysis }>(`/api/gaps/${j.job.analysis_id}`);
            setAnalysis(a.analysis);
            setBusy(false);
          } else if (j.job.status === "failed") {
            window.clearInterval(pollRef.current!);
            toast(j.job.error || "Analysis failed");
            setBusy(false);
          }
        } catch (e) {
          window.clearInterval(pollRef.current!);
          toast(errorMessage(e));
          setBusy(false);
        }
      }, 600);
    } catch (e) {
      toast(errorMessage(e));
      setBusy(false);
    }
  };
  const requestReview = async (f: RichFinding) => {
    try {
      await api.post(`/api/gaps/findings/${f.id}/review`, { reason: "" });
      setAnalysis((a) => (a ? { ...a, findings: a.findings.map((x) => (x.id === f.id ? { ...x, review_status: "requested" } : x)) } : a));
      toast(t("review_requested"));
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const clauses: ClauseRef[] = useMemo(() => {
    if (!analysis) return [];
    const fromEvidence = analysis.findings.flatMap((f) => f.evidence as ClauseRef[]);
    const fromGraph = analysis.graph.nodes.filter((n) => n.data?.citation).map((n) => ({ citation: String(n.data.citation), title: String(n.data.title || ""), text: String(n.data.excerpt || ""), kind: String(n.data.kind || ""), section: String(n.data.section || ""), language: String(n.data.language || ""), translations: (n.data.translations as Record<string, string>) || {} }) as ClauseRef);
    return [...fromEvidence, ...fromGraph];
  }, [analysis]);
  const counts = analysis?.summary.counts || {};
  const byPriority = analysis?.summary.by_priority || {};
  const filtered = useMemo(() => {
    if (!analysis) return [];
    const fs = analysis.findings;
    if (filter === "issues") return fs.filter((f) => f.status !== "covered");
    if (filter === "all") return fs;
    if (filter === "review") return fs.filter((f) => f.requires_human_review || f.kind === "unresolved");
    return fs.filter((f) => f.status === filter);
  }, [analysis, filter]);
  const selectedNode = analysis?.graph.nodes.find((n) => n.key === selected) || null;
  const highlight = useMemo(() => new Set(selectedFinding ? [`finding:${selectedFinding}`] : []), [selectedFinding]);
  const jumpToFinding = (id: string) => { setSelectedFinding(id); setFilter("all"); window.setTimeout(() => document.getElementById(`finding-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 50); };
  const currency = (analysis?.graph.nodes.find((n) => n.type === "asset")?.data?.currency as string) || "GBP";

  return (
    <CustomerShell title={t("gaps")} subtitle={t("analysis_workspace_hint")} actions={<button className="btn primary" disabled={busy} onClick={run}>{busy ? <Spinner /> : <Icon name="sparkle" size={16} />}{analysis ? t("rerun_analysis") : t("run_analysis")}</button>}>
      {analysis === undefined ? <Spinner /> : (
        <CitationProvider clauses={clauses}>
          <div className="workspace">
            <div className="stack">
              {(busy || job) && (
                <div className="card">
                  <div className="card-head"><h3>{t("analysis_progress")}</h3>{job && <Badge plain>{job.mode === "deterministic" ? t("deterministic_mode") : job.mode}</Badge>}</div>
                  <StagesPanel job={job} />
                  {job?.models && Object.keys(job.models).length > 0 && (
                    <div className="small muted" style={{ marginTop: 8 }}>{t("models_used")}: {Object.entries(job.models).map(([role, m]) => `${role}: ${m.model}${m.fallback ? (lang === "zh" ? "（回退到规则）" : " (fell back to rules)") : ""}`).join(" · ")}</div>
                  )}
                </div>
              )}
              {!analysis ? (
                <Empty>
                  <p>{lang === "zh" ? "还没有分析。分析使用你在“设置”中申报的资产和活动。" : "No analysis yet. It uses the assets and activities you declared in Settings."}</p>
                  <Link className="btn" to="/settings">{t("settings")}</Link>
                </Empty>
              ) : (
                <>
                  <div className="grid cols-3">
                    {(["gap", "partial", "duplicate"] as const).map((k) => (
                      <div className="tile" key={k} style={{ borderTop: `3px solid var(--${k === "gap" ? "danger-text" : k === "partial" ? "warning-text" : "brand-700"})` }}><div className="label">{t(k)}</div><div className="value">{counts[k] || 0}</div></div>
                    ))}
                  </div>
                  <div className="card">
                    <div className="card-head">
                      <div>
                        <h3>{t("findings")} <span className="muted small">({analysis.findings.length})</span></h3>
                        <div className="small muted">{fmtDateTime(analysis.created_at, lang)} · {analysis.summary.assets_considered} {lang === "zh" ? "项资产" : "assets"}, {analysis.summary.activities_considered} {lang === "zh" ? "项活动" : "activities"}, {analysis.summary.policies_considered.length} {lang === "zh" ? "份保单" : "policies"}{analysis.summary.facts_extracted ? ` · ${analysis.summary.facts_extracted} ${lang === "zh" ? "条事实" : "facts"}` : ""} · {analysis.mode === "deterministic" ? t("deterministic_mode") : analysis.mode}</div>
                      </div>
                      <div className="row small" style={{ gap: 6 }}>
                        {(["critical_to_investigate", "high", "medium", "information_needed"] as const).filter((k) => byPriority[k]).map((k) => <span key={k} className={`priority ${k}`}>{byPriority[k]} {PRIORITY_LABEL[k][lang]}</span>)}
                      </div>
                    </div>
                    <div className="tabs" role="tablist">
                      {[["issues", lang === "zh" ? "需关注" : "Needs attention"], ["all", t("all")], ["gap", t("gap")], ["partial", t("partial")], ["duplicate", t("duplicate")], ["note", t("note")], ["covered", t("covered")], ["review", lang === "zh" ? "需人工" : "For a person"]].map(([k, label]) => (
                        <button key={k} role="tab" aria-selected={filter === k} className={filter === k ? "active" : ""} onClick={() => setFilter(k)}>{label}</button>
                      ))}
                    </div>
                    {filtered.length === 0 && <Empty>—</Empty>}
                    {filtered.map((f) => <FindingCard key={f.id} f={f} selected={selectedFinding === f.id} onSelect={() => { setSelectedFinding(f.id); setSelected(`finding:${f.id}`); }} onReview={requestReview} currency={currency} />)}
                    {analysis.summary.rejected && analysis.summary.rejected.length > 0 && (
                      <details className="small muted" style={{ marginTop: 10 }}><summary>{analysis.summary.rejected.length} {lang === "zh" ? "项候选发现因证据不足被剔除" : "candidate findings were rejected for lack of evidence"}</summary><ul style={{ paddingLeft: 18 }}>{analysis.summary.rejected.map((r) => <li key={r.key}>{r.need}: {r.reason}</li>)}</ul></details>
                    )}
                    {analysis.summary.policies_excluded && analysis.summary.policies_excluded.length > 0 && <div className="small muted" style={{ marginTop: 8 }}>{lang === "zh" ? "未纳入：" : "Not included: "}{analysis.summary.policies_excluded.map((p) => `${p.policy_number} (${p.status.replace(/_/g, " ")})`).join(", ")}</div>}
                    <div className="alert" style={{ marginTop: 12 }}>{pick(analysis.disclaimer)}</div>
                  </div>
                </>
              )}
            </div>
            {analysis && (
              <aside className="stack">
                <div className="card" style={{ padding: 0 }}>
                  <div className="row between" style={{ padding: "12px 16px 0" }}><h3 style={{ margin: 0 }}>{t("risk_graph")}</h3><span className="small muted">{analysis.graph.nodes.length} {lang === "zh" ? "节点" : "nodes"} · {analysis.graph.edges.length} {lang === "zh" ? "关系" : "edges"}</span></div>
                  <div style={{ padding: 12 }}>
                    <RiskGraph graph={analysis.graph} selected={selected} onSelect={(k) => { setSelected(k); if (k?.startsWith("finding:")) setSelectedFinding(k.slice(8)); }} highlightKeys={highlight} />
                    {selectedNode ? <NodePanel node={selectedNode} graph={analysis.graph} onSelect={setSelected} analysisId={analysis.id} findings={analysis.findings} onJumpToFinding={jumpToFinding} /> : <div className="small muted" style={{ marginTop: 10 }}>{lang === "zh" ? "点击任一节点查看说明、状态、依据和下一步。颜色不单独表达确定性：每个节点都有文字标签。" : "Select any node for its description, status, evidence and next step. Colour never carries certainty on its own; every node has a text label."}</div>}
                  </div>
                </div>
              </aside>
            )}
          </div>
        </CitationProvider>
      )}
    </CustomerShell>
  );
}
