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
import {
  createManualTaskType,
  createSettingsRole,
  createSettingsUser,
  fetchAccurateStatus,
  fetchAuditLog,
  fetchBranches,
  fetchMachines,
  fetchSettingsCatalog,
  fetchSettingsRoles,
  fetchSettingsUsers,
  fetchShifts,
  saveRolePermissions,
  setUserActive,
  SettingsApiError,
  upsertBranch,
  upsertMachine,
  upsertShift,
  type AuditItem,
  type SettingsRole,
  type SettingsUser,
} from "../api/settings";

type TabId =
  | "overview"
  | "users"
  | "roles"
  | "task-types"
  | "org"
  | "products"
  | "accurate"
  | "audit"
  | "workflow";

const TABS: { id: TabId; label: string }[] = [
  { id: "overview", label: "Ringkasan" },
  { id: "users", label: "Pengguna" },
  { id: "roles", label: "Role & Permission" },
  { id: "task-types", label: "Jenis Tugas" },
  { id: "org", label: "Cabang / Mesin / Shift" },
  { id: "products", label: "Master Produk" },
  { id: "workflow", label: "Kebijakan Workflow" },
  { id: "accurate", label: "Accurate" },
  { id: "audit", label: "Audit Log" },
];

const ROLE_LABEL: Record<string, string> = {
  "production-head": "Kepala Produksi",
  designer: "Designer",
  helper: "Helper",
  screener: "Screener",
  operator: "Operator",
  driver: "Driver",
};

const ACTION_LABEL: Record<string, string> = {
  view: "Lihat",
  input: "Input",
  confirm: "Konfirmasi",
  approve: "Approval",
  edit: "Edit",
  delete: "Hapus",
  manage_evidence: "Kelola Bukti",
  cancel: "Batal",
};

function grantKey(role: string, taskType: string): string {
  return `${role}::${taskType}`;
}

