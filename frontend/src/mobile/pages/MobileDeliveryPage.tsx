import { DeliveryPage } from "../../pages/DeliveryPage";

/** Jadwal Pengiriman — sumber SJ/delivery existing; gate Loading/Evidence tidak di-bypass. */
export function MobileDeliveryPage() {
  return (
    <div className="ep-m-delivery" data-testid="mobile-delivery">
      <DeliveryPage />
    </div>
  );
}
