import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { TeamTaskRoleId } from "../app/nav";
import { useAuth } from "../auth";
import { isSuperAdmin } from "../roles";
import type { TeamTaskRole } from "../app/nav";
import { resolveOpsRoleFromUser, teamTaskRoleById } from "./resolveOpsRole";

type MobileOpsContextValue = {
  /** Shell PWA aktif — DataTable aktifkan pinch zoom. */
  active: true;
  role: TeamTaskRole | null;
  roleOverride: TeamTaskRoleId | null;
  setRoleOverride: (id: TeamTaskRoleId | null) => void;
  canPickRole: boolean;
};

const MobileOpsContext = createContext<MobileOpsContextValue | null>(null);

export function MobileOpsProvider({ children }: { children: ReactNode }) {
  const { user, isSuperAdmin: authSuper } = useAuth();
  const elevated = authSuper || isSuperAdmin(user?.role);
  const [roleOverride, setRoleOverride] = useState<TeamTaskRoleId | null>(null);

  const role = useMemo(
    () => resolveOpsRoleFromUser(user, elevated ? roleOverride : null),
    [user, elevated, roleOverride],
  );

  // Super Admin tanpa ops role: default tampilan Kepala Produksi agar preview bisa diuji.
  const effectiveRole = useMemo(() => {
    if (role) return role;
    if (elevated) {
      return teamTaskRoleById(roleOverride ?? "production-head");
    }
    return null;
  }, [role, elevated, roleOverride]);

  const value = useMemo<MobileOpsContextValue>(
    () => ({
      active: true,
      role: effectiveRole,
      roleOverride,
      setRoleOverride,
      canPickRole: elevated,
    }),
    [effectiveRole, roleOverride, elevated],
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
