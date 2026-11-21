import { TEAM_TASK_ROLES } from "../../app/nav";
import { TeamTaskApiPanel } from "../../components/TeamTaskApiPanel";
import { TeamTaskPage } from "../../pages/TeamTaskPage";
import { useProductionApiPanels } from "../../sim/previewMode";
import { useMobileOps } from "../MobileOpsContext";
import { teamTaskRoleById, toOperationalRole } from "../resolveOpsRole";

/**
 * Tugas Saya — strict parity dengan Tugas Tim web (TeamTaskApiPanel).
 * Ops user: role dari akun. Holding/view_all: filter data role (bukan impersonate).
 */
export function MobileMyTasksPage() {
  const ops = useMobileOps();
  const apiPanels = useProductionApiPanels();

  if (!ops) return null;

  if (!ops.role && !ops.canViewAllTasks) {
    return (
      <div className="ep-m-locked" data-testid="mobile-tasks-locked">
        <h2>Tugas Saya</h2>
        <p>Role operasional belum ditetapkan. Hubungi administrator.</p>
      </div>
    );
  }

  if (apiPanels) {
    const filterRole = ops.role
      ? ops.role
      : ops.viewRoleFilter
        ? teamTaskRoleById(ops.viewRoleFilter)
        : null;
    const allowAll =
      !ops.role && ops.canViewAllTasks && ops.viewRoleFilter === null;

    return (
      <div className="ep-m-tasks" data-testid="mobile-my-tasks">
        <TeamTaskApiPanel
          role={filterRole ? toOperationalRole(filterRole) : null}
          roleLabel={
            filterRole?.label ??
            (allowAll ? "Semua Role" : TEAM_TASK_ROLES[0].label)
          }
          initialStatusFilter="belum"
          readOnly={ops.tasksReadOnly}
          allowAllRoles={allowAll}
        />
      </div>
    );
  }

  if (!ops.role) {
    return (
      <div className="ep-m-locked" data-testid="mobile-tasks-locked-demo">
        <h2>Tugas Saya</h2>
        <p>Mode DEMO memerlukan peran operasional.</p>
      </div>
    );
  }

  return (
    <div className="ep-m-tasks" data-testid="mobile-my-tasks-demo">
      <TeamTaskPage role={ops.role} />
    </div>
  );
}
