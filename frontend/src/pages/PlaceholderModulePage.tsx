type Props = {
  title: string;
  subtitle: string;
  nextHint: string;
};

/** Kerangka modul; judul halaman hanya di topbar (prop title/subtitle untuk pemanggil). */
export function PlaceholderModulePage({ nextHint }: Props) {
  return (
    <div>
      <div className="ep-placeholder">
        <h3>Modul belum diimplementasikan</h3>
        <p>{nextHint}</p>
      </div>
    </div>
  );
}
