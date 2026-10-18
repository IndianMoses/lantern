import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BarChart, LineChart, StackedBars } from "../components/Charts";
import { InsurerShell } from "../components/Layout";
import { Spinner } from "../components/ui";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { useLang } from "../lib/i18n";

interface Metric { value: number | null; definition: string }
interface Dashboard {
  organization: { name: string; name_zh?: string; integration_mode: string };
  metrics: Record<string, Metric> & { amounts: { currency: string; submitted: Metric; approved: Metric; settled: Metric; outstanding: Metric; average_settlement: Metric }; sla_breaches: Metric & { first_response: number; decision: number } };
  charts: { claims_by_status: { status: string; label: string; count: number }[]; claims_by_type: { type: string; label: string; count: number }[]; claims_received_weekly: { week: string; count: number }[]; avg_processing_days_by_type: { type: string; label: string; days: number; n: number }[]; outcomes_by_month: Record<string, unknown>[]; workload_by_handler: { handler_id: string | null; handler: string; open: number; awaiting_customer: number }[]; document_processing: { status: string; count: number }[] };
  data_note: string;
  generated_at: string;
}

export default function InsurerDashboard() {
  const { lang } = useLang();
  const nav = useNavigate();
  const [d, setD] = useState<Dashboard | null>(null);
  const [team, setTeam] = useState<{ id: string; name: string }[]>([]);
  const [filters, setFilters] = useState({ insurance_type: "", handler_id: "", date_from: "", date_to: "" });
  useEffect(() => { api.get<{ team: typeof team }>("/api/insurer/team").then((r) => setTeam(r.team)); }, []);
  useEffect(() => {
    const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
    api.get<Dashboard>(`/api/insurer/dashboard?${q}`).then(setD);
  }, [filters]);
  if (!d) return <InsurerShell><Spinner /></InsurerShell>;
  const m = d.metrics;
  const tile = (key: string, label: string, fmt?: (v: number | null) => string) => {
    const mt = m[key] as Metric;
    return (
      <div className="tile" key={key} title={mt.definition}>
        <div className="label">{label}</div>
        <div className="value">{mt.value == null ? "—" : fmt ? fmt(mt.value) : mt.value}</div>
        <div className="sub">{mt.definition}</div>
      </div>
    );
  };
  const cur = m.amounts.currency;
  return (
    <InsurerShell title={lang === "zh" && d.organization.name_zh ? d.organization.name_zh : d.organization.name} subtitle={lang === "zh" ? "运营概览 · 实时计算自数据库" : "Operations overview · computed live from the database"} actions={<span className="demo-note">{d.organization.integration_mode === "mock" ? (lang === "zh" ? "模拟接口" : "mock integration") : d.organization.integration_mode}</span>}>
      <div className="row" style={{ marginBottom: 14 }}>
        <select className="select" style={{ maxWidth: 200 }} value={filters.insurance_type} onChange={(e) => setFilters({ ...filters, insurance_type: e.target.value })}><option value="">{lang === "zh" ? "全部险种" : "All products"}</option>{["home", "renters", "motor", "travel", "ebike", "gadget", "pet"].map((x) => <option key={x}>{x}</option>)}</select>
        <select className="select" style={{ maxWidth: 220 }} value={filters.handler_id} onChange={(e) => setFilters({ ...filters, handler_id: e.target.value })}><option value="">{lang === "zh" ? "全部理赔员" : "All handlers"}</option>{team.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        <input className="input" style={{ maxWidth: 160 }} type="date" value={filters.date_from} onChange={(e) => setFilters({ ...filters, date_from: e.target.value })} />
        <input className="input" style={{ maxWidth: 160 }} type="date" value={filters.date_to} onChange={(e) => setFilters({ ...filters, date_to: e.target.value })} />
        <span className="small muted">{lang === "zh" ? "按提交日期筛选" : "filters apply to submission date"}</span>
      </div>
      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        {tile("open_claims", lang === "zh" ? "进行中理赔" : "Open claims")}
        {tile("awaiting_handler_action", lang === "zh" ? "待理赔员处理" : "Awaiting handler")}
        {tile("awaiting_customer_information", lang === "zh" ? "待客户补充" : "Awaiting customer")}
        {tile("sla_breaches", lang === "zh" ? "超出服务目标" : "Over service target")}
        {tile("new_claims_7d", lang === "zh" ? "7天新增" : "New in 7 days")}
        {tile("decided", lang === "zh" ? "已出结论" : "Decided")}
        {tile("avg_processing_days", lang === "zh" ? "平均处理天数" : "Avg processing days")}
        {tile("avg_first_response_hours", lang === "zh" ? "平均首次响应（小时）" : "Avg first response (h)")}
        {tile("total_policies_managed", lang === "zh" ? "管理保单" : "Policies managed")}
        {tile("active_policies", lang === "zh" ? "有效保单" : "Active policies")}
        {tile("renewals_next_60_days", lang === "zh" ? "60天内续保" : "Renewals in 60 days")}
        {tile("communications_30d", lang === "zh" ? "30天沟通量" : "Messages in 30 days")}
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{lang === "zh" ? "金额（分别统计，不合并）" : "Amounts (kept separate, never combined)"}</h3>
        <div className="grid cols-4">
          {(["submitted", "approved", "settled", "outstanding"] as const).map((k) => (
            <div className="tile" key={k} title={m.amounts[k].definition}><div className="label">{k}</div><div className="value">{money(m.amounts[k].value, cur, lang)}</div><div className="sub">{m.amounts[k].definition}</div></div>
          ))}
        </div>
      </div>
      <div className="grid cols-2">
        <div className="card"><h3>{lang === "zh" ? "按状态" : "Claims by status"}</h3><BarChart data={d.charts.claims_by_status} horizontal /><div className="small muted">{lang === "zh" ? "点击队列中的状态筛选可下钻" : "Drill down from the queue filters"}</div></div>
        <div className="card"><h3>{lang === "zh" ? "每周接收" : "Claims received per week"}</h3><LineChart data={d.charts.claims_received_weekly} /></div>
        <div className="card"><h3>{lang === "zh" ? "按险种" : "Claims by product"}</h3><BarChart data={d.charts.claims_by_type} color="#2b5aa6" /></div>
        <div className="card"><h3>{lang === "zh" ? "理赔员工作量" : "Workload by handler"}</h3><BarChart data={d.charts.workload_by_handler.map((w) => ({ label: w.handler, count: w.open }))} horizontal color="#6b4fa0" /><div className="row small muted" style={{ gap: 12 }}>{d.charts.workload_by_handler.map((w) => <span key={w.handler} style={{ cursor: "pointer" }} onClick={() => nav(`/insurer/claims?handler=${w.handler_id || "unassigned"}`)}>{w.handler}: {w.open} {lang === "zh" ? "进行中" : "open"}{w.awaiting_customer ? ` (${w.awaiting_customer} ${lang === "zh" ? "待客户" : "awaiting customer"})` : ""}</span>)}</div></div>
        <div className="card"><h3>{lang === "zh" ? "各险种平均处理天数" : "Average processing days by product"}</h3>{d.charts.avg_processing_days_by_type.length ? <BarChart data={d.charts.avg_processing_days_by_type} valueKey="days" color="#b7791f" /> : <div className="muted small">—</div>}</div>
        <div className="card"><h3>{lang === "zh" ? "各月结论" : "Outcomes by month"}</h3>{d.charts.outcomes_by_month.length ? <StackedBars data={d.charts.outcomes_by_month} keys={["approved", "partially_approved", "declined"]} /> : <div className="muted small">—</div>}</div>
        <div className="card"><h3>{lang === "zh" ? "文件处理" : "Document processing"}</h3><BarChart data={d.charts.document_processing.map((x) => ({ label: x.status, count: x.count }))} horizontal color="#2f7d4f" /></div>
      </div>
      <div className="small muted" style={{ marginTop: 12 }}>{d.data_note} · {d.generated_at}</div>
    </InsurerShell>
  );
}
