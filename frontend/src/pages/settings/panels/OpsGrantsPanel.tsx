import { useEffect, useMemo, useState } from "react";
import type {
  MasterTaskType,
  OperationalRole,
  RoleActionGrant,
  TaskAction,
} from "../../../api/operational";
import {
  ACTION_LABEL,
  roleWorkLabel,
  sortActions,
} from "../settingsLabels";
import {
  applyColumnSelectAll,
  columnSelectState,
  countChangedGrants,
  filterMatrixRows,
  grantKey,
  isMatrixRowVisible,
  toggleDraftAction,
  type ColumnSelectState,
} from "../settingsDraftUtils";
import { SettingsStickySaveBar } from "../SettingsStickySaveBar";

type Props = {
  rolesOps: OperationalRole[];
  taskTypes: MasterTaskType[];
  actions: TaskAction[];
  grants: RoleActionGrant[];
  draftGrants: RoleActionGrant[];
  setDraftGrants: (next: RoleActionGrant[] | ((p: RoleActionGrant[]) => RoleActionGrant[])) => void;
  loading: boolean;
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
  askConfirm: (req: {
    title: string;
    message: string;
    details?: string[];
    confirmLabel?: string;
  }) => Promise<{ ok: boolean; reason: string }>;
};

type VisRow = {
  role: OperationalRole;
  task: MasterTaskType;
  actions: TaskAction[];
};

