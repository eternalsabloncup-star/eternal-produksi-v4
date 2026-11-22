import { useMemo, useState, type FormEvent } from "react";
import type {
  MasterTaskType,
  OperationalRole,
} from "../../../api/operational";
import { roleWorkLabel, scopeLabel } from "../settingsLabels";
import { SettingsStickySaveBar } from "../SettingsStickySaveBar";

type ManualTask = {
  task_type: string;
  label: string;
  assigned_role: string;
};

type TaskTypesProps = {
  taskTypes: MasterTaskType[];
  rolesOps: OperationalRole[];
  manualTask: ManualTask;
  setManualTask: (v: ManualTask) => void;
  savingCreate: boolean;
  onCreate: (e: FormEvent) => void;
  onDeactivate: (t: MasterTaskType) => void;
};

export function TaskTypesPanel({
  taskTypes,
  rolesOps,
  manualTask,
  setManualTask,
  savingCreate,
  onCreate,
  onDeactivate,
}: TaskTypesProps) {
  const [q, setQ] = useState("");
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return taskTypes;
    return taskTypes.filter(
      (t) =>
        t.label.toLowerCase().includes(needle) ||
        t.task_type.toLowerCase().includes(needle),
    );
  }, [taskTypes, q]);

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Tambah jenis pekerjaan manual</h3>
        <form className="ep-settings-form-grid" onSubmit={onCreate}>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Kode jenis pekerjaan</span>
            <input
              value={manualTask.task_type}
              onChange={(e) =>
                setManualTask({ ...manualTask, task_type: e.target.value })
              }
              required
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Nama tampilan</span>
            <input
              value={manualTask.label}
              onChange={(e) =>
                setManualTask({ ...manualTask, label: e.target.value })
              }
              required
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Penanggung jawab awal</span>
            <select
              value={manualTask.assigned_role}
              onChange={(e) =>
                setManualTask({ ...manualTask, assigned_role: e.target.value })
              }
            >
              {rolesOps.map((r) => (
                <option key={r} value={r}>
                  {roleWorkLabel(r)}
                </option>
              ))}
            </select>
          </label>
          <div className="ep-settings-actions">
            <button
              type="submit"
              className={`ep-btn primary${savingCreate ? " is-busy" : ""}`}
              disabled={savingCreate}
            >
              {savingCreate ? "Menyimpan…" : "Tambah jenis pekerjaan"}
            </button>
          </div>
        </form>
      </section>

      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Jenis pekerjaan terdaftar</h3>
        <div className="ep-settings-toolbar">
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Cari</span>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Nama atau kode…"
              aria-label="Cari jenis pekerjaan"
            />
          </label>
        </div>
        <div className="ep-settings-table-wrap">
          <table className="ep-settings-data-table">
            <thead>
              <tr>
                <th>Jenis pekerjaan</th>
                <th className="is-status">Cakupan</th>
                <th className="is-role">Penanggung jawab</th>
                <th className="is-role">Sumber</th>
                <th className="is-actions">Aksi</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={5}>
                    <div className="ep-settings-empty">Belum ada jenis pekerjaan.</div>
                  </td>
                </tr>
              ) : (
                filtered.map((t) => (
                  <tr key={t.task_type}>
                    <td>
                      {t.label}
                      {t.coordination ? " · koordinasi" : ""}
                    </td>
                    <td className="is-status">{scopeLabel(t.scope)}</td>
                    <td className="is-role">{roleWorkLabel(t.assigned_role)}</td>
                    <td className="is-role">
                      {t.is_default_mapping ? "Bawaan sistem" : "Dikonfigurasi"}
                    </td>
                    <td className="is-actions">
                      <button
                        type="button"
                        className="ep-btn"
                        onClick={() => onDeactivate(t)}
                      >
                        Nonaktifkan
                      </button>
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

type AssignProps = {
  taskTypes: MasterTaskType[];
  rolesOps: OperationalRole[];
  draftAssignments: Record<string, OperationalRole>;
  setDraftAssignments: (
    next:
      | Record<string, OperationalRole>
      | ((p: Record<string, OperationalRole>) => Record<string, OperationalRole>),
  ) => void;
  dirtyCount: number;
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
  askConfirm: (req: {
    title: string;
    message: string;
    confirmLabel?: string;
  }) => Promise<{ ok: boolean; reason: string }>;
};

export function TaskAssignPanel({
  taskTypes,
  rolesOps,
  draftAssignments,
  setDraftAssignments,
  dirtyCount,
  saving,
  onSave,
  onDiscard,
  askConfirm,
}: AssignProps) {
  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState("");

  const filtered = useMemo(() => {
    return taskTypes.filter((t) => {
      const role = draftAssignments[t.task_type] || t.assigned_role;
      if (roleFilter && role !== roleFilter) return false;
      const needle = q.trim().toLowerCase();
      if (!needle) return true;
      return (
        t.label.toLowerCase().includes(needle) ||
        t.task_type.toLowerCase().includes(needle)
      );
    });
  }, [taskTypes, draftAssignments, q, roleFilter]);

  async function handleDiscard() {
    if (dirtyCount === 0) return;
    const res = await askConfirm({
      title: "Batalkan perubahan?",
      message: `${dirtyCount} perubahan penanggung jawab akan dibuang.`,
      confirmLabel: "Buang perubahan",
    });
    if (!res.ok) return;
    onDiscard();
  }

  return (
    <section className="ep-settings-card ep-settings-card-flush">
      <h3 className="ep-settings-card-title">Pembagian tugas</h3>
      <p className="ep-settings-card-note">
        Mengubah penanggung jawab tidak otomatis menyalin izin pekerjaan. Setiap jenis
        disimpan terpisah.
      </p>
      <div className="ep-settings-toolbar">
        <label className="ep-settings-field">
          <span className="ep-settings-field-label">Cari</span>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Nama jenis pekerjaan…"
            aria-label="Cari jenis pekerjaan"
          />
        </label>
        <label className="ep-settings-field">
          <span className="ep-settings-field-label">Filter penanggung jawab</span>
          <select
            value={roleFilter}
            onChange={(e) => setRoleFilter(e.target.value)}
            aria-label="Filter penanggung jawab"
          >
            <option value="">Semua</option>
            {rolesOps.map((r) => (
              <option key={r} value={r}>
                {roleWorkLabel(r)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="ep-settings-table-wrap">
        <table className="ep-settings-data-table">
          <thead>
            <tr>
              <th>Jenis pekerjaan</th>
              <th className="is-status">Cakupan</th>
              <th style={{ width: 200 }}>Penanggung jawab</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={3}>
                  <div className="ep-settings-empty">Belum ada jenis pekerjaan.</div>
                </td>
              </tr>
            ) : (
              filtered.map((t) => (
                <tr key={t.task_type}>
                  <td>{t.label}</td>
                  <td className="is-status">{scopeLabel(t.scope)}</td>
                  <td>
                    <select
                      className="ep-settings-inline-select"
                      value={draftAssignments[t.task_type] || t.assigned_role}
                      disabled={saving}
                      aria-label={`Penanggung jawab ${t.label}`}
                      onChange={(e) =>
                        setDraftAssignments((prev) => ({
                          ...prev,
                          [t.task_type]: e.target.value as OperationalRole,
                        }))
                      }
                    >
                      {rolesOps.map((r) => (
                        <option key={r} value={r}>
                          {roleWorkLabel(r)}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <SettingsStickySaveBar
        dirtyCount={dirtyCount}
        saving={saving}
        saveLabel="Simpan Penanggung Jawab"
        onSave={onSave}
        onDiscard={() => void handleDiscard()}
      />
    </section>
  );
}
