import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useLang, type Lang } from "../lib/i18n";
import { Icon, type IconName } from "./Icons";
import { LanternMark } from "./Logo";

export interface NavItem { to: string; label: string; icon: IconName; count?: number; end?: boolean }

export function LangToggle() {
  const { lang, setLang, t } = useLang();
  const { user, setLocale } = useAuth();
  const change = async (l: Lang) => {
    setLang(l);
    if (user) {
      try {
        await setLocale(l);
      } catch {
        /* the interface language is already updated; the server copy is best effort */
      }
    }
  };
  return (
    <div className="row lang-toggle" style={{ gap: 2, border: "1px solid var(--border)", borderRadius: 8, padding: 2, background: "var(--surface)" }} role="group" aria-label={t("language_label")}>
      <button className={`btn sm ${lang === "en" ? "primary" : "ghost"}`} style={{ padding: "3px 9px", minHeight: 28 }} onClick={() => change("en")} aria-pressed={lang === "en"}>EN</button>
      <button className={`btn sm ${lang === "zh" ? "primary" : "ghost"}`} style={{ padding: "3px 9px", minHeight: 28 }} onClick={() => change("zh")} aria-pressed={lang === "zh"}>中文</button>
    </div>
  );
}

interface NotificationRow { id: string; title: string; body: string; link: string | null; read_at: string | null; created_at: string }

export function NotificationsBell() {
  const { t } = useLang();
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationRow[]>([]);
  const nav = useNavigate();
  const wrap = useRef<HTMLDivElement>(null);
  const load = async () => {
    try {
      const r = await api.get<{ notifications: NotificationRow[]; unread: number }>("/api/notifications");
      setItems(r.notifications);
      setUnread(r.unread);
    } catch {
      /* not signed in or offline */
    }
  };
  useEffect(() => {
    load();
    const id = window.setInterval(load, 30000);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false); };
    const k = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", h);
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("mousedown", h); document.removeEventListener("keydown", k); };
  }, [open]);
  return (
    <div style={{ position: "relative" }} ref={wrap} className="bell">
      <button className="btn sm icon" onClick={() => setOpen((o) => !o)} aria-label={t("notifications")} aria-expanded={open} aria-haspopup="dialog">
        <Icon name="bell" />
        {unread > 0 && <span className="badge accent plain" style={{ padding: "0 6px", lineHeight: 1.5 }}>{unread}</span>}
      </button>
      {open && (
        <div className="card notif-pop" role="dialog" aria-label={t("notifications")}>
          <div className="row between" style={{ marginBottom: 6 }}>
            <strong>{t("notifications")}</strong>
            <button className="btn ghost sm" onClick={async () => { await api.post("/api/notifications/read-all"); load(); }}>{t("mark_all_read")}</button>
          </div>
          {items.length === 0 && <div className="small muted">{t("nothing_yet")}</div>}
          {items.slice(0, 20).map((n) => (
            <button key={n.id} className="link-row" style={{ width: "100%", textAlign: "left", opacity: n.read_at ? 0.65 : 1, marginTop: 6, cursor: n.link ? "pointer" : "default" }} onClick={async () => { await api.post(`/api/notifications/${n.id}/read`); setOpen(false); if (n.link) nav(n.link); load(); }}>
              <div style={{ fontWeight: 600, fontSize: 13 }}>{n.title}</div>
              <div className="small muted">{n.body}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** Scrolls to the top whenever the route changes (SPA navigations keep scroll otherwise). */
export function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "auto" });
  }, [pathname]);
  return null;
}

export function Shell({ children, title, subtitle, actions, nav, surfaceLabel, homePath, mobilePrimary, insurer }: { children: ReactNode; title?: string; subtitle?: string; actions?: ReactNode; nav: NavItem[]; surfaceLabel: string; homePath: string; mobilePrimary: NavItem[]; insurer?: boolean }) {
  const { user, logout } = useAuth();
  const { t, lang } = useLang();
  const navigate = useNavigate();
  const [sheet, setSheet] = useState(false);
  const loc = useLocation();
  useEffect(() => { setSheet(false); }, [loc.pathname]);
  useEffect(() => {
    document.title = title ? `${title} · Lantern` : "Lantern";
  }, [title]);
  const secondary = nav.filter((n) => !mobilePrimary.some((m) => m.to === n.to));
  const orgName = user?.org ? (lang === "zh" && user.org.name_zh ? user.org.name_zh : user.org.name) : user?.email;
  const doLogout = async () => {
    await logout();
    navigate("/login", { replace: true });
  };
  return (
    <div className={`shell ${insurer ? "insurer" : ""}`}>
      <aside className="sidebar">
        <Link className="brand" to={homePath} aria-label="Lantern">
          <LanternMark size={26} />
          <span className="brand-text">
            <b>Lantern</b>
            <span className="brand-sub">{surfaceLabel}</span>
          </span>
        </Link>
        <nav className="nav" aria-label={t("menu")}>
          {nav.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end ?? (n.to === homePath)} className={({ isActive }) => (isActive ? "active" : "")}>
              <Icon name={n.icon} />
              <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>{n.label}</span>
              {n.count ? <span className="count">{n.count}</span> : null}
            </NavLink>
          ))}
        </nav>
        <div className="foot">
          <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>{user?.full_name}</div>
          <div style={{ overflowWrap: "anywhere" }}>{orgName}</div>
          <button className="btn ghost sm" style={{ padding: "4px 0", marginTop: 4 }} onClick={doLogout}><Icon name="logout" size={14} /> {t("sign_out")}</button>
          <div className="demo-note" style={{ marginTop: 8 }}>{t("demo_note")}</div>
        </div>
      </aside>
      <main className="main">
        <div className="mobile-bar">
          <Link className="brand" to={homePath} aria-label="Lantern"><LanternMark size={22} /><span className="brand-text"><b style={{ fontSize: 15 }}>Lantern</b></span></Link>
          <div className="row" style={{ gap: 6 }}>
            <NotificationsBell />
            <LangToggle />
          </div>
        </div>
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
      <nav className="bottom-nav" aria-label={t("menu")}>
        {mobilePrimary.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end ?? (n.to === homePath)} className={({ isActive }) => (isActive ? "active" : "")}>
            <Icon name={n.icon} />
            <span>{n.label}</span>
          </NavLink>
        ))}
        <button onClick={() => setSheet(true)} aria-haspopup="dialog" aria-expanded={sheet}><Icon name="more" /><span>{t("more")}</span></button>
      </nav>
      {sheet && (
        <>
          <div className="sheet-backdrop" onClick={() => setSheet(false)} />
          <div className="sheet" role="dialog" aria-label={t("menu")}>
            <div className="row between" style={{ marginBottom: 6 }}>
              <strong>{user?.full_name}</strong>
              <button className="btn sm icon" onClick={() => setSheet(false)} aria-label={t("close")}><Icon name="x" /></button>
            </div>
            {secondary.map((n) => (
              <Link key={n.to} to={n.to}><Icon name={n.icon} /> {n.label}{n.count ? <span className="count badge accent plain" style={{ marginLeft: "auto" }}>{n.count}</span> : null}</Link>
            ))}
            <button className="sheet-row" style={{ width: "100%", background: "none", border: 0, textAlign: "left", cursor: "pointer" }} onClick={doLogout}><Icon name="logout" /> {t("sign_out")}</button>
            <div className="demo-note" style={{ marginTop: 10 }}>{t("demo_note")}</div>
          </div>
        </>
      )}
    </div>
  );
}

