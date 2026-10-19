import { useEffect, useState } from "react";
import { BankShell } from "../components/Layout";
import { Badge, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { useLang } from "../lib/i18n";

interface Metric { value: number | null; definition: string; responses?: number | null }
interface Overview { organization: { name: string; name_zh?: string }; metrics: Record<string, Metric>; k_anonymity: number; note: string; generated_at: string }

const LABELS: Record<string, { en: string; zh: string; kind?: "rate" | "count" }> = {
  customers_total: { en: "Customers on the platform", zh: "平台上的客户" }, customers_counted: { en: "Counted (consented)", zh: "计入统计（已授权）" }, active_30d: { en: "Active in 30 days", zh: "30天活跃" }, engagement_rate_30d: { en: "Engagement rate", zh: "活跃率", kind: "rate" },
  coverage_analysis_completion: { en: "Coverage analysis completion", zh: "保障分析完成率", kind: "rate" }, policies_organised: { en: "Policies organised", zh: "已整理保单" }, active_policies: { en: "Active policies", zh: "有效保单" }, potential_gaps_identified: { en: "Potential gaps identified", zh: "识别的潜在缺口" },
  customers_with_open_tasks: { en: "Customers with open insurance tasks", zh: "有待办保险事项的客户" }, open_claims: { en: "Open claims", zh: "进行中理赔" }, support_interactions: { en: "Assistant conversations", zh: "助手对话" }, assistant_runs: { en: "Assistant turns", zh: "助手回合" },
  assistant_resolution_rate: { en: "Assistant resolution rate", zh: "助手解决率", kind: "rate" }, assistant_escalation_rate: { en: "Escalation to a person", zh: "升级人工比例", kind: "rate" }, assistant_failure_rate: { en: "Assistant failure rate", zh: "助手失败率", kind: "rate" },
  citation_verification_rate: { en: "Citations verified", zh: "引用核实率", kind: "rate" }, provider_fallbacks: { en: "Model fallbacks", zh: "模型回退次数" }, finding_verification_rate: { en: "Findings verified", zh: "发现核实率", kind: "rate" }, analysis_runs_models: { en: "Analyses with three models", zh: "三模型分析次数" }, analysis_runs_mixed: { en: "Analyses with a fallback", zh: "含回退的分析次数" }, satisfaction: { en: "Answers rated helpful", zh: "评价为有帮助的回答", kind: "rate" },
};
const GROUPS: [string, string, string[]][] = [
  ["engagement", "Engagement", ["customers_total", "customers_counted", "active_30d", "engagement_rate_30d", "support_interactions", "satisfaction"]],
  ["insurance", "Insurance outcomes", ["policies_organised", "active_policies", "coverage_analysis_completion", "potential_gaps_identified", "customers_with_open_tasks", "open_claims"]],
  ["ai", "AI quality and verification", ["assistant_runs", "assistant_resolution_rate", "assistant_failure_rate", "assistant_escalation_rate", "citation_verification_rate", "finding_verification_rate", "provider_fallbacks", "analysis_runs_models", "analysis_runs_mixed"]],
];
const GROUP_ZH: Record<string, string> = { engagement: "参与度", insurance: "保险成效", ai: "AI 质量与核验" };

function fmt(m: Metric, kind?: string, lang = "en"): string {
  if (m.value === null || m.value === undefined) return lang === "zh" ? "不适用" : "n/a";
  if (kind === "rate") return `${Math.round(m.value * 100)}%`;
  return String(m.value);
}

export default function BankDashboard() {
  const { lang } = useLang();
  const [d, setD] = useState<Overview | null>(null);
  useEffect(() => { api.get<Overview>("/api/bank/overview").then(setD); }, [lang]);
  if (!d) return <BankShell><Spinner /></BankShell>;
  return (
    <BankShell title={lang === "zh" && d.organization.name_zh ? d.organization.name_zh : d.organization.name} subtitle={lang === "zh" ? "汇总统计 · 不含任何可识别客户信息" : "Aggregate statistics · no identifiable customer information"} actions={<Badge plain>{lang === "zh" ? `小样本抑制阈值 ${d.k_anonymity}` : `cells under ${d.k_anonymity} suppressed`}</Badge>}>
      {GROUPS.map(([key, title, keys]) => (
        <div className="card" key={key} style={{ marginBottom: 16 }}>
          <h3>{lang === "zh" ? GROUP_ZH[key] : title}</h3>
          <div className="grid cols-3">
            {keys.map((k) => {
              const m = d.metrics[k];
              if (!m) return null;
              const l = LABELS[k];
              return (
                <div className="tile" key={k} title={m.definition}>
                  <div className="label">{l?.[lang] || k}</div>
                  <div className="value">{fmt(m, l?.kind, lang)}</div>
                  <div className="sub">{m.definition}{m.responses != null ? ` · ${m.responses} ${lang === "zh" ? "条评价" : "ratings"}` : ""}</div>
                </div>
              );
            })}
          </div>
        </div>
      ))}
      <div className="small muted">{d.note} · {fmtDateTime(d.generated_at, lang)}</div>
    </BankShell>
  );
}

interface Integration { interfaces: { interface: string; label: string; method: string; mode: string; status: string; calls: number; last_call_at: string | null; note: string }[]; consents: { purpose: string; label: Record<string, string>; granted: number; declined: number; default: number }[]; customers: number; data_boundary: { ingested: string[]; never_ingested: string[] } }

export function BankIntegrationPage() {
  const { lang, pick } = useLang();
  const [d, setD] = useState<Integration | null>(null);
  useEffect(() => { api.get<Integration>("/api/bank/integration").then(setD); }, [lang]);
  if (!d) return <BankShell><Spinner /></BankShell>;
  return (
    <BankShell title={lang === "zh" ? "集成" : "Integration"} subtitle={lang === "zh" ? "八个与供应商无关的接口；当前全部为模拟适配器。" : "Eight provider agnostic interfaces; every one is currently a mock adapter."}>
      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{lang === "zh" ? "接口与健康状态" : "Interfaces and health"}</h3>
        <p className="small muted" style={{ marginTop: 0 }}>{lang === "zh" ? "每个接口都是模拟适配器，没有任何实时银行连接。替换方式：在 backend/app/services/bank.py 的 adapters_for() 中注册银行的实现，接口定义保持不变。" : "Every interface is a mock adapter; no live bank connection exists. To replace one, register the bank's implementation in adapters_for() in backend/app/services/bank.py; the interface stays the same."}</p>
        <table className="table stack-sm">
          <thead><tr><th>{lang === "zh" ? "接口" : "Interface"}</th><th>{lang === "zh" ? "方法" : "Method"}</th><th>{lang === "zh" ? "模式" : "Mode"}</th><th>{lang === "zh" ? "调用" : "Calls"}</th><th>{lang === "zh" ? "最近调用" : "Last call"}</th></tr></thead>
          <tbody>
            {d.interfaces.map((i) => (
              <tr key={i.interface}><td data-label={lang === "zh" ? "接口" : "Interface"}>{i.label}</td><td data-label={lang === "zh" ? "方法" : "Method"} className="mono small">{i.method}</td><td data-label={lang === "zh" ? "模式" : "Mode"}><Badge tone={i.mode === "mock" ? "warn" : "ok"} plain>{i.mode}</Badge></td><td data-label={lang === "zh" ? "调用" : "Calls"} className="num">{i.calls}</td><td data-label={lang === "zh" ? "最近调用" : "Last call"} className="muted num">{i.last_call_at ? fmtDateTime(i.last_call_at, lang) : "—"}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid cols-2">
        <div className="card">
          <h3>{lang === "zh" ? "客户授权（汇总）" : "Customer consent (aggregate)"}</h3>
          <div className="small muted" style={{ marginBottom: 8 }}>{d.customers} {lang === "zh" ? "位客户" : "customers"}</div>
          <table className="table">
            <thead><tr><th>{lang === "zh" ? "用途" : "Purpose"}</th><th>{lang === "zh" ? "已授权" : "Granted"}</th><th>{lang === "zh" ? "已拒绝" : "Declined"}</th></tr></thead>
            <tbody>{d.consents.map((c) => <tr key={c.purpose}><td>{pick(c.label)}</td><td className="num">{c.granted}</td><td className="num">{c.declined}</td></tr>)}</tbody>
          </table>
        </div>
        <div className="card">
          <h3>{lang === "zh" ? "数据边界" : "Data boundary"}</h3>
          <div className="small"><b>{lang === "zh" ? "采集：" : "Ingested: "}</b>{d.data_boundary.ingested.join("; ")}</div>
          <div className="small" style={{ marginTop: 6 }}><b>{lang === "zh" ? "从不采集：" : "Never ingested: "}</b>{d.data_boundary.never_ingested.join("; ")}</div>
          <div className="small muted" style={{ marginTop: 10 }}>{lang === "zh" ? "详见 docs/BANK_INTEGRATION.md 中的集成与安全清单。" : "See the integration and security checklist in docs/BANK_INTEGRATION.md."}</div>
        </div>
      </div>
    </BankShell>
  );
}

interface AuditOut { events: { seq: number; action: string; actor_type: string; target_type: string | null; outcome: string; created_at: string; hash: string }[]; chain: { ok: boolean; checked: number | null; head: string | null } }

export function BankAuditPage() {
  const { lang } = useLang();
  const [d, setD] = useState<AuditOut | null>(null);
  useEffect(() => { api.get<AuditOut>("/api/bank/audit?limit=200").then(setD); }, [lang]);
  if (!d) return <BankShell><Spinner /></BankShell>;
  return (
    <BankShell title={lang === "zh" ? "审计" : "Audit"} subtitle={lang === "zh" ? "银行员工的访问记录与集成事件（哈希链）" : "The bank's own access trail and integration events (hash chained)"}>
      <div className={`alert ${d.chain.ok ? "ok" : "bad"} small`} style={{ marginBottom: 12 }}>{d.chain.ok ? `${lang === "zh" ? "链完整" : "Chain verified"} · ${d.chain.checked} ${lang === "zh" ? "条记录" : "events"}` : lang === "zh" ? "链被破坏" : "Chain broken"}</div>
      <div className="card" style={{ padding: 0, overflow: "auto" }}>
        <table className="table"><thead><tr><th>#</th><th>{lang === "zh" ? "操作" : "Action"}</th><th>{lang === "zh" ? "主体" : "Actor"}</th><th>{lang === "zh" ? "结果" : "Outcome"}</th><th>{lang === "zh" ? "时间" : "When"}</th></tr></thead>
          <tbody>{d.events.map((e) => <tr key={e.seq}><td className="mono">#{e.seq}</td><td>{e.action}<div className="small muted mono">{e.hash.slice(0, 16)}</div></td><td>{e.actor_type}</td><td><Badge tone={e.outcome === "success" ? "ok" : "warn"} plain>{e.outcome}</Badge></td><td className="muted num">{fmtDateTime(e.created_at, lang)}</td></tr>)}</tbody>
        </table>
      </div>
    </BankShell>
  );
}
