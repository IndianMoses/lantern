import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import "./styles.css";
import { AuthProvider, useAuth } from "./lib/auth";
import { LangProvider, useLang } from "./lib/i18n";
import { ToastProvider, Spinner } from "./components/ui";
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

function Guard({ role, children }: { role: "customer" | "insurer"; children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const { setLang, lang } = useLang();
  const loc = useLocation();
  React.useEffect(() => {
    if (user && user.locale !== lang && !localStorage.getItem("lantern.lang")) setLang(user.locale);
  }, [user, lang, setLang]);
  if (!ready) return <div style={{ padding: 40 }}><Spinner /></div>;
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  const isInsurer = user.role === "insurer_handler" || user.role === "insurer_admin";
  if (role === "insurer" && !isInsurer) return <Navigate to="/" replace />;
  if (role === "customer" && isInsurer) return <Navigate to="/insurer" replace />;
  return <>{children}</>;
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Guard role="customer"><OverviewPage /></Guard>} />
      <Route path="/assistant" element={<Guard role="customer"><AssistantPage /></Guard>} />
      <Route path="/policies" element={<Guard role="customer"><PoliciesPage /></Guard>} />
      <Route path="/policies/:id" element={<Guard role="customer"><PolicyDetailPage /></Guard>} />
      <Route path="/gaps" element={<Guard role="customer"><GapsPage /></Guard>} />
      <Route path="/claims" element={<Guard role="customer"><ClaimsPage /></Guard>} />
      <Route path="/claims/new" element={<Guard role="customer"><NewClaimPage /></Guard>} />
      <Route path="/claims/:id" element={<Guard role="customer"><ClaimDetailPage /></Guard>} />
      <Route path="/documents" element={<Guard role="customer"><DocumentsPage /></Guard>} />
      <Route path="/messages" element={<Guard role="customer"><MessagesPage /></Guard>} />
      <Route path="/settings" element={<Guard role="customer"><SettingsPage /></Guard>} />
      <Route path="/insurer" element={<Guard role="insurer"><InsurerDashboard /></Guard>} />
      <Route path="/insurer/claims" element={<Guard role="insurer"><QueuePage /></Guard>} />
      <Route path="/insurer/claims/:id" element={<Guard role="insurer"><WorkspacePage /></Guard>} />
      <Route path="/insurer/customers" element={<Guard role="insurer"><CustomersPage /></Guard>} />
      <Route path="/insurer/customers/:id" element={<Guard role="insurer"><CustomerDetailPage /></Guard>} />
      <Route path="/insurer/team" element={<Guard role="insurer"><TeamPage /></Guard>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
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
