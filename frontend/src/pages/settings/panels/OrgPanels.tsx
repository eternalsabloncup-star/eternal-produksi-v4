import { useMemo, useState, type FormEvent } from "react";
import type { SettingsBranch } from "../../../api/settings";

type BranchProps = {
  branches: SettingsBranch[];
  newBranch: { code: string; name: string };
  setNewBranch: (v: { code: string; name: string }) => void;
  mappingEditCode: string | null;
  mappingEditValue: string;
  setMappingEditCode: (v: string | null) => void;
  setMappingEditValue: (v: string) => void;
  savingOrg: boolean;
  savingMapping: boolean;
  onSaveBranch: (e: FormEvent) => void;
  onToggleActive: (b: SettingsBranch) => void;
  onConfirmMapping: (b: SettingsBranch) => void;
};

export function BranchesPanel({
  branches,
  newBranch,
  setNewBranch,
  mappingEditCode,
  mappingEditValue,
  setMappingEditCode,
  setMappingEditValue,
  savingOrg,
  savingMapping,
  onSaveBranch,
  onToggleActive,
  onConfirmMapping,
}: BranchProps) {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return branches;
    return branches.filter(
      (b) =>
        b.code.toLowerCase().includes(needle) ||
        b.name.toLowerCase().includes(needle) ||
        (b.accurate_branch_id || "").includes(needle),
    );
  }, [branches, q]);

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Simpan cabang</h3>
        <p className="ep-settings-card-note">
          Kode cabang harus sama dengan ID cabang di Accurate Online (dari Sales Order)
          agar pilihan cabang di Pengguna dan otorisasi PO cocok. Nama tampilan hanya dari
          data yang sudah dicek — jangan mengarang nama dari angka ID.
        </p>
        <form className="ep-settings-form-grid" onSubmit={onSaveBranch}>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Kode</span>
            <input
              value={newBranch.code}
              onChange={(e) => setNewBranch({ ...newBranch, code: e.target.value })}
              required
              pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,15}"
              title="Huruf/angka/_/- (maks. 16), tanpa spasi"
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Nama</span>
            <input
              value={newBranch.name}
              onChange={(e) => setNewBranch({ ...newBranch, name: e.target.value })}
              required
            />
          </label>
          <div className="ep-settings-actions">
            <button
              type="submit"
              className={`ep-btn primary${savingOrg ? " is-busy" : ""}`}
              disabled={savingOrg}
            >
              {savingOrg ? "Menyimpan…" : "Simpan cabang"}
            </button>
          </div>
        </form>
      </section>

      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Daftar cabang &amp; tautan Accurate</h3>
        <div className="ep-settings-toolbar">
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Cari</span>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Kode, nama, atau ID Accurate…"
              aria-label="Cari cabang"
            />
          </label>
        </div>
        <div className="ep-settings-table-wrap">
          <table className="ep-settings-data-table is-compact">
            <thead>
              <tr>
                <th style={{ width: 100 }}>Kode</th>
                <th>Nama</th>
                <th className="is-status">Aktif</th>
                <th style={{ width: 120 }}>ID Accurate</th>
                <th className="is-status">Status tautan</th>
                <th className="is-actions">Aksi</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={6}>
                    <div className="ep-settings-empty">
                      Belum ada cabang, atau Anda tidak punya hak mengaksesnya.
                    </div>
                  </td>
                </tr>
              ) : (
                filtered.map((b) => {
                  const status = b.mapping_status || "unmapped";
                  const statusLabel =
                    status === "mapped"
                      ? "Terhubung"
                      : status === "conflict"
                        ? "Konflik"
                        : "Belum dipetakan";
                  const editing = mappingEditCode === b.code;
                  return (
                    <tr key={b.code}>
                      <td>{b.code}</td>
                      <td>{b.name}</td>
                      <td className="is-status">
                        <span className={`ep-settings-pill${b.is_active ? "" : " is-off"}`}>
                          {b.is_active ? "Ya" : "Tidak"}
                        </span>
                      </td>
                      <td>
                        {editing ? (
                          <input
                            value={mappingEditValue}
                            onChange={(e) => setMappingEditValue(e.target.value)}
                            inputMode="numeric"
                            pattern="[0-9]*"
                            placeholder="ID cabang Accurate"
                            aria-label={`ID cabang Accurate untuk ${b.code}`}
                            disabled={savingMapping}
                            style={{ width: "100%", maxWidth: 120 }}
                          />
                        ) : (
                          b.accurate_branch_id || "—"
                        )}
                      </td>
                      <td className="is-status">
                        <span
                          className={`ep-settings-pill${
                            status === "mapped"
                              ? ""
                              : status === "conflict"
                                ? " is-warn"
                                : " is-off"
                          }`}
                        >
                          {statusLabel}
                        </span>
                        {!b.accurate_verified && status === "mapped" ? (
                          <div className="ep-settings-hint" style={{ marginTop: 4 }}>
                            Belum diverifikasi
                          </div>
                        ) : null}
                      </td>
                      <td className="is-actions">
                        <div className="ep-settings-row-actions">
                          {editing ? (
                            <>
                              <button
                                type="button"
                                className={`ep-btn primary${savingMapping ? " is-busy" : ""}`}
                                disabled={savingMapping}
                                onClick={() => onConfirmMapping(b)}
                              >
                                {savingMapping ? "Menyimpan…" : "Konfirmasi tautan"}
                              </button>
                              <button
                                type="button"
                                className="ep-btn"
                                disabled={savingMapping}
                                onClick={() => {
                                  setMappingEditCode(null);
                                  setMappingEditValue("");
                                }}
                              >
                                Batalkan
                              </button>
                            </>
                          ) : (
                            <button
                              type="button"
                              className="ep-btn"
                              disabled={savingOrg || savingMapping}
                              onClick={() => {
                                setMappingEditCode(b.code);
                                setMappingEditValue(b.accurate_branch_id || "");
                              }}
                            >
                              {b.accurate_branch_id ? "Ganti tautan" : "Hubungkan Accurate"}
                            </button>
                          )}
                          <button
                            type="button"
                            className="ep-btn"
                            disabled={savingOrg || savingMapping}
                            onClick={() => onToggleActive(b)}
                          >
                            {b.is_active ? "Nonaktifkan" : "Aktifkan"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

type MachineForm = {
  code: string;
  label: string;
  branch_code: string;
  machine_type: string;
  output_per_shift_pcs: string;
};

type MachineProps = {
  machines: Array<{
    code: string;
    label: string;
    branch_code?: string;
    machine_type?: string | null;
    output_per_shift_pcs?: number | null;
  }>;
  newMachine: MachineForm;
  setNewMachine: (v: MachineForm) => void;
  branches: SettingsBranch[];
  savingOrg: boolean;
  onSave: (e: FormEvent) => void;
};

export function MachinesPanel({
  machines,
  newMachine,
  setNewMachine,
  branches,
  savingOrg,
  onSave,
}: MachineProps) {
  const [branchFilter, setBranchFilter] = useState("");
  const filtered = machines.filter((m) =>
    branchFilter ? (m.branch_code || "") === branchFilter : true,
  );

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Simpan mesin</h3>
        <form className="ep-settings-form-grid" onSubmit={onSave}>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Kode</span>
            <input
              value={newMachine.code}
              onChange={(e) => setNewMachine({ ...newMachine, code: e.target.value })}
              required
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Nama</span>
            <input
              value={newMachine.label}
              onChange={(e) => setNewMachine({ ...newMachine, label: e.target.value })}
              required
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Cabang (opsional)</span>
            <select
              value={newMachine.branch_code}
              onChange={(e) =>
                setNewMachine({ ...newMachine, branch_code: e.target.value })
              }
            >
              <option value="">— Tidak diisi —</option>
              {branches.map((b) => (
                <option key={b.code} value={b.code}>
                  {b.code} — {b.name}
                </option>
              ))}
            </select>
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Tipe mesin (opsional)</span>
            <input
              value={newMachine.machine_type}
              onChange={(e) =>
                setNewMachine({ ...newMachine, machine_type: e.target.value })
              }
              placeholder="mis. Automatic / Semi-automatic"
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">
              Output / shift pcs (opsional)
            </span>
            <input
              type="number"
              min={1}
              step={1}
              value={newMachine.output_per_shift_pcs}
              onChange={(e) =>
                setNewMachine({
                  ...newMachine,
                  output_per_shift_pcs: e.target.value,
                })
              }
              placeholder="Kosong = data belum lengkap"
            />
          </label>
          <div className="ep-settings-actions">
            <button
              type="submit"
              className={`ep-btn primary${savingOrg ? " is-busy" : ""}`}
              disabled={savingOrg}
            >
              {savingOrg ? "Menyimpan…" : "Simpan mesin"}
            </button>
          </div>
        </form>
      </section>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Daftar mesin</h3>
        <div className="ep-settings-toolbar">
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Filter cabang</span>
            <select
              value={branchFilter}
              onChange={(e) => setBranchFilter(e.target.value)}
              aria-label="Filter mesin per cabang"
            >
              <option value="">Semua cabang</option>
              {branches.map((b) => (
                <option key={b.code} value={b.code}>
                  {b.code} — {b.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="ep-settings-table-wrap">
          <table className="ep-settings-data-table is-compact">
            <thead>
              <tr>
                <th style={{ width: 120 }}>Kode</th>
                <th>Nama</th>
                <th style={{ width: 140 }}>Cabang</th>
                <th style={{ width: 140 }}>Tipe</th>
                <th style={{ width: 140 }}>Output/shift</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={5}>
                    <div className="ep-settings-empty">
                      Belum ada mesin, atau Anda tidak punya hak mengaksesnya.
                    </div>
                  </td>
                </tr>
              ) : (
                filtered.map((m) => (
                  <tr key={m.code}>
                    <td>{m.code}</td>
                    <td>{m.label}</td>
                    <td>{m.branch_code || "—"}</td>
                    <td>{m.machine_type || "—"}</td>
                    <td>
                      {m.output_per_shift_pcs != null
                        ? `${Number(m.output_per_shift_pcs).toLocaleString("id-ID")} pcs`
                        : "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

type ShiftProps = {
  shifts: Array<{
    code: string;
    label: string;
    start_time: string;
    end_time: string;
  }>;
  newShift: { code: string; label: string; start_time: string; end_time: string };
  setNewShift: (v: {
    code: string;
    label: string;
    start_time: string;
    end_time: string;
  }) => void;
  savingOrg: boolean;
  onSave: (e: FormEvent) => void;
};

export function ShiftsPanel({ shifts, newShift, setNewShift, savingOrg, onSave }: ShiftProps) {
  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Simpan jam kerja</h3>
        <form className="ep-settings-form-grid" onSubmit={onSave}>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Kode</span>
            <input
              value={newShift.code}
              onChange={(e) => setNewShift({ ...newShift, code: e.target.value })}
              required
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Nama</span>
            <input
              value={newShift.label}
              onChange={(e) => setNewShift({ ...newShift, label: e.target.value })}
              required
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Jam mulai</span>
            <input
              value={newShift.start_time}
              onChange={(e) => setNewShift({ ...newShift, start_time: e.target.value })}
              required
              placeholder="08:00"
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Jam selesai</span>
            <input
              value={newShift.end_time}
              onChange={(e) => setNewShift({ ...newShift, end_time: e.target.value })}
              required
              placeholder="16:00"
            />
          </label>
          <div className="ep-settings-actions">
            <button
              type="submit"
              className={`ep-btn primary${savingOrg ? " is-busy" : ""}`}
              disabled={savingOrg}
            >
              {savingOrg ? "Menyimpan…" : "Simpan jam kerja"}
            </button>
          </div>
        </form>
      </section>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Daftar jam kerja</h3>
        <div className="ep-settings-table-wrap">
          <table className="ep-settings-data-table is-compact">
            <thead>
              <tr>
                <th style={{ width: 120 }}>Kode</th>
                <th>Nama</th>
                <th className="is-status">Mulai</th>
                <th className="is-status">Selesai</th>
              </tr>
            </thead>
            <tbody>
              {shifts.length === 0 ? (
                <tr>
                  <td colSpan={4}>
                    <div className="ep-settings-empty">
                      Belum ada jam kerja, atau Anda tidak punya hak mengaksesnya.
                    </div>
                  </td>
                </tr>
              ) : (
                shifts.map((s) => (
                  <tr key={s.code}>
                    <td>{s.code}</td>
                    <td>{s.label}</td>
                    <td className="is-status">{s.start_time}</td>
                    <td className="is-status">{s.end_time}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
