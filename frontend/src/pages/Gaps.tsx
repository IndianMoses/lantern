import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { GapFindings, type Finding } from "../components/Blocks";
import { CustomerShell } from "../components/Layout";
import { CitationProvider } from "../components/Markdown";
import { Empty, Spinner, useToast } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { useLang } from "../lib/i18n";

interface Analysis { id: string; created_at: string; findings: Finding[]; summary: { counts: Record<string, number>; policies_considered: string[]; assets_considered: number; activities_considered: number }; disclaimer: Record<string, string> }

export default function GapsPage() {
  const { t, lang, pick } = useLang();
  const toast = useToast();
  const [analysis, setAnalysis] = useState<Analysis | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [showCovered, setShowCovered] = useState(true);
  useEffect(() => { api.get<{ analysis: Analysis | null }>("/api/gaps/latest").then((r) => setAnalysis(r.analysis)); }, []);
  const run = async () => {
    setBusy(true);
    try {
      setAnalysis(await api.post<Analysis>("/api/gaps/analyze"));
    } catch (e) {
      toast(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const counts = analysis?.summary.counts || {};
  return (
    <CustomerShell title={t("gaps")} subtitle={lang === "zh" ? "把你申报的资产和活动与每份保单的条款逐一对照。" : "Your declared assets and activities, checked clause by clause against every active policy."} actions={<button className="btn primary" disabled={busy} onClick={run}>{busy ? <Spinner /> : analysis ? t("rerun_analysis") : t("run_analysis")}</button>}>
      {analysis === undefined ? <Spinner /> : !analysis ? (
        <Empty>
          <p>{lang === "zh" ? "还没有分析。分析使用你在“设置”中申报的资产和活动。" : "No analysis yet. It uses the assets and activities you declared in Settings."}</p>
          <Link className="btn" to="/settings">{t("settings")}</Link>
        </Empty>
      ) : (
        <CitationProvider clauses={analysis.findings.flatMap((f) => f.evidence)}>
          <div className="grid cols-4" style={{ marginBottom: 16 }}>
            {(["gap", "partial", "duplicate", "covered"] as const).map((k) => (
              <div className="tile" key={k} style={{ borderTop: `3px solid var(--${k === "gap" ? "bad" : k === "partial" ? "warn" : k === "duplicate" ? "purple" : "ok"})` }}>
                <div className="label">{t(k)}</div><div className="value">{counts[k] || 0}</div>
              </div>
            ))}
          </div>
          <div className="card">
            <div className="card-head">
              <div>
                <h3>{lang === "zh" ? "发现" : "Findings"}</h3>
                <div className="small muted">{fmtDateTime(analysis.created_at, lang)} · {analysis.summary.assets_considered} {lang === "zh" ? "项资产" : "assets"}, {analysis.summary.activities_considered} {lang === "zh" ? "项活动" : "activities"}, {analysis.summary.policies_considered.length} {lang === "zh" ? "份保单" : "policies"}: {analysis.summary.policies_considered.join(", ")}</div>
              </div>
              <label className="row small"><input type="checkbox" checked={showCovered} onChange={(e) => setShowCovered(e.target.checked)} /> {lang === "zh" ? "显示已保障项" : "Show covered items"}</label>
            </div>
            <GapFindings findings={analysis.findings} showCovered={showCovered} />
            <div className="alert" style={{ marginTop: 12 }}>{pick(analysis.disclaimer)}</div>
          </div>
        </CitationProvider>
      )}
    </CustomerShell>
  );
}
