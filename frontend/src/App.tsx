import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./app/AppShell";
import { useAuth } from "./auth";
import { ControlPage } from "./pages/ControlPage";
import { InboxPage } from "./pages/InboxPage";
import { LoginPage } from "./pages/LoginPage";
import { PlaceholderModulePage } from "./pages/PlaceholderModulePage";

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
    return <Navigate to="/app/control" replace />;
  }
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/app/control" replace />} />
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
        <Route index element={<Navigate to="control" replace />} />
        <Route path="control" element={<ControlPage />} />
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
        <Route
          path="schedule"
          element={
            <PlaceholderModulePage
              title="Jadwal Produksi"
              subtitle="Penjadwalan mesin — belum diimplementasikan."
              nextHint="Timeline kapasitas, alokasi PO, validasi overbooking di backend."
            />
          }
        />
        <Route
          path="tasks"
          element={
            <PlaceholderModulePage
              title="Tugas Tim"
              subtitle="Penugasan operator — kerangka saja."
              nextHint="Tugas nyata dari status PO tanpa entri kosong/duplikat."
            />
          }
        />
        <Route
          path="samples"
          element={
            <PlaceholderModulePage
              title="Approval Sampel"
              subtitle="Persetujuan sampel — belum diimplementasikan."
              nextHint="Gate approval sampel sebelum produksi, rule produk wajib sampel."
            />
          }
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
        <Route
          path="delivery"
          element={
            <PlaceholderModulePage
              title="Jadwal Pengiriman"
              subtitle="Pengiriman — belum diimplementasikan."
              nextHint="Bukti loading, surat jalan, foto, dan konfirmasi penerima."
            />
          }
        />
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
      <Route path="*" element={<Navigate to="/app/control" replace />} />
    </Routes>
  );
}
