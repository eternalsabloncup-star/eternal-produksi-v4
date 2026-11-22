type Props = {
  dirtyCount: number;
  dirtyLabel?: string;
  cleanLabel?: string;
  saving?: boolean;
  saveLabel: string;
  discardLabel?: string;
  disabled?: boolean;
  onSave: () => void;
  onDiscard: () => void;
  statusId?: string;
};

/** Sticky save bar untuk editor draft (satu domain transaksi). */
export function SettingsStickySaveBar({
  dirtyCount,
  dirtyLabel,
  cleanLabel = "Tidak ada perubahan",
  saving,
  saveLabel,
  discardLabel = "Batalkan Perubahan",
  disabled,
  onSave,
  onDiscard,
  statusId,
}: Props) {
  const dirty = dirtyCount > 0;
  return (
    <div className="ep-settings-sticky-save" role="region" aria-label="Penyimpanan">
      <p className="ep-settings-meta" id={statusId} aria-live="polite">
        {dirty
          ? dirtyLabel ||
            `${dirtyCount} perubahan belum disimpan`
          : cleanLabel}
      </p>
      <div className="ep-settings-sticky-save-actions">
        <button
          type="button"
          className="ep-btn"
          disabled={!dirty || saving || disabled}
          onClick={onDiscard}
        >
          {discardLabel}
        </button>
        <button
          type="button"
          className={`ep-btn primary${saving ? " is-busy" : ""}`}
          disabled={!dirty || saving || disabled}
          onClick={onSave}
        >
          {saving ? "Menyimpan…" : saveLabel}
        </button>
      </div>
    </div>
  );
}
