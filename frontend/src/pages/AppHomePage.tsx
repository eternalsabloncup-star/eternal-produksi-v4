import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

export function AppHomePage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onLogout() {
    setBusy(true);
    setError(null);
    try {
      await logout();
      navigate("/login", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal keluar");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page app-page">
      <header className="app-header">
        <div>
          <p className="brand-mark">Eternal Packaging</p>
          <h1>ETERNAL PRODUCTION V4</h1>
          <p className="lede">Sistem siap dikembangkan.</p>
        </div>
        <button type="button" className="ghost" onClick={onLogout} disabled={busy}>
          {busy ? "Keluar…" : "Keluar"}
        </button>
      </header>

      <section className="app-panel">
        <p className="muted">Pengguna aktif</p>
        <p className="user-name">{user?.display_name}</p>
        <p className="muted">{user?.email}</p>
        {error ? <p className="form-error">{error}</p> : null}
      </section>
    </div>
  );
}
