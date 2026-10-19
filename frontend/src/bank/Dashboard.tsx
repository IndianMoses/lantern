import { BankShell } from "../components/Layout";
import { Spinner } from "../components/ui";

// Placeholder; replaced by the bank operations pages.
export default function BankDashboard() {
  return <BankShell title="Bank"><Spinner /></BankShell>;
}
export function BankIntegrationPage() {
  return <BankShell title="Integration"><Spinner /></BankShell>;
}
export function BankAuditPage() {
  return <BankShell title="Audit"><Spinner /></BankShell>;
}
