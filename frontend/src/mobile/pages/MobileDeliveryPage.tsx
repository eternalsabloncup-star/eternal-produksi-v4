import { DeliveryPage } from "../../pages/DeliveryPage";
import { useMobileOps } from "../MobileOpsContext";

/** Jadwal Pengiriman — sumber SJ/delivery existing; gate Loading/Evidence tidak di-bypass. */
export function MobileDeliveryPage() {
  const ops = useMobileOps();
  if (ops && !ops.canOpenScheduleTabs) {
    return (
      <div className="ep-m-locked" data-testid="mobile-delivery-locked">
        <h2>Jadwal Pengiriman</h2>
        <p>
          Akses baca tugas (view_all) tidak membuka Jadwal Pengiriman. Hubungi
          administrator bila Anda memerlukan akses operasional pengiriman.
        </p>
      </div>
    );
  }
  return (
    <div className="ep-m-delivery" data-testid="mobile-delivery">
      <DeliveryPage />
    </div>
  );
}
