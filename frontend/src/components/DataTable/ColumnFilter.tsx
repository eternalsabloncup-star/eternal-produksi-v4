import type { PoFilters } from "../../demo/types";
import { DEMO_BRANCHES, DEMO_MACHINES, DEMO_STATUSES } from "../../demo/fixtures";

type Props = {
  value: PoFilters;
  onChange: (next: PoFilters) => void;
  onReset: () => void;
  showStatus?: boolean;
  showMachine?: boolean;
  showUrgent?: boolean;
};

export function ColumnFilter({
  value,
  onChange,
  onReset,
  showStatus = true,
  showMachine = false,
  showUrgent = false,
}: Props) {
  return (
    <div className="ep-filterbar">
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
        value={value.branch || ""}
        onChange={(e) => onChange({ ...value, branch: e.target.value || undefined })}
        aria-label="Filter cabang"
      >
        <option value="">Semua cabang</option>
        {DEMO_BRANCHES.map((b) => (
          <option key={b} value={b}>
            {b}
          </option>
        ))}
      </select>
      {showStatus ? (
        <select
          value={value.status || ""}
          onChange={(e) => onChange({ ...value, status: e.target.value || undefined })}
          aria-label="Filter status"
        >
          <option value="">Semua status</option>
          {DEMO_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      ) : null}
      {showMachine ? (
        <select
          value={value.machine || ""}
          onChange={(e) => onChange({ ...value, machine: e.target.value || undefined })}
          aria-label="Filter mesin"
        >
          <option value="">Semua mesin</option>
          {DEMO_MACHINES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      ) : null}
      {showUrgent ? (
        <label className="ep-inlinecheck">
          <input
            type="checkbox"
            className="ep-checkbox"
            checked={Boolean(value.urgent)}
            onChange={(e) =>
              onChange({ ...value, urgent: e.target.checked ? true : undefined })
            }
          />
          Urgent
        </label>
      ) : null}
      <button type="button" className="ep-btn sm" onClick={onReset}>
        Reset filter
      </button>
      <span className="ep-sub" style={{ marginLeft: "auto" }}>
        Filter berlaku ke halaman tabel (DEMO)
      </span>
    </div>
  );
}
