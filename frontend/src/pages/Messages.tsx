import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DraftCard } from "../components/Blocks";
import { CustomerShell } from "../components/Layout";
import { Empty, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { useLang } from "../lib/i18n";

interface Comm { id: string; claim_id: string | null; claim_reference: string | null; direction: string; kind: string; subject: string; body: string; recipient: string | null; status: string; sent_at: string | null; read_at: string | null; created_at: string }

export default function MessagesPage() {
  const { t, lang } = useLang();
  const [comms, setComms] = useState<Comm[] | null>(null);
  const [tab, setTab] = useState<"inbox" | "sent" | "drafts">("inbox");
  const load = () => api.get<{ communications: Comm[] }>("/api/communications").then((r) => setComms(r.communications));
  useEffect(() => { load(); }, [lang]);
  const list = (comms || []).filter((c) => (tab === "inbox" ? c.direction === "inbound" : tab === "sent" ? c.direction === "outbound" && c.status === "sent" : c.status === "draft"));
  return (
    <CustomerShell title={t("messages")} subtitle={lang === "zh" ? "与保险公司的往来，均关联到具体理赔或保单。" : "Everything exchanged with insurers, each linked to a claim or policy."}>
      <div className="tabs">{(["inbox", "sent", "drafts"] as const).map((k) => <button key={k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{t(k)}</button>)}</div>
      {!comms ? <Spinner /> : list.length === 0 ? <Empty>—</Empty> : (
        <div className="stack">
          {list.map((c) => tab === "drafts" ? <DraftCard key={c.id} comm={c} onSent={load} /> : (
            <div key={c.id} className="card" onClick={() => !c.read_at && api.post(`/api/communications/${c.id}/read`).then(load)} style={{ borderLeft: c.direction === "inbound" && !c.read_at ? "4px solid var(--accent)" : undefined }}>
              <div className="row between">
                <div><strong>{c.subject}</strong><div className="small muted">{c.direction === "inbound" ? (lang === "zh" ? "来自保险公司" : "From the insurer") : `${lang === "zh" ? "发送至" : "To"} ${c.recipient || "insurer"}`}{c.claim_reference && <> · <Link to={`/claims/${c.claim_id}`}>{c.claim_reference}</Link></>}</div></div>
                <span className="small muted">{fmtDateTime(c.sent_at || c.created_at, lang)}</span>
              </div>
              <p style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>{c.body}</p>
            </div>
          ))}
        </div>
      )}
    </CustomerShell>
  );
}
