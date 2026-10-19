import { BankShell, InsurerShell } from "../components/Layout";
import { SecurityPanel } from "../components/Security";
import { useAuth } from "../lib/auth";
import { useLang } from "../lib/i18n";

/** Account security for staff surfaces (sessions, password, MFA). Customers have the same panel under Settings. */
export function InsurerAccountPage() {
  const { lang } = useLang();
  const { user } = useAuth();
  return (
    <InsurerShell title={lang === "zh" ? "账户安全" : "Account security"} subtitle={`${user?.full_name || ""} · ${user?.email || ""}`}>
      <div className="card"><SecurityPanel /></div>
    </InsurerShell>
  );
}

export function BankAccountPage() {
  const { lang } = useLang();
  const { user } = useAuth();
  return (
    <BankShell title={lang === "zh" ? "账户安全" : "Account security"} subtitle={`${user?.full_name || ""} · ${user?.email || ""}`}>
      <div className="card"><SecurityPanel /></div>
    </BankShell>
  );
}
