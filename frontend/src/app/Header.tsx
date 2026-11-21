import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { EternalLogo } from "../brand/EternalLogo";
import type { ShellDataMode } from "../demo/resolveInboxMode";
import { shellModeBadgeLabel } from "../demo/resolveInboxMode";
import { userDisplayName } from "../lib/userDisplayName";
import type { AppNavItem } from "./nav";

type Props = {
  current: AppNavItem;
  dataMode: ShellDataMode;
};

export function Header({ current, dataMode }: Props) {
  const { user, logout } = useAuth();
  const [busy, setBusy] = useState(false);
  const initials = (user?.display_name?.trim() || "EP")
    .split(/\s+/)
    .map((p) => p[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  const badge = shellModeBadgeLabel(dataMode);

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
      <div className="ep-topbar-left">
        <EternalLogo
          variant="horizontal"
          className="ep-topbar-logo"
          decorative
        />
        <strong className="ep-topbar-title">{current.label}</strong>
      </div>
      <div className="ep-topright">
        {badge ? (
          <span
            className={dataMode === "api" ? "ep-api-pill" : "ep-demo-pill"}
            data-testid="shell-mode-badge"
            data-mode={dataMode}
          >
            {badge}
          </span>
        ) : null}
        <Link
          className="ep-btn sm"
          to="/app/m/tasks"
          data-testid="shell-mobile-ops-link"
        >
          Mobile
        </Link>
        <span className="ep-sub">
          {userDisplayName(user?.display_name)}
          {user?.role ? ` · ${user.role}` : ""}
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
