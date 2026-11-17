import type { ColumnDef } from "./types";

type Props<T> = {
  columns: ColumnDef<T>[];
  hidden: Set<string>;
  onChange: (hidden: Set<string>) => void;
  open: boolean;
  onClose: () => void;
};

export function ColumnVisibility<T>({
  columns,
  hidden,
  onChange,
  open,
  onClose,
}: Props<T>) {
  if (!open) return null;

  const hideable = columns.filter((c) => c.hideable !== false);

  return (
    <div
      className="ep-modalshade"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="ep-drawer" role="dialog" aria-label="Atur kolom tabel">
        <div className="ep-drawerhead">
          <div>
            <div className="ep-eyebrow">Eternal Production V4</div>
            <h2>Atur Kolom Tabel</h2>
            <div className="ep-sub">Centang kolom yang ingin ditampilkan</div>
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>
        <div className="ep-drawerbody">
          <div className="ep-note">
            Pengaturan kolom untuk sesi UI ini. Kolom No. PO dan Pelanggan tetap
            disarankan sebagai acuan sticky.
          </div>
          <div className="ep-col-list">
            {hideable.map((col) => {
              const checked = !hidden.has(col.id);
              return (
                <label key={col.id}>
                  <input
                    type="checkbox"
                    className="ep-checkbox"
                    checked={checked}
                    onChange={(e) => {
                      const next = new Set(hidden);
                      if (e.target.checked) next.delete(col.id);
                      else next.add(col.id);
                      onChange(next);
                    }}
                  />
                  {col.label}
                </label>
              );
            })}
          </div>
        </div>
        <div className="ep-drawerfoot">
          <button type="button" className="ep-btn primary" onClick={onClose}>
            Selesai
          </button>
        </div>
      </div>
    </div>
  );
}
