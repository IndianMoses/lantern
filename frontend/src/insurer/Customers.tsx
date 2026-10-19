import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { InsurerShell } from "../components/Layout";
import { Empty, Spinner, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtDate, fmtDateTime } from "../lib/format";
import { useLang } from "../lib/i18n";
import { PolicyCardView, type PolicyCard } from "../pages/Overview";
import type { QueueRow } from "./Queue";

interface Row { id: string; name: string; email: string; locale: string; policies: number; active_policies: number; open_claims: number; claims: number; next_renewal: string | null }

export default function CustomersPage() {
  const { lang } = useLang();
  const nav = useNavigate();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [q, setQ] = useState("");
  useEffect(() => { api.get<{ customers: Row[] }>(`/api/insurer/customers${q ? `?q=${encodeURIComponent(q)}` : ""}`).then((r) => setRows(r.customers)); }, [q, lang]);
  return (
    <InsurerShell title={lang === "zh" ? "客户" : "Customers"} subtitle={lang === "zh" ? "仅显示与本公司有保单或理赔关系的客户。" : "Only customers with a policy or claim administered by this organisation."}>
      <input className="input" style={{ maxWidth: 320, marginBottom: 12 }} placeholder={lang === "zh" ? "搜索姓名或邮箱" : "Search name or email"} onKeyDown={(e) => e.key === "Enter" && setQ((e.target as HTMLInputElement).value)} />
      <div className="card" style={{ padding: 0 }}>
        {!rows ? <div style={{ padding: 20 }}><Spinner /></div> : rows.length === 0 ? <Empty>—</Empty> : (
          <table className="table"><thead><tr><th>{lang === "zh" ? "客户" : "Customer"}</th><th>{lang === "zh" ? "保单" : "Policies"}</th><th>{lang === "zh" ? "进行中理赔" : "Open claims"}</th><th>{lang === "zh" ? "下次续保" : "Next renewal"}</th></tr></thead>
            <tbody>{rows.map((r) => <tr key={r.id} className="clickable" onClick={() => nav(`/insurer/customers/${r.id}`)}><td><Link to={`/insurer/customers/${r.id}`} onClick={(e) => e.stopPropagation()}>{r.name}</Link><div className="small muted">{r.email} · {r.locale}</div></td><td>{r.active_policies} / {r.policies}</td><td>{r.open_claims} / {r.claims}</td><td>{fmtDate(r.next_renewal, lang)}</td></tr>)}</tbody></table>
        )}
      </div>
    </InsurerShell>
  );
}

export function CustomerDetailPage() {
  const { id } = useParams();
  const { lang } = useLang();
  const [d, setD] = useState<{ customer: Row; policies: PolicyCard[]; claims: QueueRow[]; communications: { id: string; direction: string; subject: string; sent_at: string | null; claim_id: string | null }[] } | null>(null);
  useEffect(() => { api.get<typeof d>(`/api/insurer/customers/${id}`).then(setD); }, [id, lang]);
  if (!d) return <InsurerShell><Spinner /></InsurerShell>;
  return (
    <InsurerShell title={d.customer.name} subtitle={d.customer.email}>
      <div className="grid cols-2">
        <div className="card"><h3>{lang === "zh" ? "保单" : "Policies"}</h3><div className="stack">{d.policies.map((p) => <div key={p.id} style={{ pointerEvents: "none" }}><PolicyCardView p={p} /></div>)}</div></div>
        <div className="stack">
          <div className="card"><h3>{lang === "zh" ? "理赔" : "Claims"}</h3>{d.claims.map((c) => <div key={c.id} className="row between" style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}><Link to={`/insurer/claims/${c.id}`} className="mono">{c.reference}</Link><span className="small">{c.incident_type_label}</span><StatusBadge status={c.status} label={c.status_label} /></div>)}{d.claims.length === 0 && <div className="muted small">—</div>}</div>
          <div className="card"><h3>{lang === "zh" ? "沟通记录" : "Communication history"}</h3>{d.communications.map((m) => <div key={m.id} className="small" style={{ padding: "4px 0" }}>{m.direction === "outbound" ? "←" : "→"} {m.subject} <span className="muted">{fmtDateTime(m.sent_at, lang)}</span></div>)}{d.communications.length === 0 && <div className="muted small">—</div>}</div>
        </div>
      </div>
    </InsurerShell>
  );
}

export function TeamPage() {
  const { lang } = useLang();
  const { user } = useAuth();
  const [team, setTeam] = useState<{ id: string; name: string; role: string; email: string }[]>([]);
  const [audit, setAudit] = useState<{ events: { seq: number; action: string; actor_type: string; actor_id: string | null; target_type: string | null; outcome: string; created_at: string; hash: string; details: Record<string, unknown> }[]; chain: { ok: boolean; checked: number; head?: string } } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<{ team: typeof team }>("/api/insurer/team").then((r) => setTeam(r.team));
    if (user?.role === "insurer_admin") api.get<typeof audit>("/api/insurer/audit?limit=150").then(setAudit).catch((e) => setErr(String(e)));
  }, [user?.role]);
  return (
    <InsurerShell title={lang === "zh" ? "团队与审计" : "Team & audit"}>
      <div className="grid cols-2">
        <div className="card"><h3>{lang === "zh" ? "团队" : "Team"}</h3><table className="table"><tbody>{team.map((s) => <tr key={s.id}><td>{s.name}</td><td>{s.role.replace("insurer_", "")}</td><td className="muted">{s.email}</td></tr>)}</tbody></table></div>
        <div className="card">
          <h3>{lang === "zh" ? "审计日志（哈希链）" : "Audit log (hash chained)"}</h3>
          {user?.role !== "insurer_admin" ? <div className="alert info small">{lang === "zh" ? "仅管理员可查看完整审计日志。" : "Only organisation administrators can view the full audit log."}</div> : !audit ? (err ? <div className="alert bad">{err}</div> : <Spinner />) : (
            <>
              <div className={`alert ${audit.chain.ok ? "ok" : "bad"} small`}>{audit.chain.ok ? `${lang === "zh" ? "链完整" : "Chain verified"} · ${audit.chain.checked} ${lang === "zh" ? "条记录" : "events"} · head ${audit.chain.head?.slice(0, 16)}…` : `${lang === "zh" ? "链被破坏" : "Chain broken"}`}</div>
              <div style={{ maxHeight: 520, overflow: "auto", marginTop: 8 }}>
                <table className="table"><tbody>{audit.events.map((a) => <tr key={a.seq}><td className="mono small">#{a.seq}</td><td className="small">{a.action}<div className="muted">{a.actor_type} · {a.target_type || ""} · {a.outcome}</div></td><td className="small muted">{fmtDateTime(a.created_at, lang)}<div className="mono">{a.hash.slice(0, 16)}</div></td></tr>)}</tbody></table>
              </div>
            </>
          )}
        </div>
      </div>
    </InsurerShell>
  );
}
