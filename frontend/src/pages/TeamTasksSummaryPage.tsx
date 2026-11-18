import { useEffect, useMemo, useState } from "react";
import { NavIcon } from "../app/icons";
import {
  TEAM_TASK_ROLES,
  type TeamTaskRoleId,
} from "../app/nav";
import { DesignerTaskPanel } from "../components/DesignerTaskPanel";
import { DriverTaskPanel } from "../components/DriverTaskPanel";
import { HelperTaskPanel } from "../components/HelperTaskPanel";
import { OperatorTaskPanel } from "../components/OperatorTaskPanel";
import { ProductionHeadAllocTable } from "../components/ProductionHeadAllocTable";
import { ScreenerTaskPanel } from "../components/ScreenerTaskPanel";
import { TeamTaskApiPanel } from "../components/TeamTaskApiPanel";
import { resolveInboxPoSourceMode } from "../demo/resolveInboxMode";
import { subscribeProductionAlloc } from "../demo/productionHeadAlloc";
import { getOpenTeamTaskCounts } from "../demo/teamTasks";
import { subscribeTeamTaskWork } from "../demo/teamTaskWork";

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
  const apiMode =
    resolveInboxPoSourceMode(import.meta.env.VITE_INBOX_PO_SOURCE) === "api";
  const [activeRoleId, setActiveRoleId] =
    useState<TeamTaskRoleId>("production-head");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const unsubAlloc = subscribeProductionAlloc(() => setTick((t) => t + 1));
    const unsubWork = subscribeTeamTaskWork(() => setTick((t) => t + 1));
    return () => {
      unsubAlloc();
      unsubWork();
    };
  }, []);

  const activeRole =
    TEAM_TASK_ROLES.find((r) => r.id === activeRoleId) ?? TEAM_TASK_ROLES[0];

  const counts = useMemo(() => {
    void tick;
    return getOpenTeamTaskCounts();
  }, [tick]);

  return (
    <div>
      <div className="ep-task-rolerow-wrap">
        <div className="ep-task-rolerow" role="list" aria-label="Peran Tugas Tim">
          {TEAM_TASK_ROLES.map((role) => {
            const count = counts[role.id] ?? 0;
            const active = role.id === activeRole.id;
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
                  className={`ep-task-rolechip-badge${count > 0 ? " is-alert" : ""}`}
                  aria-label={`${count} belum dikerjakan`}
                >
                  {count}
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
