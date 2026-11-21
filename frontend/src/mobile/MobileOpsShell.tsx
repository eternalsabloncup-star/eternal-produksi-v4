import { useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { formatOpenTaskBadge } from "../api/openTaskCounts";
import type { TeamTaskRoleId } from "../app/nav";
import { TEAM_TASK_ROLES } from "../app/nav";
import { useAuth } from "../auth";
import { userDisplayName } from "../lib/userDisplayName";
import { ApiSimulationBanner } from "../sim/ApiSimulationBanner";
import { useOpenTeamTaskCounts } from "../hooks/useOpenTeamTaskCounts";
import { MobileBottomNav } from "./MobileBottomNav";
import { MobileOpsProvider, useMobileOps } from "./MobileOpsContext";
import { mobileTabByPath, MOBILE_OPS_TABS } from "./mobileNav";
import { MobileDeliveryPage } from "./pages/MobileDeliveryPage";
import { MobileMonitoringPage } from "./pages/MobileMonitoringPage";
import { MobileMyTasksPage } from "./pages/MobileMyTasksPage";
import { MobileSchedulePage } from "./pages/MobileSchedulePage";
import { registerOpsServiceWorker } from "./registerOpsSw";
import "./mobile.css";

function MobileOpsChrome() {
  const location = useLocation();
  const { user, logout } = useAuth();
  const ops = useMobileOps();
  const tab = mobileTabByPath(location.pathname);
  const { counts, status } = useOpenTeamTaskCounts();

  const [visited, setVisited] = useState<Record<string, boolean>>({
    tasks: true,
  });

  useEffect(() => {
    setVisited((v) => (v[tab.id] ? v : { ...v, [tab.id]: true }));
  }, [tab.id]);

  useEffect(() => {
    void registerOpsServiceWorker();
  }, []);

  const badge = useMemo(() => {
    if (!ops) return null;
    if (status !== "ready" && status !== "truncated") return null;
    if (ops.role) {
      return formatOpenTaskBadge(counts[ops.role.id] ?? 0, status);
    }
    if (ops.canViewAllTasks) {
      if (ops.viewRoleFilter) {
        return formatOpenTaskBadge(counts[ops.viewRoleFilter] ?? 0, status);
      }
      const total = TEAM_TASK_ROLES.reduce(
        (sum, r) => sum + (counts[r.id] ?? 0),
        0,
      );
      return formatOpenTaskBadge(total, status);
    }
    return null;
  }, [ops, counts, status]);

  const showViewRoleFilter =
    Boolean(ops?.canViewAllTasks) && !ops?.role;

  return (
    <div className="ep-m-app" data-testid="mobile-ops-shell">
      <header className="ep-m-header">
        <div className="ep-m-header-brand">
          {/* Mobile: simbol. Tablet+: horizontal — CSS .ep-m-logo-*. */}
          <span className="ep-m-logo-wrap" aria-label="ETERNAL">
            <img
              className="ep-m-logo ep-m-logo-mark"
              src="/brand/eternal-logo-mark.png"
              srcSet="/brand/eternal-logo-mark.png 1x, /brand/eternal-logo-mark@2x.png 2x"
              alt=""
              draggable={false}
              decoding="async"
            />
            <img
              className="ep-m-logo ep-m-logo-horizontal"
              src="/brand/eternal-logo-horizontal.png"
              srcSet="/brand/eternal-logo-horizontal.png 1x, /brand/eternal-logo-horizontal@2x.png 2x"
              alt=""
              draggable={false}
              decoding="async"
            />
          </span>
          <strong className="ep-m-header-product">Eternal Packaging</strong>
          <span className="ep-m-header-tab">{tab.label}</span>
        </div>
        <div className="ep-m-header-meta">
          {ops?.role ? (
            <span className="ep-m-role" data-testid="mobile-ops-role">
              {ops.role.label}
            </span>
          ) : ops?.canViewAllTasks ? (
            <span className="ep-m-role" data-testid="mobile-ops-role-viewall">
              Role
            </span>
          ) : (
            <span className="ep-m-role is-locked" data-testid="mobile-ops-role-locked">
              Role operasional belum ditetapkan
            </span>
          )}
          {showViewRoleFilter ? (
            <label className="ep-m-rolefilter">
              <span className="sr-only">Filter role tugas</span>
              <select
                value={ops?.viewRoleFilter ?? ""}
                onChange={(e) => {
                  const v = e.target.value;
                  ops?.setViewRoleFilter(
                    v ? (v as TeamTaskRoleId) : null,
                  );
                }}
                aria-label="Filter role tugas"
                data-testid="mobile-ops-role-filter"
              >
                <option value="">Semua Role</option>
                {TEAM_TASK_ROLES.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <Link className="ep-m-linkdesk" to="/app/schedule">
            Desktop
          </Link>
          <button
            type="button"
            className="ep-m-logout"
            onClick={() => void logout()}
          >
            Keluar
          </button>
        </div>
        {user ? (
          <p className="ep-m-user">{userDisplayName(user.display_name)}</p>
        ) : null}
      </header>

      <main className="ep-m-main">
        <ApiSimulationBanner />
        {/* Keep-alive tab panels — filter/scroll state bertahan antar tab. */}
        {MOBILE_OPS_TABS.map((t) => {
          if (!visited[t.id] && t.id !== tab.id) return null;
          const hidden = t.id !== tab.id;
          return (
            <div
              key={t.id}
              className={`ep-m-panel${hidden ? " is-hidden" : ""}`}
              hidden={hidden}
              aria-hidden={hidden}
              data-tab={t.id}
            >
              {t.id === "tasks" ? <MobileMyTasksPage /> : null}
              {t.id === "schedule" ? <MobileSchedulePage /> : null}
              {t.id === "delivery" ? <MobileDeliveryPage /> : null}
              {t.id === "monitoring" ? <MobileMonitoringPage /> : null}
            </div>
          );
        })}
      </main>

      <MobileBottomNav openTaskBadge={badge} />
    </div>
  );
}

export function MobileOpsShell() {
  return (
    <MobileOpsProvider>
      <MobileOpsChrome />
    </MobileOpsProvider>
  );
}
