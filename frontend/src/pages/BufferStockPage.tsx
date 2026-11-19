import { BufferStockApiPanel } from "../components/BufferStockApiPanel";
import { BufferStockTable } from "../components/BufferStockTable";
import { useProductionApiPanels } from "../sim/previewMode";

/** Buffer Stock — preview Cursor memakai BufferStockApiPanel. */
export function BufferStockPage() {
  const apiPanels = useProductionApiPanels();
  if (apiPanels) return <BufferStockApiPanel />;
  return (
    <div>
      <BufferStockTable />
    </div>
  );
}
