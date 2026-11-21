import { TeamTaskApiPanel } from "../../components/TeamTaskApiPanel";
import { TeamTaskPage } from "../../pages/TeamTaskPage";
import { useProductionApiPanels } from "../../sim/previewMode";
import { useMobileOps } from "../MobileOpsContext";
import { toOperationalRole } from "../resolveOpsRole";

/**
 * Tugas Saya — strict parity dengan Tugas Tim web (TeamTaskApiPanel).
 * Tidak membuat status engine / assignment baru.
 */
export function MobileMyTasksPage() {
  const ops = useMobileOps();
  const apiPanels = useProductionApiPanels();

  if (!ops?.role) {
    return (
      <div className="ep-m-locked" data-testid="mobile-tasks-locked">
        <h2>Tugas Saya</h2>
        <p>
          Peran operasional belum di-assign di Master. Tidak ada tugas yang
          ditampilkan (fail-closed).
        </p>
      </div>
    );
  }

  if (apiPanels) {
    return (
      <div className="ep-m-tasks" data-testid="mobile-my-tasks">
        <TeamTaskApiPanel
          role={toOperationalRole(ops.role)}
          roleLabel={ops.role.label}
        />
      </div>
    );
  }

  return (
    <div className="ep-m-tasks" data-testid="mobile-my-tasks-demo">
      <TeamTaskPage role={ops.role} />
    </div>
  );
}
