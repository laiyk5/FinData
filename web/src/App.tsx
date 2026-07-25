import { useEffect, type ReactElement } from "react";
import { HashRouter, Navigate, Route, Routes, useNavigate } from "react-router";
import { UNAUTHORIZED_EVENT, getToken } from "./api";
import Layout from "./components/Layout";
import ConfigPage from "./pages/Config";
import CronPage from "./pages/Cron";
import DashboardPage from "./pages/Dashboard";
import DatasetDetailPage from "./pages/DatasetDetail";
import DatasetsPage from "./pages/Datasets";
import EventsPage from "./pages/Events";
import LoginPage from "./pages/Login";
import ProviderDetailPage from "./pages/ProviderDetail";
import ProvidersPage from "./pages/Providers";
import ServerPage from "./pages/Server";
import TaskDetailPage from "./pages/TaskDetail";
import TasksPage from "./pages/Tasks";

function RequireAuth({ children }: { children: ReactElement }) {
  if (!getToken()) return <Navigate to="/login" replace />;
  return children;
}

/** Redirects to the login page whenever any API call reports a 401. */
function UnauthorizedRedirect() {
  const navigate = useNavigate();
  useEffect(() => {
    const handler = (): void => {
      void navigate("/login", { replace: true });
    };
    window.addEventListener(UNAUTHORIZED_EVENT, handler);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handler);
  }, [navigate]);
  return null;
}

export default function App() {
  return (
    <HashRouter>
      <UnauthorizedRedirect />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="datasets" element={<DatasetsPage />} />
          <Route path="datasets/:name" element={<DatasetDetailPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="tasks/:id" element={<TaskDetailPage />} />
          <Route path="providers" element={<ProvidersPage />} />
          <Route path="providers/:name" element={<ProviderDetailPage />} />
          <Route path="server" element={<ServerPage />} />
          <Route path="cron" element={<CronPage />} />
          <Route path="events" element={<EventsPage />} />
          <Route path="config" element={<ConfigPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  );
}
