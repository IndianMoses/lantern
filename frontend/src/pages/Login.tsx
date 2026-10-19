import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, errorMessage } from "../lib/api";
import { useAuth, type LoginResult } from "../lib/auth";
import { useLang } from "../lib/i18n";
import { Icon } from "../components/Icons";
import { LangToggle } from "../components/Layout";
import { LanternMark } from "../components/Logo";

interface DemoAccount { email: string; password: string; name: string; role: string; org?: string; locale: string }

export default function Login() {
  const { login, register, verifyMfa } = useAuth();
  const { lang, setLang, t } = useLang();
  const nav = useNavigate();
  const location = useLocation();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("GB");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState<DemoAccount[]>([]);
  const [challenge, setChallenge] = useState<{ token: string; email: string } | null>(null);
  const [code, setCode] = useState("");

  useEffect(() => {
    api.get<{ accounts: DemoAccount[] }>("/api/dev/demo-accounts").then((r) => setDemo(r.accounts)).catch(() => setDemo([]));
  }, []);

  const go = (user: { role: string; locale: "en" | "zh" }) => {
    setLang(user.locale);
    const from = (location.state as { from?: string } | null)?.from;
    const home = user.role === "customer" ? "/" : user.role === "bank_admin" ? "/bank" : "/insurer";
    nav(from && from !== "/login" ? from : home, { replace: true });
  };
  // One place handles both outcomes of a password login: a session, or a
  // short lived MFA challenge that must be answered with an authenticator code.
  const afterLogin = (r: LoginResult, who: string) => {
    if (r.mfa_required) {
      setChallenge({ token: r.challenge_token, email: who });
      setCode("");
      return;
    }
    go(r.user);
  };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      if (mode === "login") afterLogin(await login(email, password), email);
      else go(await register({ email, password, full_name: name, locale: lang, country }));
    } catch (ex) {
      setErr(errorMessage(ex));
    } finally {
      setBusy(false);
    }
  };
  const quick = async (a: DemoAccount) => {
    setBusy(true);
    setErr(null);
    try {
      afterLogin(await login(a.email, a.password), a.email);
    } catch (ex) {
      setErr(errorMessage(ex));
    } finally {
      setBusy(false);
    }
  };
  const submitCode = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!challenge) return;
    setBusy(true);
    setErr(null);
    try {
      go(await verifyMfa(challenge.token, code.trim()));
    } catch (ex) {
      setErr(errorMessage(ex));
    } finally {
      setBusy(false);
    }
  };
  const customers = demo.filter((d) => d.role === "customer");
  const staff = demo.filter((d) => d.role !== "customer");
  return (
    <div className="login">
      <section className="hero">
        <div className="brand" style={{ padding: 0 }}>
          <LanternMark size={34} tone="dark" />
          <div className="brand-text"><b style={{ color: "#FFFFFF", fontSize: 20 }}>Lantern</b><span className="brand-sub" style={{ color: "var(--brand-on-dark)" }}>ClaimMate</span></div>
        </div>
        <h1>{lang === "zh" ? "逐条读懂你的保险。" : "Insurance, read by the clause."}</h1>
        <p>{lang === "zh" ? "一个会引用条款原文的保险助手：回答保单问题、找出保障缺口、把理赔材料准备好。任何发送都需要你亲自批准。" : "An assistant that answers from your own policy wording with clause citations, finds coverage gaps and overlaps, and prepares claims end to end. Anything that leaves the platform needs your explicit approval."}</p>
        <div className="points">
          <span><Icon name="check" size={14} /> {lang === "zh" ? "条款级引用" : "Clause level citations"}</span>
          <span><Icon name="check" size={14} /> {lang === "zh" ? "中英双语" : "English & 中文"}</span>
          <span><Icon name="check" size={14} /> {lang === "zh" ? "人工批准" : "Human approval tiers"}</span>
          <span><Icon name="check" size={14} /> {lang === "zh" ? "可安装到手机" : "Installs on your phone"}</span>
        </div>
      </section>
      <section className="panel">
        {challenge ? (
          <form onSubmit={submitCode}>
            <div className="row between" style={{ marginBottom: 16 }}>
              <h2>{t("mfa_code")}</h2>
              <LangToggle />
            </div>
            <div className="field-group">
              <div className="small muted">{challenge.email}</div>
              <p className="small" style={{ margin: 0 }}>{t("mfa_prompt")}</p>
              <div><label className="field">{t("mfa_code")}</label><input className="input" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} autoFocus required /></div>
              {err && <div className="alert bad" role="alert">{err}</div>}
              <button className="btn primary" disabled={busy || code.trim().length < 6} type="submit">{t("verify")}</button>
              <button type="button" className="btn ghost" onClick={() => { setChallenge(null); setErr(null); }}>{t("cancel")}</button>
            </div>
          </form>
        ) : (
        <form onSubmit={submit}>
          <div className="row between" style={{ marginBottom: 16 }}>
            <h2>{mode === "login" ? (lang === "zh" ? "登录" : "Sign in") : lang === "zh" ? "创建账户" : "Create an account"}</h2>
            <LangToggle />
          </div>
          <div className="field-group">
            {mode === "register" && (
              <div><label className="field">{lang === "zh" ? "姓名" : "Full name"}</label><input className="input" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} /></div>
            )}
            <div><label className="field">Email</label><input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="username" /></div>
            <div><label className="field">{lang === "zh" ? "密码" : "Password"}</label><input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={mode === "register" ? 10 : 8} autoComplete={mode === "login" ? "current-password" : "new-password"} /></div>
            {mode === "register" && (
              <div><label className="field">{lang === "zh" ? "国家/地区" : "Country"}</label>
                <select className="select" value={country} onChange={(e) => setCountry(e.target.value)}><option value="GB">United Kingdom</option><option value="CN">中国大陆</option><option value="HK">Hong Kong</option><option value="SG">Singapore</option></select></div>
            )}
            {err && <div className="alert bad" role="alert">{err}</div>}
            <button className="btn primary" disabled={busy} type="submit">{mode === "login" ? (lang === "zh" ? "登录" : "Sign in") : lang === "zh" ? "注册" : "Register"}</button>
            <button type="button" className="btn ghost" onClick={() => setMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? (lang === "zh" ? "没有账户？创建一个" : "No account? Create one") : lang === "zh" ? "已有账户？登录" : "Have an account? Sign in"}
            </button>
          </div>
          {demo.length > 0 && (
            <div className="demo-accounts">
              <div className="muted">{lang === "zh" ? "演示账户（仅开发环境显示）" : "Demo accounts (shown in development only)"}</div>
              <div className="small muted" style={{ marginTop: 6 }}>{lang === "zh" ? "客户" : "Customers"}</div>
              {customers.slice(0, 3).map((a) => (
                <button type="button" key={a.email} onClick={() => quick(a)}><b>{a.name}</b> · {a.email} <span className="muted">({a.locale})</span></button>
              ))}
              <div className="small muted" style={{ marginTop: 6 }}>{lang === "zh" ? "保险公司员工" : "Insurer staff"}</div>
              {staff.filter((a) => ["jordan.reyes@northgate.example", "wang.lei@pengcheng.example", "morgan.clarke@northgate.example", "nia.williams@pawfield.example"].includes(a.email)).map((a) => (
                <button type="button" key={a.email} onClick={() => quick(a)}><b>{a.name}</b> · {a.org} · {a.role.replace("insurer_", "")}</button>
              ))}
              {staff.some((a) => a.role === "bank_admin") && <div className="small muted" style={{ marginTop: 6 }}>{lang === "zh" ? "银行运营" : "Bank operations"}</div>}
              {staff.filter((a) => a.role === "bank_admin").map((a) => (
                <button type="button" key={a.email} onClick={() => quick(a)}><b>{a.name}</b> · {a.org}</button>
              ))}
            </div>
          )}
        </form>
        )}
      </section>
    </div>
  );
}