function useIsCompact(maxWidth = 860) {
  const [compact, setCompact] = useState(
    () => typeof window !== "undefined" && window.matchMedia(`(max-width: ${maxWidth}px)`).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${maxWidth}px)`);
    const fn = () => setCompact(mq.matches);
    fn();
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, [maxWidth]);
  return compact;
}

function SelectAllCheckbox({
  state,
  label,
  disabled,
  onToggle,
}: {
  state: ColumnSelectState;
  label: string;
  disabled?: boolean;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <input
      type="checkbox"
      checked={state === "all"}
      ref={(el) => {
        if (el) el.indeterminate = state === "partial";
      }}
      disabled={disabled}
      aria-label={`Centang semua: ${label}`}
      title={`Centang semua kolom ${label} pada baris yang tampil`}
      onChange={(e) => onToggle(e.target.checked)}
    />
  );
}

export function OpsGrantsPanel({
  rolesOps,
  taskTypes,
  actions: rawActions,
  grants,
  draftGrants,
  setDraftGrants,
  loading,
  saving,
  onSave,
  onDiscard,
  askConfirm,
}: Props) {
  const compact = useIsCompact(860);
  const actions = useMemo(() => sortActions(rawActions), [rawActions]);
  const [roleFilter, setRoleFilter] = useState<string>("");
  const [query, setQuery] = useState("");
  const [dirtyOnly, setDirtyOnly] = useState(false);
  const [mobileTask, setMobileTask] = useState<string | null>(null);

  const draftMap = useMemo(() => {
    const map = new Map<string, TaskAction[]>();
    for (const g of draftGrants) map.set(grantKey(g.role, g.task_type), g.actions);
    return map;
  }, [draftGrants]);

  const serverMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const g of grants) {
      map.set(grantKey(g.role, g.task_type), [...g.actions].sort().join("|"));
    }
    return map;
  }, [grants]);

  const allRows: VisRow[] = useMemo(() => {
    const rows: VisRow[] = [];
    const roleList = roleFilter
      ? rolesOps.filter((r) => r === roleFilter)
      : rolesOps;
    for (const role of roleList) {
      for (const task of taskTypes) {
        const granted = draftMap.get(grantKey(role, task.task_type)) || [];
        if (!isMatrixRowVisible(role, task, granted)) continue;
        rows.push({ role, task, actions: granted });
      }
    }
    return rows;
  }, [rolesOps, taskTypes, draftMap, roleFilter]);

  const filteredRows = useMemo(() => {
    let rows = filterMatrixRows(
      allRows.map((r) => ({ ...r, label: r.task.label, task_type: r.task.task_type })),
      query,
    ).map((r) => ({
      role: r.role,
      task: r.task,
      actions: r.actions,
    }));
    if (dirtyOnly) {
      rows = rows.filter((r) => {
        const key = grantKey(r.role, r.task.task_type);
        const next = [...r.actions].sort().join("|");
        return (serverMap.get(key) || "") !== next;
      });
    }
    return rows;
  }, [allRows, query, dirtyOnly, serverMap]);

  const dirtyCount = countChangedGrants(draftGrants, grants);

  async function handleColumnSelect(action: TaskAction, enabled: boolean) {
    const n = filteredRows.length;
    if (n === 0) return;
    const res = await askConfirm({
      title: enabled ? "Centang semua kolom" : "Hapus centang kolom",
      message: enabled
        ? `Centang "${ACTION_LABEL[action] || action}" pada ${n} baris yang sedang ditampilkan?`
        : `Hapus centang "${ACTION_LABEL[action] || action}" pada ${n} baris yang sedang ditampilkan?`,
      details: [
        "Hanya mengubah tampilan draft di perangkat Anda.",
        "Belum tersimpan ke server sampai Anda menekan Simpan Izin Pekerjaan.",
        "Baris di luar filter tidak diubah.",
      ],
      confirmLabel: enabled ? "Centang semua" : "Hapus centang",
    });
    if (!res.ok) return;
    setDraftGrants((prev) =>
      applyColumnSelectAll(
        prev,
        filteredRows.map((r) => ({ role: r.role, task_type: r.task.task_type })),
        action,
        enabled,
      ),
    );
  }

  async function handleDiscard() {
    if (dirtyCount === 0) return;
    const res = await askConfirm({
      title: "Batalkan perubahan?",
      message: `${dirtyCount} perubahan izin pekerjaan akan dibuang.`,
      confirmLabel: "Buang perubahan",
    });
    if (!res.ok) return;
    onDiscard();
  }

  if (loading) {
    return <p className="ep-settings-empty">Memuat izin pekerjaan…</p>;
  }

  const mobileGroups = useMemo(() => {
    const byTask = new Map<string, VisRow[]>();
    for (const r of filteredRows) {
      const list = byTask.get(r.task.task_type) || [];
      list.push(r);
      byTask.set(r.task.task_type, list);
    }
    return [...byTask.entries()].map(([taskType, rows]) => ({
      taskType,
      label: rows[0]?.task.label || taskType,
      rows,
    }));
  }, [filteredRows]);

  const selectedMobile = mobileGroups.find((g) => g.taskType === mobileTask) || null;

  return (
    <section className="ep-settings-card ep-settings-card-flush">
      <h3 className="ep-settings-card-title">Izin pekerjaan</h3>
      <p className="ep-settings-card-note">
        Centang hanya menyiapkan perubahan. Setiap baris disimpan terpisah — sebagian
        bisa berhasil jika ada yang gagal.
      </p>

      <div className="ep-settings-toolbar" role="search">
        <label className="ep-settings-field">
          <span className="ep-settings-field-label">Peran kerja</span>
          <select
            value={roleFilter}
            onChange={(e) => {
              setRoleFilter(e.target.value);
              setMobileTask(null);
            }}
            aria-label="Filter peran kerja"
          >
            <option value="">Semua peran kerja</option>
            {rolesOps.map((r) => (
              <option key={r} value={r}>
                {roleWorkLabel(r)}
              </option>
            ))}
          </select>
        </label>
        <label className="ep-settings-field">
          <span className="ep-settings-field-label">Cari jenis pekerjaan</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Nama atau kode…"
            aria-label="Cari jenis pekerjaan"
          />
        </label>
        <label className="ep-settings-check-row">
          <input
            type="checkbox"
            checked={dirtyOnly}
            onChange={(e) => setDirtyOnly(e.target.checked)}
          />
          Hanya yang berubah
        </label>
        <div className="ep-settings-toolbar-meta" aria-live="polite">
          <span>{filteredRows.length} baris</span>
          <span>{dirtyCount} belum disimpan</span>
        </div>
      </div>

      {compact ? (
        <div className="ep-settings-mobile-matrix">
          {!selectedMobile ? (
            <ul className="ep-settings-mobile-list">
              {mobileGroups.length === 0 ? (
                <li className="ep-settings-empty">Tidak ada jenis pekerjaan yang cocok.</li>
              ) : (
                mobileGroups.map((g) => (
                  <li key={g.taskType}>
                    <button
                      type="button"
                      className="ep-settings-mobile-item"
                      onClick={() => setMobileTask(g.taskType)}
                    >
                      <span className="ep-settings-mobile-item-title">{g.label}</span>
                      <span className="ep-settings-mobile-item-meta">
                        {g.rows.length} peran kerja
                      </span>
                    </button>
                  </li>
                ))
              )}
            </ul>
          ) : (
            <div className="ep-settings-mobile-detail">
              <button
                type="button"
                className="ep-btn"
                onClick={() => setMobileTask(null)}
              >
                ← Kembali ke daftar
              </button>
              <h4 className="ep-settings-mobile-detail-title">{selectedMobile.label}</h4>
              {selectedMobile.rows.map((row) => (
                <fieldset key={grantKey(row.role, row.task.task_type)} className="ep-settings-mobile-grant">
                  <legend>{roleWorkLabel(row.role)}</legend>
                  <div className="ep-settings-check-grid">
                    {actions.map((a) => (
                      <label key={a} className="ep-settings-check">
                        <input
                          type="checkbox"
                          checked={row.actions.includes(a)}
                          disabled={saving}
                          onChange={(e) =>
                            setDraftGrants((prev) =>
                              toggleDraftAction(
                                prev,
                                row.role,
                                row.task.task_type,
                                a,
                                e.target.checked,
                              ),
                            )
                          }
                        />
                        <span>{ACTION_LABEL[a] || a}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="ep-settings-table-wrap">
          <table className="ep-settings-data-table is-wide">
            <thead>
              <tr>
                <th className="is-role">Peran kerja</th>
                <th className="is-type">Jenis pekerjaan</th>
                {actions.map((a) => {
                  const state = columnSelectState(
                    filteredRows.map((r) => ({ actions: r.actions })),
                    a,
                  );
                  return (
                    <th key={a} className="is-check">
                      <div className="ep-settings-colhead">
                        <SelectAllCheckbox
                          state={state}
                          label={ACTION_LABEL[a] || a}
                          disabled={saving || filteredRows.length === 0}
                          onToggle={(enabled) => void handleColumnSelect(a, enabled)}
                        />
                        <span>{ACTION_LABEL[a] || a}</span>
                      </div>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {filteredRows.length === 0 ? (
                <tr>
                  <td colSpan={2 + actions.length}>
                    <div className="ep-settings-empty">
                      Tidak ada baris yang cocok dengan filter.
                    </div>
                  </td>
                </tr>
              ) : (
                filteredRows.map((row) => (
                  <tr key={grantKey(row.role, row.task.task_type)}>
                    <td className="is-role">{roleWorkLabel(row.role)}</td>
                    <td className="is-type" title={row.task.label}>
                      {row.task.label}
                    </td>
                    {actions.map((a) => (
                      <td key={a} className="is-check">
                        <input
                          type="checkbox"
                          checked={row.actions.includes(a)}
                          disabled={saving}
                          aria-label={`${ACTION_LABEL[a] || a} — ${roleWorkLabel(row.role)} — ${row.task.label}`}
                          onChange={(e) =>
                            setDraftGrants((prev) =>
                              toggleDraftAction(
                                prev,
                                row.role,
                                row.task.task_type,
                                a,
                                e.target.checked,
                              ),
                            )
                          }
                        />
                      </td>
                    ))}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      <SettingsStickySaveBar
        dirtyCount={dirtyCount}
        saving={saving}
        saveLabel="Simpan Izin Pekerjaan"
        onSave={onSave}
        onDiscard={() => void handleDiscard()}
        statusId="ops-grants-save-status"
      />
    </section>
  );
}
