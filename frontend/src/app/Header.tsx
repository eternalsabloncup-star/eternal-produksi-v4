import { useState } from "react";
import { useAuth } from "../auth";
import type { AppNavItem } from "./nav";

type Props = {
  current: AppNavItem;
};

export function Header({ current }: Props) {
  const { user, logout } = useAuth();
  const [busy, setBusy] = useState(false);
  const initials = (user?.display_name || user?.email || "EP")
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  async function onLogout() {
    setBusy(true);
    try {
      await logout();
    } finally {
      setBusy(false);
    }
  }

  return (
    <header className="ep-topbar">
      <span className="ep-crumb">Workspace /</span>
      <strong>{current.label}</strong>
      <div className="ep-topright">
        <span className="ep-demo-pill">● DEMO INTERAKTIF</span>
        <span className="ep-sub" title={user?.email}>
          {user?.display_name || user?.email}
        </span>
        <span className="ep-avatar" aria-hidden>
          {initials}
        </span>
        <button type="button" className="ep-btn sm" onClick={onLogout} disabled={busy}>
          {busy ? "Keluar…" : "Keluar"}
        </button>
      </div>
    </header>
  );
}
