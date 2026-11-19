import { useState } from "react";
import { NavIcon } from "../app/icons";
import {
  TEAM_TASK_ROLES,
  type TeamTaskRoleId,
} from "../app/nav";
import { formatOpenTaskBadge } from "../api/openTaskCounts";
import { DesignerTaskPanel } from "../components/DesignerTaskPanel";
import { DriverTaskPanel } from "../components/DriverTaskPanel";
import { HelperTaskPanel } from "../components/HelperTaskPanel";
import { OperatorTaskPanel } from "../components/OperatorTaskPanel";
import { ProductionHeadAllocTable } from "../components/ProductionHeadAllocTable";
import { ScreenerTaskPanel } from "../components/ScreenerTaskPanel";
import { TeamTaskApiPanel } from "../components/TeamTaskApiPanel";
import { useOpenTeamTaskCounts } from "../hooks/useOpenTeamTaskCounts";
import { useProductionApiPanels } from "../sim/previewMode";

function RolePanel({ roleId }: { roleId: TeamTaskRoleId }) {
  switch (roleId) {
    case "production-head":
      return <ProductionHeadAllocTable />;
    case "designer":
      return <DesignerTaskPanel />;
    case "screener":
      return <ScreenerTaskPanel />;
    case "helper":
      return <HelperTaskPanel />;
    case "operator":
      return <OperatorTaskPanel />;
    case "driver":
      return <DriverTaskPanel />;
    default:
      return null;
  }
}

/** Ringkasan: deretan chip peran + satu panel aktif; judul hanya di topbar. */
export function TeamTasksSummaryPage() {
  const apiMode = useProductionApiPanels();
  const [activeRoleId, setActiveRoleId] =
    useState<TeamTaskRoleId>("production-head");
  const {
    counts,
    status: countsStatus,
    error: countsError,
  } = useOpenTeamTaskCounts();

  const activeRole =
    TEAM_TASK_ROLES.find((r) => r.id === activeRoleId) ?? TEAM_TASK_ROLES[0];

  return (
    <div>
      {countsStatus === "error" ? (
        <p className="ep-error" role="alert" data-testid="task-counts-error">
          {countsError || "Jumlah tugas tidak dapat dimuat."}
        </p>
      ) : null}
      {countsStatus === "truncated" ? (
        <p className="ep-notice" data-testid="task-counts-truncated">
          Jumlah tugas terbuka mencapai batas baca API — angka badge mungkin
          lebih rendah dari total sebenarnya.
        </p>
      ) : null}
      <div className="ep-task-rolerow-wrap">
        <div className="ep-task-rolerow" role="list" aria-label="Peran Tugas Tim">
          {TEAM_TASK_ROLES.map((role) => {
            const count = counts[role.id] ?? 0;
            const badge = formatOpenTaskBadge(count, countsStatus) ?? "0";
            const active = role.id === activeRole.id;
            const alert =
              countsStatus === "ready" || countsStatus === "truncated"
                ? count > 0
                : countsStatus === "error";
            return (
              <button
                key={role.id}
                type="button"
                role="listitem"
                className={`ep-task-rolechip${active ? " active" : ""}`}
                aria-pressed={active}
                onClick={() => setActiveRoleId(role.id)}
                title={role.label}
              >
                <span
                  className="ep-task-rolechip-ico"
                  style={{ background: role.box, color: role.color }}
                  aria-hidden
                >
                  <NavIcon name={role.icon} size={14} />
                </span>
                <span className="ep-task-rolechip-label">{role.label}</span>
                <span
                  className={`ep-task-rolechip-badge${alert ? " is-alert" : ""}`}
                  aria-label={
                    countsStatus === "error"
                      ? countsError || "Jumlah tidak tersedia"
                      : countsStatus === "loading"
                        ? "Memuat…"
                        : `${count} belum dikerjakan`
                  }
                  data-testid={`summary-badge-${role.id}`}
                  data-status={countsStatus}
                >
                  {badge}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      {apiMode ? (
        <TeamTaskApiPanel
          key={activeRole.id}
          role={activeRole.id}
          roleLabel={activeRole.label}
        />
      ) : (
        <RolePanel key={activeRole.id} roleId={activeRole.id} />
      )}
    </div>
  );
}