export function CustomerShell(props: { children: ReactNode; title?: string; subtitle?: string; actions?: ReactNode }) {
  const { t } = useLang();
  const nav: NavItem[] = [
    { to: "/", label: t("overview"), icon: "home" },
    { to: "/assistant", label: t("assistant"), icon: "chat" },
    { to: "/policies", label: t("my_insurance"), icon: "shield" },
    { to: "/gaps", label: t("gaps"), icon: "layers" },
    { to: "/claims", label: t("claims"), icon: "claims" },
    { to: "/documents", label: t("documents"), icon: "folder" },
    { to: "/messages", label: t("messages"), icon: "inbox" },
    { to: "/settings", label: t("settings"), icon: "settings" },
  ];
  const mobilePrimary: NavItem[] = [
    { to: "/", label: t("home"), icon: "home" },
    { to: "/assistant", label: t("assistant"), icon: "chat" },
    { to: "/policies", label: t("insurance"), icon: "shield" },
    { to: "/claims", label: t("claims"), icon: "claims" },
  ];
  return <Shell {...props} nav={nav} mobilePrimary={mobilePrimary} surfaceLabel={t("customer_surface")} homePath="/" />;
}

export function InsurerShell(props: { children: ReactNode; title?: string; subtitle?: string; actions?: ReactNode }) {
  const { t } = useLang();
  const nav: NavItem[] = [
    { to: "/insurer", label: t("operations"), icon: "grid" },
    { to: "/insurer/claims", label: t("queue"), icon: "list" },
    { to: "/insurer/customers", label: t("customers"), icon: "users" },
    { to: "/insurer/team", label: t("team"), icon: "lock" },
  ];
  const mobilePrimary: NavItem[] = nav.slice(0, 4);
  return <Shell {...props} nav={nav} mobilePrimary={mobilePrimary} surfaceLabel={t("insurer_surface")} homePath="/insurer" insurer />;
}

export function BankShell(props: { children: ReactNode; title?: string; subtitle?: string; actions?: ReactNode }) {
  const { t } = useLang();
  const nav: NavItem[] = [
    { to: "/bank", label: t("operations"), icon: "bank" },
    { to: "/bank/integration", label: t("integration"), icon: "graph" },
    { to: "/bank/audit", label: t("audit"), icon: "lock" },
  ];
  return <Shell {...props} nav={nav} mobilePrimary={nav} surfaceLabel={t("bank_surface")} homePath="/bank" />;
}
