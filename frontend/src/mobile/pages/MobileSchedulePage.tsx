import { ControlPage } from "../../pages/ControlPage";
import { useMobileOps } from "../MobileOpsContext";

/** Jadwal Produksi — sumber data existing (ScheduleApiPanel / demo table). */
export function MobileSchedulePage() {
  const ops = useMobileOps();
  if (ops && !ops.canOpenScheduleTabs) {
    return (
      <div className="ep-m-locked" data-testid="mobile-schedule-locked">
        <h2>Jadwal Produksi</h2>
        <p>
          Akses baca tugas (view_all) tidak membuka Jadwal Produksi. Hubungi
          administrator bila Anda memerlukan akses operasional jadwal.
        </p>
      </div>
    );
  }
  return (
    <div className="ep-m-schedule" data-testid="mobile-schedule">
      <ControlPage />
    </div>
  );
}
