import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CustomerShell } from "../components/Layout";
import { Badge, Empty, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtDate, money } from "../lib/format";
import { useLang } from "../lib/i18n";

interface Overview {
  metrics: { active_policies: number; annual_premium_by_currency: Record<string, number>; monthly_premium_by_currency: Record<string, number>; renewals_next_60_days: number; open_claims: number; outstanding_actions: number; potential_gaps: number; gap_analysis_at: string | null };
  policies: PolicyCard[];
  actions: { type: string; priority: string; title: string; detail: string; link: string; due_date?: string }[];
  insights: { status: string; severity: string; title: string; detail: string; link: string }[];
  recent_claims: { id: string; reference: string; status: string; status_label: string; incident_type: string; updated_at: string; progress: number | null }[];
}
export interface PolicyCard {
  id: string; provider_name: string; product_name: string; insurance_type: string; insurance_type_label: string; policy_number: string; language: string; currency: string; start_date: string; end_date: string; days_to_end: number; premium_amount: number | null; premium_frequency: string | null; excess_amount: number | null; status: string; renewal_status: string; source: string; summary: Record<string, unknown>; open_claims: number; insurer_org_id?: string | null; extraction_confidence?: number | null;
}

export function PolicyCardView({ p }: { p: PolicyCard }) {
  const { t, lang } = useLang();
  const nav = useNavigate();
  const limits = (p.summary?.key_limits as string[] | undefined) || [];
  return (
    <div className="policy-card" onClick={() => nav(`/policies/${p.id}`)} role="link" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && nav(`/policies/${p.id}`)}>
      <div className="row between">
        <span className="type">{p.insurance_type_label}</span>
        <div className="row" style={{ gap: 6 }}>
          {p.status === "pending_review" ? <Badge tone="warn">{t("pending_review")}</Badge> : <StatusBadge status={p.renewal_status === "renewing_soon" ? "renewing_soon" : p.status} label={p.status === "active" ? (p.renewal_status === "renewing_soon" ? `${t("renews_in")} ${p.days_to_end} ${t("days")}` : lang === "zh" ? "有效" : "Active") : t("expired")} />}
          {p.open_claims > 0 && <Badge tone="info">{p.open_claims} {lang === "zh" ? "理赔" : "claim"}{p.open_claims > 1 && lang !== "zh" ? "s" : ""}</Badge>}
        </div>
      </div>
      <div className="name">{p.provider_name}</div>
      <div style={{ color: "var(--ink-2)" }}>{p.product_name} · <span className="mono">{p.policy_number}</span></div>
      <div className="facts">
        <span>{t("period")}: {fmtDate(p.start_date, lang)} – {fmtDate(p.end_date, lang)}</span>
        <span>{t("premium")}: {money(p.premium_amount, p.currency, lang)}{p.premium_frequency ? `/${p.premium_frequency === "monthly" ? (lang === "zh" ? "月" : "mo") : lang === "zh" ? "年" : "yr"}` : ""}</span>
        <span>{t("excess")}: {money(p.excess_amount, p.currency, lang)}</span>
        {limits[0] && <span>{limits[0]}</span>}
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const { user } = useAuth();
  const { t, lang } = useLang();
  const [data, setData] = useState<Overview | null>(null);
  useEffect(() => {
    api.get<Overview>("/api/overview").then(setData);
  }, []);
  if (!data) return <CustomerShell title={t("overview")}><Spinner /></CustomerShell>;
  const m = data.metrics;
  const cur = Object.keys(m.annual_premium_by_currency)[0] || "GBP";
  const greeting = lang === "zh" ? `你好，${user?.full_name}` : `Hello, ${user?.full_name?.split(" ")[0]}`;
  return (
    <CustomerShell title={greeting} subtitle={lang === "zh" ? "这是你所有保险的概览。" : "Here is where all your insurance stands today."} actions={<Link className="btn primary" to="/assistant">{lang === "zh" ? "问助手" : "Ask the assistant"}</Link>}>
      <div className="grid cols-4" style={{ marginBottom: 18 }}>
        <div className="tile"><div className="label">{t("active_policies")}</div><div className="value">{m.active_policies}</div></div>
        <div className="tile"><div className="label">{t("annual_premium")}</div><div className="value">{money(m.annual_premium_by_currency[cur] || 0, cur, lang)}</div><div className="sub">≈ {money(m.monthly_premium_by_currency[cur] || 0, cur, lang)} {t("monthly_equiv")}</div></div>
        <div className="tile"><div className="label">{t("open_claims")}</div><div className="value">{m.open_claims}</div><div className="sub">{m.renewals_next_60_days} {lang === "zh" ? "份保单60天内到期" : "renewal(s) within 60 days"}</div></div>
        <div className="tile"><div className="label">{t("actions")}</div><div className="value">{m.outstanding_actions}</div><div className="sub">{m.potential_gaps > 0 ? `${m.potential_gaps} ${lang === "zh" ? "个潜在缺口" : "potential gap(s)"}` : m.gap_analysis_at ? (lang === "zh" ? "无缺口" : "no gaps flagged") : lang === "zh" ? "尚未分析" : "no analysis yet"}</div></div>
      </div>
      <div className="grid cols-2">
        <div className="card">
          <h3>{t("actions")}</h3>
          {data.actions.length === 0 ? <Empty>{t("no_actions")}</Empty> : (
            <div className="stack" style={{ gap: 8 }}>
              {data.actions.map((a, i) => (
                <Link key={i} to={a.link} style={{ display: "block", padding: "10px 12px", borderRadius: 10, border: "1px solid var(--line)", color: "inherit", textDecoration: "none", background: a.priority === "high" ? "var(--warn-soft)" : "var(--bg)" }}>
                  <div className="row between"><strong>{a.title}</strong><Badge tone={a.priority === "high" ? "bad" : a.priority === "medium" ? "warn" : ""} plain>{a.priority}</Badge></div>
                  {a.detail && <div className="small muted">{a.detail}</div>}
                </Link>
              ))}
            </div>
          )}
        </div>
        <div className="card">
          <div className="card-head"><h3>{t("insights")}</h3><Link className="small" to="/gaps">{lang === "zh" ? "打开缺口分析 →" : "Open gap analysis →"}</Link></div>
          {data.insights.length === 0 ? <Empty>{t("run_gap_prompt")}</Empty> : (
            <div className="stack" style={{ gap: 8 }}>
              {data.insights.map((x, i) => (
                <div key={i} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
                  <div className="row"><StatusBadge status={x.status} label={t(x.status)} /><strong>{x.title}</strong></div>
                  <div className="small" style={{ color: "var(--ink-2)" }}>{x.detail}</div>
                </div>
              ))}
              <div className="small muted">{lang === "zh" ? "以上为潜在问题，不是保障结论。" : "Potential issues, not coverage determinations."}</div>
            </div>
          )}
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-head"><h3>{t("portfolio")}</h3><Link className="btn sm" to="/policies">{t("view")}</Link></div>
        <div className="grid cols-2">
          {data.policies.map((p) => <PolicyCardView key={p.id} p={p} />)}
        </div>
      </div>
      {data.recent_claims.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-head"><h3>{t("recent_claims")}</h3><Link className="btn sm" to="/claims">{t("view")}</Link></div>
          <table className="table">
            <thead><tr><th>Ref</th><th>{t("incident")}</th><th>{t("status")}</th><th>{lang === "zh" ? "更新" : "Updated"}</th></tr></thead>
            <tbody>
              {data.recent_claims.map((c) => (
                <tr key={c.id} className="clickable" onClick={() => (window.location.href = `/claims/${c.id}`)}>
                  <td className="mono">{c.reference}</td><td>{c.incident_type.replace(/_/g, " ")}</td><td><StatusBadge status={c.status} label={c.status_label} /></td><td className="muted">{fmtDate(c.updated_at, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CustomerShell>
  );
}
