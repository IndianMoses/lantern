import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CustomerShell } from "../components/Layout";
import { Badge, Empty, Spinner, useToast } from "../components/ui";
import { api, errorMessage } from "../lib/api";
import { fmtDateTime } from "../lib/format";
import { useLang } from "../lib/i18n";

interface Doc { id: string; filename: string; mime_type: string; size_bytes: number; doc_type: string; claim_id: string | null; policy_id: string | null; extraction_status: string; injection_flags: unknown[]; submitted_at: string | null; created_at: string; sha256: string; is_demo_data: boolean }

export default function DocumentsPage() {
  const { t, lang } = useLang();
  const toast = useToast();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const load = () => api.get<{ documents: Doc[] }>("/api/documents").then((r) => setDocs(r.documents));
  useEffect(() => { load(); }, []);
  const upload = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    form.append("doc_type", "other");
    try {
      await api.upload("/api/documents/upload", form);
      load();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const remove = async (d: Doc) => {
    if (!confirm(lang === "zh" ? "删除此文件？" : "Delete this document?")) return;
    try {
      await api.del(`/api/documents/${d.id}`);
      load();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  return (
    <CustomerShell title={t("documents")} subtitle={lang === "zh" ? "保单条款、理赔证据和往来信函。原件会被完整保留。" : "Policy wordings, claim evidence and correspondence. Originals are preserved unchanged."} actions={<label className="btn primary">{t("upload")}<input type="file" style={{ display: "none" }} accept=".pdf,.txt,.md,.png,.jpg,.jpeg,.webp" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} /></label>}>
      {!docs ? <Spinner /> : docs.length === 0 ? <Empty>{lang === "zh" ? "暂无文件" : "No documents yet."}</Empty> : (
        <div className="card">
          <table className="table">
            <thead><tr><th>{lang === "zh" ? "文件" : "File"}</th><th>{lang === "zh" ? "类型" : "Type"}</th><th>{lang === "zh" ? "关联" : "Linked to"}</th><th>{lang === "zh" ? "文字提取" : "Text"}</th><th>{lang === "zh" ? "上传时间" : "Uploaded"}</th><th></th></tr></thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id}>
                  <td><a href={`/api/documents/${d.id}/download`} target="_blank" rel="noreferrer">{d.filename}</a><div className="small muted">{(d.size_bytes / 1024).toFixed(0)} KB · sha256 {d.sha256.slice(0, 10)}…</div>{d.injection_flags?.length ? <Badge tone="warn" plain>⚠ {lang === "zh" ? "含类似指令的文字（已忽略）" : "instruction like text (ignored)"}</Badge> : null}</td>
                  <td>{d.doc_type}</td>
                  <td>{d.claim_id ? <Link to={`/claims/${d.claim_id}`}>{lang === "zh" ? "理赔" : "claim"}</Link> : d.policy_id ? <Link to={`/policies/${d.policy_id}`}>{lang === "zh" ? "保单" : "policy"}</Link> : "—"}</td>
                  <td>{d.extraction_status}</td>
                  <td className="muted">{fmtDateTime(d.created_at, lang)}{d.submitted_at && <div className="small">{lang === "zh" ? "已发送给保险公司" : "shared with insurer"}</div>}</td>
                  <td>{!d.submitted_at && !d.policy_id && !d.is_demo_data && <button className="btn sm" onClick={() => remove(d)}>✕</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CustomerShell>
  );
}
