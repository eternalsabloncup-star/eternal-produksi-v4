import type { TeamTaskRole } from "../app/nav";
import { TeamTaskApiPanel } from "../components/TeamTaskApiPanel";
import { DesignerTaskPanel } from "../components/DesignerTaskPanel";
import { DriverTaskPanel } from "../components/DriverTaskPanel";
import { HelperTaskPanel } from "../components/HelperTaskPanel";
import { OperatorTaskPanel } from "../components/OperatorTaskPanel";
import { ProductionHeadAllocTable } from "../components/ProductionHeadAllocTable";
import { ScreenerTaskPanel } from "../components/ScreenerTaskPanel";
import { useProductionApiPanels } from "../sim/previewMode";

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

/**
 * Preview Cursor / simulasi API → selalu TeamTaskApiPanel.
 * DemoRolePanel hanya jika VITE_INBOX_PO_SOURCE=demo dan simulasi OFF.
 */
export function TeamTaskPage({ role }: Props) {
  const apiPanels = useProductionApiPanels();
  return (
    <div>
      {apiPanels ? (
        <TeamTaskApiPanel role={role.id} roleLabel={role.label} />
      ) : (
        <DemoRolePanel role={role} />
      )}
    </div>
  );
}
