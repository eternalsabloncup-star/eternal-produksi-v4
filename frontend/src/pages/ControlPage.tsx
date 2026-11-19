import { ScheduleApiPanel } from "../components/ScheduleApiPanel";
import { ScheduleProductionTable } from "../components/ScheduleProductionTable";
import { useProductionApiPanels } from "../sim/previewMode";

/** Halaman Jadwal Produksi — preview Cursor memakai ScheduleApiPanel. */
export function ControlPage() {
  const apiPanels = useProductionApiPanels();
  if (apiPanels) return <ScheduleApiPanel />;
  return (
    <div>
      <ScheduleProductionTable />
    </div>
  );
}
