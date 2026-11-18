import { useCallback, useEffect, useMemo, useState } from "react";
import {
  assignTaskTypeRole,
  fetchRoleActions,
  fetchTaskTypes,
  setRoleActions,
  type MasterTaskType,
  type OperationalRole,
  type RoleActionGrant,
  type TaskAction,
} from "../api/operational";

const ROLE_LABEL: Record<OperationalRole, string> = {
  "production-head": "Kepala Produksi",
  designer: "Designer",
  helper: "Helper",
  screener: "Screener",
  operator: "Operator",
  driver: "Driver",
};

const ACTION_LABEL: Record<TaskAction, string> = {
  view: "Lihat",
  input: "Input",
  confirm: "Konfirmasi",
  approve: "Approval",
  edit: "Edit",
  delete: "Hapus",
  manage_evidence: "Kelola Bukti",
};

function grantKey(role: string, taskType: string): string {
  return `${role}::${taskType}`;
}

/** Master/Pengaturan — jenis pekerjaan, role penanggung jawab, izin aksi. */
export function SettingsMasterPage() {
  const [taskTypes, setTaskTypes] = useState<MasterTaskType[]>([]);
  const [roles, setRoles] = useState<OperationalRole[]>([]);
  const [actions, setActions] = useState<TaskAction[]>([]);
  const [grants, setGrants] = useState<RoleActionGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [types, roleActions] = await Promise.all([
        fetchTaskTypes(),
        fetchRoleActions(),
      ]);
      setTaskTypes(types.items);
      setRoles(types.roles);
      setActions(types.actions);
      setGrants(roleActions.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat Master.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const grantMap = useMemo(() => {
    const map = new Map<string, TaskAction[]>();
    for (const g of grants) map.set(grantKey(g.role, g.task_type), g.actions);
    return map;
  }, [grants]);

  async function onAssignRole(taskType: string, role: OperationalRole) {
    setSavingKey(taskType);
    setError(null);
    setNotice(null);
    try {
      const updated = await assignTaskTypeRole(taskType, role);
      setTaskTypes((prev) =>
        prev.map((t) => (t.task_type === taskType ? { ...t, ...updated } : t)),
      );
      setNotice(`Jenis pekerjaan "${updated.label}" kini ditugaskan ke ${ROLE_LABEL[role]}.`);
      const refreshed = await fetchRoleActions();
      setGrants(refreshed.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan perubahan.");
    } finally {
      setSavingKey(null);
    }
  }

  async function onToggleAction(
    role: OperationalRole,
    taskType: string,
    action: TaskAction,
    enabled: boolean,
  ) {
    const key = grantKey(role, taskType);
    const current = grantMap.get(key) || [];
    const next = enabled
      ? Array.from(new Set([...current, action]))
      : current.filter((a) => a !== action);
    setSavingKey(key);
    setError(null);
    setNotice(null);
    try {
      const saved = await setRoleActions(role, taskType, next);
      setGrants((prev) => {
        const rest = prev.filter(
          (g) => !(g.role === role && g.task_type === taskType),
        );
        return [...rest, saved];
      });
      setNotice(
        `Izin ${ACTION_LABEL[action]} untuk ${ROLE_LABEL[role]} pada "${taskType}" ${
          enabled ? "diberikan" : "dicabut"
        }.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan izin.");
    } finally {
      setSavingKey(null);
    }
  }

  if (loading) {
    return (
      <div className="ep-placeholder">
        <h3>Memuat Master…</h3>
        <p>Mengambil jenis pekerjaan dan izin dari MongoDB V4.</p>
      </div>
    );
  }

  if (error && taskTypes.length === 0) {
    return (
      <div className="ep-placeholder">
        <h3>Master tidak dapat dimuat</h3>
        <p>{error}</p>
        <button type="button" className="ep-btn" onClick={() => void load()}>
          Coba lagi
        </button>
      </div>
    );
  }

  return (
    <div className="ep-master">
      <p className="ep-sectionlabel">
        Jenis Pekerjaan &amp; Role — sumber data MongoDB V4 (bukan DEMO)
      </p>
      {error ? <p className="ep-error">{error}</p> : null}
      {notice ? <p className="ep-notice">{notice}</p> : null}

      <div className="ep-tblscroll">
        <table className="ep-table">
          <thead>
            <tr>
              <th>Jenis Pekerjaan</th>
              <th>Cakupan</th>
              <th>Role Penanggung Jawab</th>
              <th>Pemetaan</th>
            </tr>
          </thead>
          <tbody>
            {taskTypes.map((t) => (
              <tr key={t.task_type}>
                <td>
                  {t.label}
                  {t.coordination ? " · koordinasi" : ""}
                </td>
                <td>{t.scope}</td>
                <td>
                  <select
                    value={t.assigned_role}
                    disabled={savingKey === t.task_type}
                    onChange={(e) =>
                      void onAssignRole(t.task_type, e.target.value as OperationalRole)
                    }
                  >
                    {roles.map((r) => (
                      <option key={r} value={r}>
                        {ROLE_LABEL[r]}
                      </option>
                    ))}
                  </select>
                </td>
                <td>{t.is_default_mapping ? "Default Owner" : "Dikonfigurasi"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="ep-sectionlabel">Izin Aksi per Role</p>
      <div className="ep-tblscroll">
        <table className="ep-table">
          <thead>
            <tr>
              <th>Role</th>
              <th>Jenis Pekerjaan</th>
              {actions.map((a) => (
                <th key={a}>{ACTION_LABEL[a]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {roles.flatMap((role) =>
              taskTypes.map((t) => {
                const key = grantKey(role, t.task_type);
                const granted = grantMap.get(key) || [];
                if (granted.length === 0 && t.assigned_role !== role) return null;
                return (
                  <tr key={key}>
                    <td>{ROLE_LABEL[role]}</td>
                    <td>{t.label}</td>
                    {actions.map((a) => (
                      <td key={a}>
                        <input
                          type="checkbox"
                          aria-label={`${ROLE_LABEL[role]} ${ACTION_LABEL[a]} ${t.label}`}
                          checked={granted.includes(a)}
                          disabled={savingKey === key}
                          onChange={(e) =>
                            void onToggleAction(role, t.task_type, a, e.target.checked)
                          }
                        />
                      </td>
                    ))}
                  </tr>
                );
              }),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
