import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { STATUS_TONE, humanize } from "../lib/format";
import { Icon } from "./Icons";

export function Badge({ tone, children, plain }: { tone?: string; children: ReactNode; plain?: boolean }) {
  return <span className={`badge ${tone || ""} ${plain ? "plain" : ""}`}>{children}</span>;
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  return <Badge tone={STATUS_TONE[status] || ""}>{label || humanize(status)}</Badge>;
}

export function Spinner() {
  return <span className="spinner" aria-label="loading" />;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Meter({ value, ok }: { value: number; ok?: boolean }) {
  return (
    <div className={`meter ${ok ? "ok" : ""}`} role="progressbar" aria-valuenow={value} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
    </div>
  );
}

export function Modal({ title, children, onClose, wide }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={wide ? { maxWidth: 960 } : undefined} role="dialog" aria-modal="true" aria-label={title}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h2>{title}</h2>
          <button className="btn sm icon" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Drawer({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <>
      <div className="modal-backdrop" style={{ background: "rgba(27,31,36,.2)" }} onMouseDown={(e) => e.target === e.currentTarget && onClose()} />
      <aside className="drawer" role="dialog" aria-label={title}>
        <div className="row between" style={{ marginBottom: 12 }}>
          <h3>{title}</h3>
          <button className="btn sm icon" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        {children}
      </aside>
    </>
  );
}

// ---- toasts
const ToastCtx = createContext<(msg: string) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<string | null>(null);
  const show = useCallback((m: string) => {
    setMsg(m);
    window.setTimeout(() => setMsg(null), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {msg && <div className="toast" role="status">{msg}</div>}
    </ToastCtx.Provider>
  );
}
export function useToast() {
  return useContext(ToastCtx);
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div>
      <label className="field">{label}</label>
      {children}
      {hint && <div className="small muted" style={{ marginTop: 4 }}>{hint}</div>}
    </div>
  );
}
