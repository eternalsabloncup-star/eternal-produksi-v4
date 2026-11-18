import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { TEAM_TASK_ROLES } from "./app/nav";
import { useAuth } from "./auth";
import { ControlPage } from "./pages/ControlPage";
import { InboxPage } from "./pages/InboxPage";
import { LoginPage } from "./pages/LoginPage";
import { BufferStockPage } from "./pages/BufferStockPage";
import { DeliveryPage } from "./pages/DeliveryPage";
import { PlaceholderModulePage } from "./pages/PlaceholderModulePage";
import { ProductionFlowPage } from "./pages/ProductionFlowPage";
import { TeamTaskPage } from "./pages/TeamTaskPage";
import { TeamTasksSummaryPage } from "./pages/TeamTasksSummaryPage";

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
        path="/login"
        element={
          <PublicOnly>
            <LoginPage />
          </PublicOnly>
        }
      />
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
        <Route
          path="dashboard"
          element={
            <PlaceholderModulePage
              title="Dashboard"
              subtitle="Ringkasan operasional — kerangka navigasi Task #004."
              nextHint="KPI agregat lintas cabang dan antrean perhatian berbasis API."
            />
          }
        />
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
          element={
            <PlaceholderModulePage
              title="Hasil Produksi & QC"
              subtitle="Hasil cetak dan QC — kerangka navigasi."
              nextHint="Pemisahan qty cetak vs qty lolos QC + gate siap kirim."
            />
          }
        />
        <Route path="delivery" element={<DeliveryPage />} />
        <Route
          path="reports"
          element={
            <PlaceholderModulePage
              title="Laporan"
              subtitle="Laporan operasional — kerangka saja."
              nextHint="Laporan berbasis data API, bukan fixture DEMO."
            />
          }
        />
        <Route
          path="settings"
          element={
            <PlaceholderModulePage
              title="Pengaturan"
              subtitle="Master data — pratinjau menu saja."
              nextHint="Cabang, user/role, mesin, produk, shift, integrasi."
            />
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/app/schedule" replace />} />
    </Routes>
  );
}
