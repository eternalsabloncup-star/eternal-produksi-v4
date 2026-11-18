import type { ReactNode } from "react";
import type { PoFilters } from "../../demo/types";
import { DEMO_BRANCHES, DEMO_MACHINES, DEMO_STATUSES } from "../../demo/fixtures";
import {
  inboxFilterEnabled,
  type InboxSourceMode,
} from "../../demo/inboxApiFilters";

type Props = {
  value: PoFilters;
  onChange: (next: PoFilters) => void;
  onReset: () => void;
  showStatus?: boolean;
  showMachine?: boolean;
  showUrgent?: boolean;
  /** Konten tambahan di ujung kanan baris (mis. Kolom Tabel / Reset tampilan). */
  trailing?: ReactNode;
  /** Teks bantuan DEMO di kanan; default true untuk halaman lama. */
  showDemoHint?: boolean;
  /** Satu baris + scroll horizontal khusus toolbar. */
  compact?: boolean;
  /**
   * Mode sumber Inbox.
   * API: cabang/status/mesin dinonaktifkan eksplisit (belum ada di Accurate).
   * DEMO: semua filter aktif seperti sebelumnya.
   */
  sourceMode?: InboxSourceMode;
};

export function ColumnFilter({
  value,
  onChange,
  onReset,
  showStatus = true,
  showMachine = false,
  showUrgent = false,
  trailing,
  showDemoHint = true,
  compact = false,
  sourceMode = "demo",
}: Props) {
  const branchEnabled = inboxFilterEnabled(sourceMode, "branch");
  const statusEnabled = inboxFilterEnabled(sourceMode, "status");
  const machineEnabled = inboxFilterEnabled(sourceMode, "machine");
  const urgentEnabled = inboxFilterEnabled(sourceMode, "urgent");

  return (
    <div className={`ep-filterbar${compact ? " is-compact" : ""}`}>
      <div className="ep-search">
        <span style={{ fontSize: 14, color: "#97a8bc" }} aria-hidden>
          ⌕
        </span>
        <input
          placeholder="Cari PO, pelanggan, produk..."
          value={value.q || ""}
          onChange={(e) => onChange({ ...value, q: e.target.value })}
          aria-label="Cari"
        />
      </div>
      <select
        value={branchEnabled ? value.branch || "" : ""}
        onChange={(e) => onChange({ ...value, branch: e.target.value || undefined })}
        aria-label="Filter cabang"
        disabled={!branchEnabled}
        title={
          branchEnabled
            ? undefined
            : "Filter cabang belum didukung untuk sumber Accurate (API)"
        }
      >
        <option value="">
          {branchEnabled ? "Semua cabang" : "Cabang (tidak tersedia di API)"}
        </option>
        {branchEnabled
          ? DEMO_BRANCHES.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))
          : null}
      </select>
      {showStatus ? (
        <select
          value={statusEnabled ? value.status || "" : ""}
          onChange={(e) =>
            onChange({ ...value, status: e.target.value || undefined })
          }
          aria-label="Filter status"
          disabled={!statusEnabled}
          title={
            statusEnabled
              ? undefined
              : "Filter status belum didukung untuk sumber Accurate (API)"
          }
        >
          <option value="">
            {statusEnabled ? "Semua status" : "Status (tidak tersedia di API)"}
          </option>
          {statusEnabled
            ? DEMO_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))
            : null}
        </select>
      ) : null}
      {showMachine ? (
        <select
          value={machineEnabled ? value.machine || "" : ""}
          onChange={(e) =>
            onChange({ ...value, machine: e.target.value || undefined })
          }
          aria-label="Filter mesin"
          disabled={!machineEnabled}
          title={
            machineEnabled
              ? undefined
              : "Filter mesin belum didukung untuk sumber Accurate (API)"
          }
        >
          <option value="">
            {machineEnabled ? "Semua mesin" : "Mesin (tidak tersedia di API)"}
          </option>
          {machineEnabled
            ? DEMO_MACHINES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))
            : null}
        </select>
      ) : null}
      {showUrgent ? (
        <label
          className={`ep-inlinecheck${!urgentEnabled ? " is-disabled" : ""}`}
          title={
            urgentEnabled
              ? undefined
              : "Filter urgent belum didukung untuk sumber ini"
          }
        >
          <input
            type="checkbox"
            className="ep-checkbox"
            checked={Boolean(value.urgent)}
            disabled={!urgentEnabled}
            onChange={(e) =>
              onChange({
                ...value,
                urgent: e.target.checked ? true : undefined,
              })
            }
          />
          Urgent
        </label>
      ) : null}
      <button type="button" className="ep-btn sm" onClick={onReset}>
        Reset filter
      </button>
      {trailing}
      {showDemoHint ? (
        <span className="ep-sub ep-filterbar-hint">
          Filter berlaku ke halaman tabel (DEMO)
        </span>
      ) : sourceMode === "api" ? (
        <span className="ep-sub ep-filterbar-hint">
          Filter aktif: pencarian &amp; urgent. Cabang/status belum tersedia di
          Accurate.
        </span>
      ) : null}
    </div>
  );
}
