import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Login } from "./components/Login";
import { ToastProvider } from "./components/Toast";
import { AppStateProvider, useAppState } from "./lib/app-state";
import { Dashboard } from "./pages/Dashboard";
import { Exchanges } from "./pages/Exchanges";
import { History } from "./pages/History";
import { Opportunities } from "./pages/Opportunities";
import { OpportunityDetail } from "./pages/OpportunityDetail";
import { Settings } from "./pages/Settings";
import { Trading } from "./pages/Trading";
import { Wallets } from "./pages/Wallets";

function Shell() {
  const { auth } = useAppState();
  if (!auth.checked) {
    return (
      <div className="login-wrap">
        <span className="spinner" aria-label="Loading" />
      </div>
    );
  }
  if (auth.required && !auth.authenticated) return <Login />;
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/opportunities" element={<Opportunities />} />
        <Route path="/opportunities/:id" element={<OpportunityDetail />} />
        <Route path="/trading" element={<Trading />} />
        <Route path="/wallets" element={<Wallets />} />
        <Route path="/exchanges" element={<Exchanges />} />
        <Route path="/history" element={<History />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AppStateProvider>
          <Shell />
        </AppStateProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
