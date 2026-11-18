import type { TeamTaskRole } from "../app/nav";
import { DesignerTaskPanel } from "../components/DesignerTaskPanel";
import { DriverTaskPanel } from "../components/DriverTaskPanel";
import { HelperTaskPanel } from "../components/HelperTaskPanel";
import { OperatorTaskPanel } from "../components/OperatorTaskPanel";
import { ProductionHeadAllocTable } from "../components/ProductionHeadAllocTable";
import { ScreenerTaskPanel } from "../components/ScreenerTaskPanel";
import { TeamTaskApiPanel } from "../components/TeamTaskApiPanel";
import { resolveInboxPoSourceMode } from "../demo/resolveInboxMode";

type Props = {
  role: TeamTaskRole;
};

function DemoRolePanel({ role }: Props) {
  switch (role.id) {
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

/** Halaman submenu peran: task engine nyata di mode API, DEMO lokal di mode demo. */
export function TeamTaskPage({ role }: Props) {
  const apiMode =
    resolveInboxPoSourceMode(import.meta.env.VITE_INBOX_PO_SOURCE) === "api";
  return (
    <div>
      {apiMode ? (
        <TeamTaskApiPanel role={role.id} roleLabel={role.label} />
      ) : (
        <DemoRolePanel role={role} />
      )}
    </div>
  );
}
