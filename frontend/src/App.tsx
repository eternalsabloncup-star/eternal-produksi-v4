import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { TEAM_TASK_ROLES } from "./app/nav";
import { useAuth } from "./auth";
import { ControlPage } from "./pages/ControlPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DesignApprovalPage } from "./pages/DesignApprovalPage";
import { ReportsPage } from "./pages/ReportsPage";
import { InboxPage } from "./pages/InboxPage";
import { LoginPage } from "./pages/LoginPage";
import { BufferStockPage } from "./pages/BufferStockPage";
import { DeliveryPage } from "./pages/DeliveryPage";
import { ProductionFlowPage } from "./pages/ProductionFlowPage";
import { SettingsMasterPage } from "./pages/SettingsMasterPage";
import { TeamTaskPage } from "./pages/TeamTaskPage";
import { TeamTasksSummaryPage } from "./pages/TeamTasksSummaryPage";
import { LiveTrackingPage } from "./pages/tracking/LiveTrackingPage";
import { TrackingAdminPage } from "./pages/tracking/TrackingAdminPage";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="page center">
        <p className="muted">Memuat sesi…</p>
      </div>
    );
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function PublicOnly({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <div className="page center">
        <p className="muted">Memuat sesi…</p>
      </div>
    );
  }
  if (user) {
    return <Navigate to="/app/schedule" replace />;
  }
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/app/schedule" replace />} />
      <Route
        path="/t/:customerSlug/:poSlug/:token"
        element={<LiveTrackingPage />}
      />
      <Route
        path="/login"
        element={
          <PublicOnly>
            <LoginPage />
          </PublicOnly>
        }
      />
      {/* Halaman customer — tanpa sesi internal V4. */}
      <Route path="/approval/:token" element={<DesignApprovalPage />} />
      <Route
        path="/app"
        element={
          <ProtectedRoute>
            <AppShell />
          </ProtectedRoute>
        }
      >
        <Route index element={<Navigate to="schedule" replace />} />
        <Route path="schedule" element={<ControlPage />} />
        <Route path="control" element={<Navigate to="/app/schedule" replace />} />
        <Route path="inbox" element={<InboxPage />} />
        <Route path="tracking" element={<TrackingAdminPage />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="production-flow" element={<ProductionFlowPage />} />
        <Route path="tasks" element={<TeamTasksSummaryPage />} />
        {TEAM_TASK_ROLES.map((role) => (
          <Route
            key={role.id}
            path={`tasks/${role.id}`}
            element={<TeamTaskPage role={role} />}
          />
        ))}
        <Route path="buffer-stock" element={<BufferStockPage />} />
        <Route
          path="samples"
          element={<Navigate to="/app/buffer-stock" replace />}
        />
        <Route
          path="qc"
          element={<Navigate to="/app/tasks/helper" replace />}
        />
        <Route path="delivery" element={<DeliveryPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="settings" element={<SettingsMasterPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/app/schedule" replace />} />
    </Routes>
  );
}
