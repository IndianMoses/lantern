import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useLang, type Lang } from "../lib/i18n";

export function LangToggle() {
  const { lang, setLang } = useLang();
  const { user, setLocale } = useAuth();
  const change = async (l: Lang) => {
    setLang(l);
    if (user) {
      try {
        await setLocale(l);
      } catch {
        /* ignore */
      }
    }
  };
  return (
    <div className="row" style={{ gap: 2, border: "1px solid var(--line)", borderRadius: 8, padding: 2, background: "var(--bg-elev)" }} role="group" aria-label="Language">
      <button className={`btn sm ${lang === "en" ? "primary" : "ghost"}`} style={{ padding: "3px 9px" }} onClick={() => change("en")}>EN</button>
      <button className={`btn sm ${lang === "zh" ? "primary" : "ghost"}`} style={{ padding: "3px 9px" }} onClick={() => change("zh")}>中文</button>
    </div>
  );
}

export function NotificationsBell() {
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<{ id: string; title: string; body: string; link: string | null; read_at: string | null; created_at: string }[]>([]);
  const nav = useNavigate();
  const load = async () => {
    try {
      const r = await api.get<{ notifications: typeof items; unread: number }>("/api/notifications");
      setItems(r.notifications);
      setUnread(r.unread);
    } catch {
      /* ignore */
    }
  };
  useEffect(() => {
    load();
    const id = window.setInterval(load, 30000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <div style={{ position: "relative" }}>
      <button className="btn sm" onClick={() => setOpen((o) => !o)} aria-label="Notifications">🔔 {unread > 0 && <span className="badge accent plain">{unread}</span>}</button>
      {open && (
        <div className="card" style={{ position: "absolute", right: 0, top: 36, width: 360, zIndex: 40, maxHeight: 420, overflow: "auto", padding: 10 }}>
          <div className="row between" style={{ marginBottom: 6 }}>
            <strong>Notifications</strong>
            <button className="btn ghost sm" onClick={async () => { await api.post("/api/notifications/read-all"); load(); }}>Mark all read</button>
          </div>
          {items.length === 0 && <div className="small muted">Nothing yet.</div>}
          {items.slice(0, 20).map((n) => (
            <div key={n.id} style={{ padding: "8px 6px", borderTop: "1px solid var(--line)", opacity: n.read_at ? 0.6 : 1, cursor: n.link ? "pointer" : "default" }} onClick={async () => { await api.post(`/api/notifications/${n.id}/read`); setOpen(false); if (n.link) nav(n.link); load(); }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{n.title}</div>
              <div className="small muted">{n.body}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Shell({ children, title, subtitle, actions, insurer, nav }: { children: ReactNode; title?: string; subtitle?: string; actions?: ReactNode; insurer?: boolean; nav: { to: string; label: string; count?: number }[] }) {
  const { user, logout } = useAuth();
  const { t, lang } = useLang();
  const navigate = useNavigate();
  return (
    <div className={`shell ${insurer ? "insurer" : ""}`}>
      <aside className="sidebar">
        <div className="brand">
          <div className="mark" />
          <div>
            <b>Lantern</b>
            <span>{insurer ? (lang === "zh" ? "保险公司工作台" : "Insurer workspace") : lang === "zh" ? "保险助手" : "Insurance assistant"}</span>
          </div>
        </div>
        <nav className="nav">
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/" || n.to === "/insurer"} className={({ isActive }) => (isActive ? "active" : "")}>
              {n.label}
              {n.count ? <span className="count">{n.count}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="foot">
          <div style={{ fontWeight: 600, color: "var(--ink-2)" }}>{user?.full_name}</div>
          <div>{user?.org ? (lang === "zh" && user.org.name_zh ? user.org.name_zh : user.org.name) : user?.email}</div>
          <button className="btn ghost sm" style={{ padding: "4px 0" }} onClick={async () => { await logout(); navigate("/login"); }}>{t("sign_out")}</button>
          <div className="demo-note" style={{ marginTop: 8 }}>{t("demo_note")}</div>
        </div>
      </aside>
      <main className="main">
        <div className="topbar">
          <div className="title">
            {title && <h1>{title}</h1>}
            {subtitle && <p>{subtitle}</p>}
          </div>
          <div className="tools">
            {actions}
            <NotificationsBell />
            <LangToggle />
          </div>
        </div>
        {children}
      </main>
    </div>
  );
}

export function CustomerShell(props: { children: ReactNode; title?: string; subtitle?: string; actions?: ReactNode }) {
  const { t } = useLang();
  const nav = [
    { to: "/", label: t("overview") },
    { to: "/assistant", label: t("assistant") },
    { to: "/policies", label: t("my_insurance") },
    { to: "/gaps", label: t("gaps") },
    { to: "/claims", label: t("claims") },
    { to: "/documents", label: t("documents") },
    { to: "/messages", label: t("messages") },
    { to: "/settings", label: t("settings") },
  ];
  return <Shell {...props} nav={nav} />;
}

export function InsurerShell(props: { children: ReactNode; title?: string; subtitle?: string; actions?: ReactNode }) {
  const { t } = useLang();
  const nav = [
    { to: "/insurer", label: t("operations") },
    { to: "/insurer/claims", label: t("queue") },
    { to: "/insurer/customers", label: t("customers") },
    { to: "/insurer/team", label: t("team") },
  ];
  return <Shell {...props} nav={nav} insurer />;
}