/** Pengaturan V4 — shell bertab + Master task/grants yang sudah ada. */
export function SettingsMasterPage() {
  const [tab, setTab] = useState<TabId>("overview");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Existing Master task/role-actions
  const [taskTypes, setTaskTypes] = useState<MasterTaskType[]>([]);
  const [rolesOps, setRolesOps] = useState<OperationalRole[]>([]);
  const [actions, setActions] = useState<TaskAction[]>([]);
  const [grants, setGrants] = useState<RoleActionGrant[]>([]);
  const [masterLoading, setMasterLoading] = useState(true);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  // Settings entities
  const [users, setUsers] = useState<SettingsUser[]>([]);
  const [roles, setRoles] = useState<SettingsRole[]>([]);
  const [modulePerms, setModulePerms] = useState<string[]>([]);
  const [sensitivePerms, setSensitivePerms] = useState<string[]>([]);
  const [draftPerms, setDraftPerms] = useState<string[]>([]);
  const [selectedRole, setSelectedRole] = useState<string>("");
  const [permReason, setPermReason] = useState("");
  const [confirmSensitive, setConfirmSensitive] = useState(false);
  const [branches, setBranches] = useState<Array<{ code: string; name: string }>>([]);
  const [machines, setMachines] = useState<
    Array<{ code: string; label: string; branch_code?: string }>
  >([]);
  const [shifts, setShifts] = useState<
    Array<{ code: string; label: string; start_time: string; end_time: string }>
  >([]);
  const [audits, setAudits] = useState<AuditItem[]>([]);
  const [accurate, setAccurate] = useState<{
    configured: Record<string, boolean>;
    sj_live_sync_enabled: boolean;
    note: string;
  } | null>(null);
  const [policies, setPolicies] = useState<
    Array<{ id: string; label: string; status: string; note: string }>
  >([]);

  const [newUser, setNewUser] = useState({
    email: "",
    display_name: "",
    password: "",
    role: "user",
    operational_role: "",
  });
  const [newRole, setNewRole] = useState({ role_key: "", label: "" });
  const [newBranch, setNewBranch] = useState({ code: "", name: "" });
  const [newMachine, setNewMachine] = useState({ code: "", label: "", branch_code: "" });
  const [newShift, setNewShift] = useState({
    code: "",
    label: "",
    start_time: "08:00",
    end_time: "16:00",
  });
  const [manualTask, setManualTask] = useState({
    task_type: "",
    label: "",
    assigned_role: "helper",
  });

  const loadMaster = useCallback(async () => {
    setMasterLoading(true);
    try {
      const [types, roleActions] = await Promise.all([
        fetchTaskTypes(),
        fetchRoleActions(),
      ]);
      setTaskTypes(types.items);
      setRolesOps(types.roles);
      setActions(types.actions);
      setGrants(roleActions.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat Master.");
    } finally {
      setMasterLoading(false);
    }
  }, []);

  const loadSettings = useCallback(async () => {
    setError(null);
    try {
      const cat = await fetchSettingsCatalog();
      setModulePerms(cat.module_permissions);
      setSensitivePerms(cat.sensitive_module_permissions);
      setPolicies(cat.workflow_policies.items);
      const [u, r, b, m, s, a, acc] = await Promise.all([
        fetchSettingsUsers().catch(() => ({ items: [] as SettingsUser[] })),
        fetchSettingsRoles().catch(() => ({ items: [] as SettingsRole[] })),
        fetchBranches().catch(() => ({ items: [] })),
        fetchMachines().catch(() => ({ items: [] })),
        fetchShifts().catch(() => ({ items: [] })),
        fetchAuditLog({ module: "settings", limit: 50 }).catch(() => ({
          items: [] as AuditItem[],
          total: 0,
        })),
        fetchAccurateStatus().catch(() => null),
      ]);
      setUsers(u.items);
      setRoles(r.items);
      setSelectedRole((prev) => prev || (r.items[0]?.role_key ?? ""));
      setBranches(b.items);
      setMachines(m.items);
      setShifts(s.items);
      setAudits(a.items);
      setAccurate(acc);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat Settings.");
    }
  }, []);

  useEffect(() => {
    void loadMaster();
    void loadSettings();
  }, [loadMaster, loadSettings]);

  useEffect(() => {
    const role = roles.find((x) => x.role_key === selectedRole);
    setDraftPerms(role ? [...role.module_permissions] : []);
    setConfirmSensitive(false);
    setPermReason("");
  }, [selectedRole, roles]);

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
      setNotice(`Jenis pekerjaan "${updated.label}" ditugaskan ke ${ROLE_LABEL[role]}.`);
      const refreshed = await fetchRoleActions();
      setGrants(refreshed.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan.");
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
    try {
      const saved = await setRoleActions(role, taskType, next);
      setGrants((prev) => {
        const rest = prev.filter((g) => !(g.role === role && g.task_type === taskType));
        return [...rest, saved];
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan izin.");
    } finally {
      setSavingKey(null);
    }
  }

  const selectedRoleDoc = roles.find((r) => r.role_key === selectedRole);

  return (
    <div className="ep-master">
      <p className="ep-sectionlabel">Pengaturan V4 — API Mongo (bukan DEMO fixture)</p>
      <div className="ep-settings-tabs" role="tablist" aria-label="Navigasi Pengaturan">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`ep-btn${tab === t.id ? " primary" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {error ? (
        <p className="ep-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? <p className="ep-notice">{notice}</p> : null}

      {tab === "overview" ? (
        <div className="ep-placeholder">
          <h3>Ringkasan Pengaturan</h3>
          <p>
            Kelola pengguna, role modul, izin tugas, master operasional, status Accurate, dan
            audit. Permission baru default deny. Perubahan grant sensitif wajib konfirmasi.
          </p>
          <ul>
            <li>Pengguna: {users.length}</li>
            <li>Role: {roles.length}</li>
            <li>Jenis tugas Master: {taskTypes.length}</li>
            <li>Cabang: {branches.length}</li>
          </ul>
        </div>
      ) : null}

      {tab === "users" ? (
        <section>
          <p className="ep-sectionlabel">Pengguna</p>
          <form
            className="ep-settings-form"
            onSubmit={(e) => {
              e.preventDefault();
              void (async () => {
                try {
                  await createSettingsUser({
                    ...newUser,
                    operational_role: newUser.operational_role || null,
                  });
                  setNotice("Pengguna dibuat.");
                  setNewUser({
                    email: "",
                    display_name: "",
                    password: "",
                    role: "user",
                    operational_role: "",
                  });
                  await loadSettings();
                } catch (err) {
                  setError(err instanceof Error ? err.message : "Gagal membuat pengguna.");
                }
              })();
            }}
          >
            <input
              placeholder="Email"
              value={newUser.email}
              onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
              required
            />
            <input
              placeholder="Nama"
              value={newUser.display_name}
              onChange={(e) => setNewUser({ ...newUser, display_name: e.target.value })}
              required
            />
            <input
              type="password"
              placeholder="Password (≥12)"
              value={newUser.password}
              onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
              required
              minLength={12}
            />
            <select
              value={newUser.role}
              onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}
            >
              {roles.map((r) => (
                <option key={r.role_key} value={r.role_key}>
                  {r.label}
                </option>
              ))}
            </select>
            <button type="submit" className="ep-btn primary">
              Tambah pengguna
            </button>
          </form>
          <div className="ep-tblscroll">
            <table className="ep-table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Nama</th>
                  <th>Role</th>
                  <th>Ops</th>
                  <th>Aktif</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.length === 0 ? (
                  <tr>
                    <td colSpan={6}>
                      <div className="ep-empty">Tidak ada data / akses ditolak.</div>
                    </td>
                  </tr>
                ) : (
                  users.map((u) => (
                    <tr key={u.id}>
                      <td>{u.email}</td>
                      <td>{u.display_name}</td>
                      <td>{u.role}</td>
                      <td>{u.operational_role || "—"}</td>
                      <td>{u.is_active ? "Ya" : "Tidak"}</td>
                      <td>
                        <button
                          type="button"
                          className="ep-btn"
                          disabled={!u.is_active}
                          onClick={() => {
                            const reason = window.prompt("Alasan nonaktif (wajib jika ada dependensi):") || "";
                            void setUserActive(u.id, false, reason)
                              .then(() => loadSettings())
                              .catch((err: unknown) =>
                                setError(
                                  err instanceof Error ? err.message : "Gagal nonaktifkan.",
                                ),
                              );
                          }}
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
      ) : null}

      {tab === "roles" ? (
        <section>
          <p className="ep-sectionlabel">Role modul + draft permission</p>
          <form
            className="ep-settings-form"
            onSubmit={(e) => {
              e.preventDefault();
              void createSettingsRole(newRole.role_key, newRole.label)
                .then(() => {
                  setNotice("Custom role dibuat (default deny).");
                  setNewRole({ role_key: "", label: "" });
                  return loadSettings();
                })
                .catch((err: unknown) =>
                  setError(err instanceof Error ? err.message : "Gagal buat role."),
                );
            }}
          >
            <input
              placeholder="role_key"
              value={newRole.role_key}
              onChange={(e) => setNewRole({ ...newRole, role_key: e.target.value })}
              required
            />
            <input
              placeholder="Label"
              value={newRole.label}
              onChange={(e) => setNewRole({ ...newRole, label: e.target.value })}
              required
            />
            <button type="submit" className="ep-btn primary">
              Tambah custom role
            </button>
          </form>
          <label>
            Role aktif{" "}
            <select value={selectedRole} onChange={(e) => setSelectedRole(e.target.value)}>
              {roles.map((r) => (
                <option key={r.role_key} value={r.role_key}>
                  {r.label} ({r.kind}) v{r.version}
                </option>
              ))}
            </select>
          </label>
          <div className="ep-tblscroll">
            <table className="ep-table">
              <thead>
                <tr>
                  <th>Permission</th>
                  <th>Draft</th>
                </tr>
              </thead>
              <tbody>
                {modulePerms.map((p) => (
                  <tr key={p}>
                    <td>
                      {p}
                      {sensitivePerms.includes(p) ? " · sensitif" : ""}
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={draftPerms.includes(p)}
                        disabled={selectedRole === "super_admin"}
                        onChange={(e) => {
                          setDraftPerms((prev) =>
                            e.target.checked
                              ? Array.from(new Set([...prev, p]))
                              : prev.filter((x) => x !== p),
                          );
                        }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <label>
            Alasan (wajib jika grant sensitif){" "}
            <input value={permReason} onChange={(e) => setPermReason(e.target.value)} />
          </label>
          <label>
            <input
              type="checkbox"
              checked={confirmSensitive}
              onChange={(e) => setConfirmSensitive(e.target.checked)}
            />{" "}
            Konfirmasi perubahan grant sensitif
          </label>
          <button
            type="button"
            className="ep-btn primary"
            disabled={!selectedRoleDoc || selectedRole === "super_admin"}
            onClick={() => {
              if (!selectedRoleDoc) return;
              if (
                !window.confirm(
                  "Simpan draft permission ke server? Concurrent edit memakai version check.",
                )
              ) {
                return;
              }
              void saveRolePermissions(selectedRole, {
                module_permissions: draftPerms,
                expected_version: selectedRoleDoc.version,
                reason: permReason || undefined,
                confirm_sensitive: confirmSensitive,
              })
                .then(() => {
                  setNotice("Permission disimpan.");
                  return loadSettings();
                })
                .catch((err: unknown) => {
                  if (err instanceof SettingsApiError && err.status === 409) {
                    setError("Konflik versi — muat ulang data lalu simpan lagi.");
                    void loadSettings();
                  } else {
                    setError(err instanceof Error ? err.message : "Gagal simpan permission.");
                  }
                });
            }}
          >
            Simpan permission
          </button>

          <p className="ep-sectionlabel">Izin aksi operasional (Master existing)</p>
          {masterLoading ? (
            <p>Memuat…</p>
          ) : (
            <div className="ep-tblscroll">
              <table className="ep-table">
                <thead>
                  <tr>
                    <th>Role</th>
                    <th>Jenis</th>
                    {actions.map((a) => (
                      <th key={a}>{ACTION_LABEL[a] || a}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rolesOps.flatMap((role) =>
                    taskTypes.map((t) => {
                      const key = grantKey(role, t.task_type);
                      const granted = grantMap.get(key) || [];
                      if (granted.length === 0 && t.assigned_role !== role) return null;
                      return (
                        <tr key={key}>
                          <td>{ROLE_LABEL[role] || role}</td>
                          <td>{t.label}</td>
                          {actions.map((a) => (
                            <td key={a}>
                              <input
                                type="checkbox"
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
          )}
        </section>
      ) : null}

      {tab === "task-types" ? (
        <section>
          <p className="ep-sectionlabel">Jenis tugas terdaftar + manual</p>
          <form
            className="ep-settings-form"
            onSubmit={(e) => {
              e.preventDefault();
              void createManualTaskType({
                task_type: manualTask.task_type,
                label: manualTask.label,
                scope: "po",
                assigned_role: manualTask.assigned_role,
              })
                .then(() => {
                  setNotice("Jenis tugas manual dibuat (tanpa workflow handler otomatis).");
                  return loadMaster();
                })
                .catch((err: unknown) =>
                  setError(err instanceof Error ? err.message : "Gagal buat task type."),
                );
            }}
          >
            <input
              placeholder="task_type"
              value={manualTask.task_type}
              onChange={(e) => setManualTask({ ...manualTask, task_type: e.target.value })}
              required
            />
            <input
              placeholder="Label"
              value={manualTask.label}
              onChange={(e) => setManualTask({ ...manualTask, label: e.target.value })}
              required
            />
            <select
              value={manualTask.assigned_role}
              onChange={(e) =>
                setManualTask({ ...manualTask, assigned_role: e.target.value })
              }
            >
              {rolesOps.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r] || r}
                </option>
              ))}
            </select>
            <button type="submit" className="ep-btn primary">
              Tambah manual
            </button>
          </form>
          <div className="ep-tblscroll">
            <table className="ep-table">
              <thead>
                <tr>
                  <th>Jenis</th>
                  <th>Cakupan</th>
                  <th>Role penanggung jawab</th>
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
                        {rolesOps.map((r) => (
                          <option key={r} value={r}>
                            {ROLE_LABEL[r] || r}
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
        </section>
      ) : null}

      {tab === "org" ? (
        <section>
          <p className="ep-sectionlabel">Cabang</p>
          <form
            className="ep-settings-form"
            onSubmit={(e) => {
              e.preventDefault();
              void upsertBranch(newBranch.code, newBranch.name)
                .then(() => loadSettings())
                .catch((err: unknown) =>
                  setError(err instanceof Error ? err.message : "Gagal cabang."),
                );
            }}
          >
            <input
              placeholder="Kode"
              value={newBranch.code}
              onChange={(e) => setNewBranch({ ...newBranch, code: e.target.value })}
              required
            />
            <input
              placeholder="Nama"
              value={newBranch.name}
              onChange={(e) => setNewBranch({ ...newBranch, name: e.target.value })}
              required
            />
            <button type="submit" className="ep-btn primary">
              Simpan cabang
            </button>
          </form>
          <ul>
            {branches.map((b) => (
              <li key={b.code}>
                {b.code} — {b.name}
              </li>
            ))}
          </ul>
          <p className="ep-sectionlabel">Mesin</p>
          <form
            className="ep-settings-form"
            onSubmit={(e) => {
              e.preventDefault();
              void upsertMachine(newMachine)
                .then(() => loadSettings())
                .catch((err: unknown) =>
                  setError(err instanceof Error ? err.message : "Gagal mesin."),
                );
            }}
          >
            <input
              placeholder="Kode"
              value={newMachine.code}
              onChange={(e) => setNewMachine({ ...newMachine, code: e.target.value })}
              required
            />
            <input
              placeholder="Label"
              value={newMachine.label}
              onChange={(e) => setNewMachine({ ...newMachine, label: e.target.value })}
              required
            />
            <input
              placeholder="Cabang (opsional)"
              value={newMachine.branch_code}
              onChange={(e) => setNewMachine({ ...newMachine, branch_code: e.target.value })}
            />
            <button type="submit" className="ep-btn primary">
              Simpan mesin
            </button>
          </form>
          <ul>
            {machines.map((m) => (
              <li key={m.code}>
                {m.code} — {m.label} {m.branch_code ? `(${m.branch_code})` : ""}
              </li>
            ))}
          </ul>
          <p className="ep-sectionlabel">Shift</p>
          <form
            className="ep-settings-form"
            onSubmit={(e) => {
              e.preventDefault();
              void upsertShift(newShift)
                .then(() => loadSettings())
                .catch((err: unknown) =>
                  setError(err instanceof Error ? err.message : "Gagal shift."),
                );
            }}
          >
            <input
              placeholder="Kode"
              value={newShift.code}
              onChange={(e) => setNewShift({ ...newShift, code: e.target.value })}
              required
            />
            <input
              placeholder="Label"
              value={newShift.label}
              onChange={(e) => setNewShift({ ...newShift, label: e.target.value })}
              required
            />
            <input
              placeholder="Mulai HH:MM"
              value={newShift.start_time}
              onChange={(e) => setNewShift({ ...newShift, start_time: e.target.value })}
              required
            />
            <input
              placeholder="Selesai HH:MM"
              value={newShift.end_time}
              onChange={(e) => setNewShift({ ...newShift, end_time: e.target.value })}
              required
            />
            <button type="submit" className="ep-btn primary">
              Simpan shift
            </button>
          </form>
          <ul>
            {shifts.map((s) => (
              <li key={s.code}>
                {s.code} — {s.label} ({s.start_time}–{s.end_time})
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {tab === "products" ? (
        <div className="ep-placeholder">
          <h3>Master Produk — belum tersedia di baseline</h3>
          <p>
            Kontrak <code>screen_size</code> ada di PR #9 yang belum di-merge. Endpoint Settings
            mengembalikan 501 sampai merge resmi — tidak ada cherry-pick diam-diam.
          </p>
        </div>
      ) : null}

      {tab === "workflow" ? (
        <section>
          <p className="ep-sectionlabel">Kebijakan workflow (read-only)</p>
          <div className="ep-tblscroll">
            <table className="ep-table">
              <thead>
                <tr>
                  <th>Kebijakan</th>
                  <th>Status</th>
                  <th>Catatan</th>
                </tr>
              </thead>
              <tbody>
                {policies.map((p) => (
                  <tr key={p.id}>
                    <td>{p.label}</td>
                    <td>{p.status}</td>
                    <td>{p.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      {tab === "accurate" ? (
        <section>
          <p className="ep-sectionlabel">Status integrasi Accurate (non-secret)</p>
          {accurate ? (
            <ul>
              <li>Base URL: {accurate.configured.base_url ? "terkonfigurasi" : "belum"}</li>
              <li>API token: {accurate.configured.api_token ? "terkonfigurasi" : "belum"}</li>
              <li>Signature: {accurate.configured.signature ? "terkonfigurasi" : "belum"}</li>
              <li>DB ID: {accurate.configured.db_id ? "terkonfigurasi" : "belum"}</li>
              <li>
                SJ live sync:{" "}
                <strong>{accurate.sj_live_sync_enabled ? "ON" : "OFF"}</strong>
              </li>
              <li>{accurate.note}</li>
            </ul>
          ) : (
            <p className="ep-empty">Tidak dapat memuat status / akses ditolak.</p>
          )}
          <button type="button" className="ep-btn" disabled title="SJ live sync tetap OFF">
            Sync production (disabled)
          </button>
        </section>
      ) : null}

      {tab === "audit" ? (
        <section>
          <p className="ep-sectionlabel">Audit Log (append-only)</p>
          <div className="ep-tblscroll">
            <table className="ep-table">
              <thead>
                <tr>
                  <th>Waktu</th>
                  <th>Aksi</th>
                  <th>Actor</th>
                  <th>Target</th>
                </tr>
              </thead>
              <tbody>
                {audits.length === 0 ? (
                  <tr>
                    <td colSpan={4}>
                      <div className="ep-empty">Belum ada entri / akses ditolak.</div>
                    </td>
                  </tr>
                ) : (
                  audits.map((a, idx) => (
                    <tr key={`${a.action}-${idx}`}>
                      <td>{a.created_at ? String(a.created_at) : "—"}</td>
                      <td>{a.action}</td>
                      <td>{a.actor_email || "—"}</td>
                      <td>{a.target_email || "—"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
}
