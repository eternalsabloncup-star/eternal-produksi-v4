import type { TeamTaskRole } from "../app/nav";
import { DesignerTaskPanel } from "../components/DesignerTaskPanel";
import { DriverTaskPanel } from "../components/DriverTaskPanel";
import { HelperTaskPanel } from "../components/HelperTaskPanel";
import { OperatorTaskPanel } from "../components/OperatorTaskPanel";
import { ProductionHeadAllocTable } from "../components/ProductionHeadAllocTable";
import { ScreenerTaskPanel } from "../components/ScreenerTaskPanel";

type Props = {
  role: TeamTaskRole;
};

/** Halaman submenu peran — panel khusus per unit record Owner. */
export function TeamTaskPage({ role }: Props) {
  switch (role.id) {
    case "production-head":
      return (
        <div>
          <ProductionHeadAllocTable />
        </div>
      );
    case "designer":
      return (
        <div>
          <DesignerTaskPanel />
        </div>
      );
    case "screener":
      return (
        <div>
          <ScreenerTaskPanel />
        </div>
      );
    case "helper":
      return (
        <div>
          <HelperTaskPanel />
        </div>
      );
    case "operator":
      return (
        <div>
          <OperatorTaskPanel />
        </div>
      );
    case "driver":
      return (
        <div>
          <DriverTaskPanel />
        </div>
      );
    default:
      return null;
  }
}
