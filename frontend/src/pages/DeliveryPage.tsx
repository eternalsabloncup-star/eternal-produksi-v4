import { DeliveryApiPanel } from "../components/DeliveryApiPanel";
import { DeliveryScheduleTable } from "../components/DeliveryScheduleTable";
import { useProductionApiPanels } from "../sim/previewMode";

/** Jadwal Pengiriman — preview Cursor memakai DeliveryApiPanel. */
export function DeliveryPage() {
  const apiPanels = useProductionApiPanels();
  return <div>{apiPanels ? <DeliveryApiPanel /> : <DeliveryScheduleTable />}</div>;
}
