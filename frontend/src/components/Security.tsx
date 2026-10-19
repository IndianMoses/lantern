import { useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import { fmtDateTime } from "../lib/format";
import { useLang } from "../lib/i18n";
import { Icon } from "./Icons";
import { Badge, Field, useToast } from "./ui";

interface SessionRow { id: string; current: boolean; ip: string | null; user_agent: string | null; created_at: string; last_seen_at: string; expires_at: string; mfa_passed: boolean }

/** Account security: sessions, password, multi factor authentication. Used by every role. */
export function SecurityPanel() {
  const { t, lang } = useLang();
  const { user, refresh } = useAuth();
  const toast = useToast();
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [pw, setPw] = useState({ current: "", next: "" });
  const [mfa, setMfa] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const load = () => api.get<{ sessions: SessionRow[] }>("/api/auth/sessions").then((r) => setSessions(r.sessions)).catch(() => {});
  useEffect(() => { load(); }, []);
  const revokeOthers = async () => {
    const r = await api.post<{ revoked: number }>("/api/auth/sessions/revoke", { all_others: true });
    toast(`${r.revoked} ${lang === "zh" ? "个会话已注销" : "session(s) signed out"}`);
    load();
  };
  const changePw = async () => {
    try {
      const r = await api.post<{ sessions_revoked: number }>("/api/auth/change-password", { current_password: pw.current, new_password: pw.next });
      toast(lang === "zh" ? `密码已更改，其他 ${r.sessions_revoked} 个会话已注销` : `Password changed; ${r.sessions_revoked} other session(s) signed out`);
      setPw({ current: "", next: "" });
      load();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const setupMfa = async () => {
    try {
      setMfa(await api.post<{ secret: string; otpauth_uri: string }>("/api/auth/mfa/setup"));
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const enableMfa = async () => {
    try {
      await api.post("/api/auth/mfa/enable", { code });
      toast(lang === "zh" ? "已启用双重验证" : "Multi factor authentication enabled");
      setMfa(null);
      setCode("");
      await refresh();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const disableMfa = async () => {
    try {
      await api.post("/api/auth/mfa/disable", { code });
      toast(lang === "zh" ? "已关闭双重验证" : "Multi factor authentication disabled");
      setCode("");
      await refresh();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  return (
    <div className="stack">
      <div>
        <div className="row between"><h3 style={{ margin: 0 }}>{t("sessions")}</h3><button className="btn sm" onClick={revokeOthers} disabled={sessions.length < 2}><Icon name="logout" size={14} /> {t("revoke_others")}</button></div>
        <div className="table-wrap" style={{ marginTop: 8 }}>
          <table className="table">
            <thead><tr><th>{lang === "zh" ? "设备" : "Device"}</th><th>IP</th><th>{lang === "zh" ? "最近活动" : "Last seen"}</th><th>{lang === "zh" ? "到期" : "Expires"}</th></tr></thead>
            <tbody>
              {sessions.map((s) => (
                <tr key={s.id}>
                  <td>{s.current && <Badge tone="ok" plain>{t("this_device")}</Badge>} <span className="small muted">{(s.user_agent || "").slice(0, 60) || "—"}</span>{s.mfa_passed && <Badge plain>MFA</Badge>}</td>
                  <td className="mono">{s.ip || "—"}</td>
                  <td className="muted num">{fmtDateTime(s.last_seen_at, lang)}</td>
                  <td className="muted num">{fmtDateTime(s.expires_at, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="divider" />
      <div>
        <h3>{t("change_password")}</h3>
        <div className="grid cols-2">
          <Field label={t("current_password")}><input className="input" type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} /></Field>
          <Field label={t("new_password")} hint={lang === "zh" ? "至少 10 个字符" : "At least 10 characters"}><input className="input" type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} /></Field>
        </div>
        <button className="btn sm" style={{ marginTop: 8 }} disabled={pw.current.length < 8 || pw.next.length < 10} onClick={changePw}>{t("change_password")}</button>
      </div>
      <div className="divider" />
      <div>
        <div className="row between"><h3 style={{ margin: 0 }}>{lang === "zh" ? "双重验证（TOTP）" : "Multi factor authentication (TOTP)"}</h3>{user?.mfa_enabled ? <Badge tone="ok">{lang === "zh" ? "已启用" : "enabled"}</Badge> : <Badge plain>{lang === "zh" ? "未启用" : "not enabled"}</Badge>}</div>
        <p className="small muted" style={{ marginTop: 6 }}>{lang === "zh" ? "适用于任何验证器应用。管理员和银行账户建议启用；生产环境可强制要求。" : "Works with any authenticator app. Recommended for administrator and bank accounts; production can require it."}</p>
        {!user?.mfa_enabled && !mfa && <button className="btn sm" onClick={setupMfa}><Icon name="key" size={14} /> {lang === "zh" ? "设置双重验证" : "Set up MFA"}</button>}
        {mfa && (
          <div className="stack" style={{ gap: 8 }}>
            <div className="alert info small">{lang === "zh" ? "在验证器应用中添加此密钥（或扫描 otpauth 链接），然后输入 6 位验证码确认。" : "Add this key to your authenticator app (or use the otpauth link), then confirm with the 6 digit code."}</div>
            <div className="mono" style={{ fontSize: 15, letterSpacing: ".08em", overflowWrap: "anywhere" }}>{mfa.secret}</div>
            <div className="small muted" style={{ overflowWrap: "anywhere" }}>{mfa.otpauth_uri}</div>
            <div className="row"><input className="input" style={{ maxWidth: 160 }} inputMode="numeric" placeholder="123456" value={code} onChange={(e) => setCode(e.target.value)} /><button className="btn primary sm" disabled={code.length < 6} onClick={enableMfa}>{lang === "zh" ? "启用" : "Enable"}</button><button className="btn sm" onClick={() => setMfa(null)}>{t("cancel")}</button></div>
          </div>
        )}
        {user?.mfa_enabled && (
          <div className="row" style={{ marginTop: 8 }}><input className="input" style={{ maxWidth: 160 }} inputMode="numeric" placeholder="123456" value={code} onChange={(e) => setCode(e.target.value)} /><button className="btn sm" disabled={code.length < 6} onClick={disableMfa}>{lang === "zh" ? "关闭双重验证" : "Disable MFA"}</button></div>
        )}
      </div>
    </div>
  );
}

interface ShareRow { id: string; record_type: string; record_id: string; record_label: string | null; permissions: string[]; with: { name: string; email: string; role: string } | null; created_at: string }
interface Shares { given: ShareRow[]; received: ShareRow[] }

/** Explicit, per record sharing with a household member, adviser or another customer. View only unless upload is added. */
export function SharesPanel({ records }: { records: { type: "policy" | "claim"; id: string; label: string }[] }) {
  const { lang } = useLang();
  const toast = useToast();
  const [shares, setShares] = useState<Shares>({ given: [], received: [] });
  const [form, setForm] = useState({ email: "", record: "", upload: false });
  const load = () => api.get<Shares>("/api/shares").then(setShares).catch(() => {});
  useEffect(() => { load(); }, []);
  const grant = async () => {
    const rec = records.find((r) => `${r.type}:${r.id}` === form.record);
    if (!rec) return;
    try {
      await api.post("/api/shares", { grantee_email: form.email.trim(), record_type: rec.type, record_id: rec.id, permissions: form.upload ? ["view", "upload"] : ["view"] });
      setForm({ email: "", record: "", upload: false });
      load();
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  const revoke = async (id: string) => {
    await api.del(`/api/shares/${id}`);
    load();
  };
  const zh = lang === "zh";
  return (
    <div className="stack" style={{ gap: 8 }}>
      <h3 style={{ margin: 0 }}>{zh ? "与他人共享" : "Sharing with others"}</h3>
      <p className="small muted" style={{ margin: 0 }}>{zh ? "逐条记录授权给家庭成员或顾问，默认仅可查看，可随时撤销。保险公司只在你提交理赔时才会收到资料。" : "Share one record at a time with a household member or adviser. View only by default, revocable at any time. Insurers only receive material when you submit a claim."}</p>
      {shares.given.length > 0 && (
        <table className="table">
          <thead><tr><th>{zh ? "记录" : "Record"}</th><th>{zh ? "共享对象" : "Shared with"}</th><th>{zh ? "权限" : "Permissions"}</th><th></th></tr></thead>
          <tbody>{shares.given.map((s) => <tr key={s.id}><td className="mono">{s.record_label || s.record_id.slice(0, 8)}</td><td>{s.with?.name}<div className="small muted">{s.with?.email}</div></td><td>{s.permissions.join(", ")}</td><td><button className="btn sm" onClick={() => revoke(s.id)}>{zh ? "撤销" : "Revoke"}</button></td></tr>)}</tbody>
        </table>
      )}
      {shares.received.length > 0 && <div className="small">{zh ? "他人与你共享：" : "Shared with you: "}{shares.received.map((s) => <Badge key={s.id} plain>{s.record_label || s.record_type} · {s.with?.name}</Badge>)}</div>}
      <div className="row" style={{ flexWrap: "wrap" }}>
        <select className="select" style={{ maxWidth: 240 }} value={form.record} onChange={(e) => setForm({ ...form, record: e.target.value })}><option value="">{zh ? "选择记录" : "Choose a record"}</option>{records.map((r) => <option key={`${r.type}:${r.id}`} value={`${r.type}:${r.id}`}>{r.label}</option>)}</select>
        <input className="input" style={{ maxWidth: 260 }} type="email" placeholder={zh ? "对方账户邮箱" : "Their account email"} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <label className="small row" style={{ gap: 6 }}><input type="checkbox" checked={form.upload} onChange={(e) => setForm({ ...form, upload: e.target.checked })} />{zh ? "允许上传文件" : "allow uploads"}</label>
        <button className="btn sm" disabled={!form.email || !form.record} onClick={grant}>{zh ? "共享" : "Share"}</button>
      </div>
    </div>
  );
}

interface Consent { purpose: string; label: Record<string, string>; granted: boolean; source: string; recorded_at: string | null; locked: boolean }

export function ConsentPanel() {
  const { t, lang, pick } = useLang();
  const toast = useToast();
  const [consents, setConsents] = useState<Consent[]>([]);
  const load = () => api.get<{ consents: Consent[] }>("/api/consents").then((r) => setConsents(r.consents)).catch(() => {});
  useEffect(() => { load(); }, [lang]);
  const toggle = async (c: Consent) => {
    try {
      const r = await api.post<{ consents: Consent[] }>("/api/consents", { purpose: c.purpose, granted: !c.granted });
      setConsents(r.consents);
    } catch (e) {
      toast(errorMessage(e));
    }
  };
  return (
    <div className="stack" style={{ gap: 8 }}>
      <h3 style={{ margin: 0 }}>{t("consents")}</h3>
      <p className="small muted" style={{ margin: 0 }}>{t("consent_note")}</p>
      {consents.map((c) => (
        <label key={c.purpose} className="row between" style={{ padding: "8px 0", borderBottom: "1px solid var(--border)", cursor: c.locked ? "default" : "pointer" }}>
          <span style={{ flex: 1, minWidth: 0 }}>{pick(c.label)}<div className="small muted">{c.locked ? (lang === "zh" ? "服务运行所必需" : "Required for the service") : c.source === "default" ? (lang === "zh" ? "默认设置" : "Default") : `${lang === "zh" ? "由你设置于" : "Set by you"} ${c.recorded_at ? fmtDateTime(c.recorded_at, lang) : ""}`}</div></span>
          <input type="checkbox" checked={c.granted} disabled={c.locked} onChange={() => toggle(c)} style={{ width: 20, height: 20 }} />
        </label>
      ))}
    </div>
  );
}
