import { DemoBanner } from "../components/DemoBanner";
import { PageHeading } from "../components/PageHeading";

type Props = {
  title: string;
  subtitle: string;
  nextHint: string;
};

export function PlaceholderModulePage({ title, subtitle, nextHint }: Props) {
  return (
    <div>
      <PageHeading title={title} subtitle={subtitle} />
      <DemoBanner />
      <div className="ep-placeholder">
        <h3>Modul belum diimplementasikan</h3>
        <p>
          Halaman ini sengaja berupa kerangka UI untuk navigasi Task #004. Tidak ada
          workflow produksi lengkap, tidak ada write MongoDB, dan tidak ada API bisnis.
        </p>
        <p>
          <strong>Batas Task #005+:</strong> {nextHint}
        </p>
      </div>
    </div>
  );
}
