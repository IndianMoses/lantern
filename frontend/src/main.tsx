import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import "./styles.css";
import { AuthProvider, BANK_ROLES, INSURER_ROLES, useAuth } from "./lib/auth";
import { LangProvider, useLang } from "./lib/i18n";
import { ToastProvider, Spinner } from "./components/ui";
import { ScrollToTop } from "./components/Layout";
import Login from "./pages/Login";
import OverviewPage from "./pages/Overview";
import AssistantPage from "./pages/Assistant";
import PoliciesPage, { PolicyDetailPage } from "./pages/Policies";
import GapsPage from "./pages/Gaps";
import ClaimsPage, { NewClaimPage } from "./pages/Claims";
import ClaimDetailPage from "./pages/ClaimDetail";
import DocumentsPage from "./pages/Documents";
import MessagesPage from "./pages/Messages";
import SettingsPage from "./pages/Settings";
import InsurerDashboard from "./insurer/Dashboard";
import QueuePage from "./insurer/Queue";
import WorkspacePage from "./insurer/Workspace";
import CustomersPage, { CustomerDetailPage, TeamPage } from "./insurer/Customers";
import BankDashboard, { BankAuditPage, BankIntegrationPage } from "./bank/Dashboard";
import { BankAccountPage, InsurerAccountPage } from "./pages/Account";

type Surface = "customer" | "insurer" | "bank";

function homeFor(role: string): string {
  if (INSURER_ROLES.includes(role as never)) return "/insurer";
  if (BANK_ROLES.includes(role as never)) return "/bank";
  return "/";
}

/**
 * Route guard. The server enforces authorisation on every endpoint; this only
 * keeps users on the surface that matches their role and sends anonymous
 * visitors to the login page with the requested path preserved.
 */
function Guard({ surface, children }: { surface: Surface; children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const { setLang, lang } = useLang();
  const loc = useLocation();
  React.useEffect(() => {
    if (user && user.locale !== lang && !localStorage.getItem("lantern.lang")) setLang(user.locale);
  }, [user, lang, setLang]);
  if (!ready) return <div style={{ padding: 40 }}><Spinner /></div>;
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname + loc.search }} replace />;
  const mine: Surface = INSURER_ROLES.includes(user.role) ? "insurer" : BANK_ROLES.includes(user.role) ? "bank" : "customer";
  if (mine !== surface) return <Navigate to={homeFor(user.role)} replace />;
  return <>{children}</>;
}

function NotFound() {
  const { user, ready } = useAuth();
  if (!ready) return <div style={{ padding: 40 }}><Spinner /></div>;
  return <Navigate to={user ? homeFor(user.role) : "/login"} replace />;
}

function App() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<Guard surface="customer"><OverviewPage /></Guard>} />
        <Route path="/assistant" element={<Guard surface="customer"><AssistantPage /></Guard>} />
        <Route path="/policies" element={<Guard surface="customer"><PoliciesPage /></Guard>} />
        <Route path="/policies/:id" element={<Guard surface="customer"><PolicyDetailPage /></Guard>} />
        <Route path="/gaps" element={<Guard surface="customer"><GapsPage /></Guard>} />
        <Route path="/gaps/:analysisId" element={<Guard surface="customer"><GapsPage /></Guard>} />
        <Route path="/claims" element={<Guard surface="customer"><ClaimsPage /></Guard>} />
        <Route path="/claims/new" element={<Guard surface="customer"><NewClaimPage /></Guard>} />
        <Route path="/claims/:id" element={<Guard surface="customer"><ClaimDetailPage /></Guard>} />
        <Route path="/documents" element={<Guard surface="customer"><DocumentsPage /></Guard>} />
        <Route path="/messages" element={<Guard surface="customer"><MessagesPage /></Guard>} />
        <Route path="/settings" element={<Guard surface="customer"><SettingsPage /></Guard>} />
        <Route path="/insurer" element={<Guard surface="insurer"><InsurerDashboard /></Guard>} />
        <Route path="/insurer/claims" element={<Guard surface="insurer"><QueuePage /></Guard>} />
        <Route path="/insurer/claims/:id" element={<Guard surface="insurer"><WorkspacePage /></Guard>} />
        <Route path="/insurer/customers" element={<Guard surface="insurer"><CustomersPage /></Guard>} />
        <Route path="/insurer/customers/:id" element={<Guard surface="insurer"><CustomerDetailPage /></Guard>} />
        <Route path="/insurer/team" element={<Guard surface="insurer"><TeamPage /></Guard>} />
        <Route path="/insurer/account" element={<Guard surface="insurer"><InsurerAccountPage /></Guard>} />
        <Route path="/bank" element={<Guard surface="bank"><BankDashboard /></Guard>} />
        <Route path="/bank/integration" element={<Guard surface="bank"><BankIntegrationPage /></Guard>} />
        <Route path="/bank/audit" element={<Guard surface="bank"><BankAuditPage /></Guard>} />
        <Route path="/bank/account" element={<Guard surface="bank"><BankAccountPage /></Guard>} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </>
  );
}

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* offline shell is optional */
    });
  });
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <LangProvider>
      <AuthProvider>
        <ToastProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </ToastProvider>
      </AuthProvider>
    </LangProvider>
  </React.StrictMode>,
);
