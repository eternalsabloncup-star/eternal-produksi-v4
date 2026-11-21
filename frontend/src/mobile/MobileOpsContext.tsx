import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { TeamTaskRole, TeamTaskRoleId } from "../app/nav";
import { TEAM_TASK_ROLES } from "../app/nav";
import { useAuth } from "../auth";
import {
  hasOperationsFull,
  hasViewAllTasks,
  isSuperAdmin,
} from "../roles";
import { resolveOpsRoleFromUser } from "./resolveOpsRole";

type MobileOpsContextValue = {
  /** Shell PWA aktif — DataTable aktifkan pinch zoom. */
  active: true;
  /** Role operasional dari akun login — null jika tidak punya ops role. */
  role: TeamTaskRole | null;
  /** Boleh membaca seluruh role operasional (Holding / grant view_all / SA / full). */
  canViewAllTasks: boolean;
  /** View-only: punya view_all tanpa otoritas mutation penuh. */
  tasksReadOnly: boolean;
  /** Filter data role untuk view_all — null = semua role. Bukan impersonation. */
  viewRoleFilter: TeamTaskRoleId | null;
  setViewRoleFilter: (id: TeamTaskRoleId | null) => void;
  /** Tab Jadwal Produksi / Pengiriman — bukan otomatis dari view_all. */
  canOpenScheduleTabs: boolean;
};

const MobileOpsContext = createContext<MobileOpsContextValue | null>(null);

export function MobileOpsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [viewRoleFilter, setViewRoleFilter] = useState<TeamTaskRoleId | null>(
    null,
  );

  const role = useMemo(() => resolveOpsRoleFromUser(user), [user]);
  const canViewAll = useMemo(() => hasViewAllTasks(user), [user]);
  const opsFull = useMemo(() => hasOperationsFull(user), [user]);
  const sa = isSuperAdmin(user?.role);

  const value = useMemo<MobileOpsContextValue>(
    () => ({
      active: true,
      role,
      canViewAllTasks: canViewAll || opsFull || sa,
      tasksReadOnly: canViewAll && !opsFull && !sa && !role,
      viewRoleFilter,
      setViewRoleFilter,
      // view_all tidak membuka dokumen jadwal/pengiriman secara otomatis.
      canOpenScheduleTabs: Boolean(role) || opsFull || sa,
    }),
    [role, canViewAll, opsFull, sa, viewRoleFilter],
  );

  return (
    <MobileOpsContext.Provider value={value}>
      {children}
    </MobileOpsContext.Provider>
  );
}

export function useMobileOps(): MobileOpsContextValue | null {
  return useContext(MobileOpsContext);
}

export function useMobileOpsActive(): boolean {
  return useContext(MobileOpsContext)?.active === true;
}

export function mobileViewRoleOptions(): TeamTaskRole[] {
  return TEAM_TASK_ROLES;
}
