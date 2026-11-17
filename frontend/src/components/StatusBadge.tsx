const TONE: Record<string, string> = {
  "PO Baru": "blue",
  "Belum Terjadwal": "amber",
  Dijadwalkan: "purple",
  Persiapan: "purple",
  "Menunggu Sampel": "amber",
  Produksi: "blue",
  QC: "purple",
  "Siap Kirim": "green",
  Terkirim: "green",
  Terlambat: "red",
};

type Props = {
  status: string;
};

export function StatusBadge({ status }: Props) {
  const tone = TONE[status] || "gray";
  return <span className={`ep-pill ${tone}`}>{status}</span>;
}
