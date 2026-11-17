import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      navigate("/app", { replace: true });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Email atau kata sandi salah";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="page login-page">
      <div className="login-shell">
        <header className="login-brand">
          <p className="brand-mark">Eternal Packaging</p>
          <h1>Eternal Production V4</h1>
          <p className="lede">Masuk untuk melanjutkan ke sistem produksi.</p>
        </header>

        <form className="login-form" onSubmit={onSubmit} noValidate>
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              name="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={submitting}
            />
          </label>

          <label className="field">
            <span>Kata sandi</span>
            <div className="password-row">
              <input
                type={showPassword ? "text" : "password"}
                name="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                disabled={submitting}
              />
              <button
                type="button"
                className="ghost"
                onClick={() => setShowPassword((v) => !v)}
                disabled={submitting}
              >
                {showPassword ? "Sembunyikan" : "Tampilkan"}
              </button>
            </div>
          </label>

          {error ? <p className="form-error">{error}</p> : null}

          <button type="submit" className="primary" disabled={submitting}>
            {submitting ? "Memproses…" : "Masuk"}
          </button>
        </form>
      </div>
    </div>
  );
}
