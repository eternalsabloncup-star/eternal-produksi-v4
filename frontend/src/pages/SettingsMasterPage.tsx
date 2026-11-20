import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { NavIcon, type NavIconName } from "../app/icons";
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
  fetchSettingsProducts,
  fetchSettingsRoles,
  fetchSettingsUsers,
  fetchShifts,
  saveProductScreenSize,
  saveRolePermissions,
  setBranchActive,
  setUserActive,
  SettingsApiError,
  updateSettingsUser,
  upsertBranch,
  upsertMachine,
  upsertShift,
  type AuditItem,
  type SettingsBranch,
  type SettingsRole,
  type SettingsUser,
} from "../api/settings";
import { ConfirmationSuccessOverlay } from "../components/ConfirmationSuccessOverlay";
import {
  activeBranchOptions,
  formatUserBranches,
  toggleBranchId,
} from "./settingsUsersUi";

export type SettingsTabId =
  | "overview"
  | "users"
  | "roles"
  | "task-types"
  | "branches"
  | "machines"
  | "shifts"
  | "products"
  | "accurate"
  | "audit"
  | "workflow";

type SettingsNavItem = {
  id: SettingsTabId;
  label: string;
  icon: NavIconName;
  description: string;
};

type SettingsNavSection = {
  id: string;
  label: string;
  items: SettingsNavItem[];
};

type FeedbackState = {
  tone: "success" | "error";
  title: string;
  message: string;
};

/** Navigasi Settings Center — 11 submenu (Cabang/Mesin/Shift terpisah). */
export const SETTINGS_NAV_SECTIONS: SettingsNavSection[] = [
  {
    id: "access",
    label: "Umum & Akses",
    items: [
      {
        id: "overview",
        label: "Ringkasan",
        icon: "layout-dashboard",
        description: "Pintasan dan ringkasan master Pengaturan.",
      },
      {
        id: "users",
        label: "Pengguna",
        icon: "users",
        description: "Buat akun dan nonaktifkan pengguna.",
      },
      {
        id: "roles",
        label: "Role & Permission",
        icon: "shield-check",
        description: "Role modul, draft permission, dan izin aksi operasional.",
      },
    ],
  },
  {
    id: "ops",
    label: "Operasional & Master",
    items: [
      {
        id: "task-types",
        label: "Jenis Tugas",
        icon: "clipboard-list",
        description: "Jenis tugas terdaftar dan assignment role.",
      },
      {
        id: "branches",
        label: "Cabang",
        icon: "building-2",
        description: "Master kode dan nama cabang.",
      },
      {
        id: "machines",
        label: "Mesin",
        icon: "cog",
        description: "Master mesin produksi per cabang.",
      },
      {
        id: "shifts",
        label: "Shift",
        icon: "clock-3",
        description: "Master shift dan jam kerja.",
      },
      {
        id: "products",
        label: "Master Produk",
        icon: "package",
        description: "Ukuran screen pada master_products.",
      },
      {
        id: "workflow",
        label: "Kebijakan Workflow",
        icon: "workflow",
        description: "Status kebijakan workflow (read-only).",
      },
    ],
  },
  {
    id: "security",
    label: "Integrasi & Keamanan",
    items: [
      {
        id: "accurate",
        label: "Accurate Online",
        icon: "plug-zap",
        description: "Status integrasi Accurate (non-secret).",
      },
      {
        id: "audit",
        label: "Audit Log",
        icon: "history",
        description: "Entri audit append-only modul settings.",
      },
    ],
  },
];

export const SETTINGS_NAV_ITEMS: SettingsNavItem[] = SETTINGS_NAV_SECTIONS.flatMap(
  (s) => s.items,
);

export function navItemById(id: SettingsTabId): SettingsNavItem {
  return SETTINGS_NAV_ITEMS.find((x) => x.id === id) ?? SETTINGS_NAV_ITEMS[0];
}

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

export function grantKey(role: string, taskType: string): string {
  return `${role}::${taskType}`;
}

export function serializeGrants(items: RoleActionGrant[]): string {
  return JSON.stringify(
    [...items]
      .map((g) => ({
        role: g.role,
        task_type: g.task_type,
        actions: [...g.actions].sort(),
      }))
      .sort((a, b) =>
        grantKey(a.role, a.task_type).localeCompare(grantKey(b.role, b.task_type)),
      ),
  );
}

export function serializePerms(perms: string[]): string {
  return [...perms].sort().join("|");
}

export function assignmentsFromTaskTypes(
  items: MasterTaskType[],
): Record<string, OperationalRole> {
  const map: Record<string, OperationalRole> = {};
  for (const t of items) map[t.task_type] = t.assigned_role;
  return map;
}

const EMPTY_USER = {
  email: "",
  display_name: "",
  password: "",
  role: "user",
  operational_role: "",
  branch_ids: [] as string[],
};

/** Pengaturan V4 — Settings Center; draft lokal → Simpan eksplisit. */
export function SettingsMasterPage() {
  const [tab, setTab] = useState<SettingsTabId>("overview");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<FeedbackState | null>(null);

  const [taskTypes, setTaskTypes] = useState<MasterTaskType[]>([]);
  const [rolesOps, setRolesOps] = useState<OperationalRole[]>([]);
  const [actions, setActions] = useState<TaskAction[]>([]);
  const [grants, setGrants] = useState<RoleActionGrant[]>([]);
  const [draftGrants, setDraftGrants] = useState<RoleActionGrant[]>([]);
  const [draftAssignments, setDraftAssignments] = useState<
    Record<string, OperationalRole>
  >({});
  const [masterLoading, setMasterLoading] = useState(true);
  const [savingModulePerms, setSavingModulePerms] = useState(false);
  const [savingOpsGrants, setSavingOpsGrants] = useState(false);
  const [savingAssignments, setSavingAssignments] = useState(false);
  const [savingCreate, setSavingCreate] = useState(false);
  const [savingOrg, setSavingOrg] = useState(false);

  const [users, setUsers] = useState<SettingsUser[]>([]);
  const [roles, setRoles] = useState<SettingsRole[]>([]);
  const [modulePerms, setModulePerms] = useState<string[]>([]);
  const [sensitivePerms, setSensitivePerms] = useState<string[]>([]);
  const [draftPerms, setDraftPerms] = useState<string[]>([]);
  const [selectedRole, setSelectedRole] = useState<string>("");
  const [permReason, setPermReason] = useState("");
  const [confirmSensitive, setConfirmSensitive] = useState(false);
  const [branches, setBranches] = useState<SettingsBranch[]>([]);
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

  const [newUser, setNewUser] = useState(EMPTY_USER);
  const [editingUser, setEditingUser] = useState<SettingsUser | null>(null);
  const [editUserForm, setEditUserForm] = useState({
    display_name: "",
    role: "user",
    operational_role: "",
    branch_ids: [] as string[],
  });
  const [savingEditUser, setSavingEditUser] = useState(false);
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
  const [productsDirty, setProductsDirty] = useState(false);

  const activeNav = navItemById(tab);

  const showFeedback = useCallback((tone: FeedbackState["tone"], title: string, message: string) => {
    setFeedback({ tone, title, message });
  }, []);

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
      setDraftGrants(roleActions.items.map((g) => ({ ...g, actions: [...g.actions] })));
      setDraftAssignments(assignmentsFromTaskTypes(types.items));
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

  const selectedRoleDoc = roles.find((r) => r.role_key === selectedRole);

  const draftGrantMap = useMemo(() => {
    const map = new Map<string, TaskAction[]>();
    for (const g of draftGrants) map.set(grantKey(g.role, g.task_type), g.actions);
    return map;
  }, [draftGrants]);

  const modulePermsDirty =
    !!selectedRoleDoc &&
    serializePerms(draftPerms) !== serializePerms(selectedRoleDoc.module_permissions);

  const opsGrantsDirty = serializeGrants(draftGrants) !== serializeGrants(grants);

  const assignmentsDirty = useMemo(() => {
    const baseline = assignmentsFromTaskTypes(taskTypes);
    const keys = new Set([...Object.keys(baseline), ...Object.keys(draftAssignments)]);
    for (const k of keys) {
      if ((draftAssignments[k] || "") !== (baseline[k] || "")) return true;
    }
    return false;
  }, [draftAssignments, taskTypes]);

  const formDirtyUsers =
    Boolean(newUser.email.trim()) ||
    Boolean(newUser.display_name.trim()) ||
    Boolean(newUser.password) ||
    newUser.branch_ids.length > 0 ||
    editingUser != null;
  const assignableBranches = useMemo(
    () => activeBranchOptions(branches),
    [branches],
  );
  const editBranchOptions = useMemo(() => {
    const selected = new Set(
      (editUserForm.branch_ids || []).map((c) => c.toUpperCase()),
    );
    const byCode = new Map(branches.map((b) => [b.code.toUpperCase(), b]));
    const rows = [...assignableBranches];
    for (const code of selected) {
      if (!rows.some((b) => b.code.toUpperCase() === code)) {
        const existing = byCode.get(code);
        rows.push(
          existing || {
            code,
            name: "(nonaktif / tidak di master)",
            is_active: false,
          },
        );
      }
    }
    return rows;
  }, [assignableBranches, branches, editUserForm.branch_ids]);
  const formDirtyRole = Boolean(newRole.role_key.trim()) || Boolean(newRole.label.trim());
  const formDirtyManual =
    Boolean(manualTask.task_type.trim()) || Boolean(manualTask.label.trim());
  const formDirtyBranch =
    Boolean(newBranch.code.trim()) || Boolean(newBranch.name.trim());
  const formDirtyMachine =
    Boolean(newMachine.code.trim()) ||
    Boolean(newMachine.label.trim()) ||
    Boolean(newMachine.branch_code.trim());
  const formDirtyShift =
    Boolean(newShift.code.trim()) ||
    Boolean(newShift.label.trim()) ||
    newShift.start_time !== "08:00" ||
    newShift.end_time !== "16:00";

  function isTabDirty(id: SettingsTabId): boolean {
    switch (id) {
      case "users":
        return formDirtyUsers;
      case "roles":
        return modulePermsDirty || opsGrantsDirty || formDirtyRole;
      case "task-types":
        return assignmentsDirty || formDirtyManual;
      case "branches":
        return formDirtyBranch;
      case "machines":
        return formDirtyMachine;
      case "shifts":
        return formDirtyShift;
      case "products":
        return productsDirty;
      default:
        return false;
    }
  }

  function discardDraftsForTab(id: SettingsTabId) {
    switch (id) {
      case "users":
        setNewUser(EMPTY_USER);
        setEditingUser(null);
        break;
      case "roles": {
        const role = roles.find((x) => x.role_key === selectedRole);
        setDraftPerms(role ? [...role.module_permissions] : []);
        setConfirmSensitive(false);
        setPermReason("");
        setNewRole({ role_key: "", label: "" });
        setDraftGrants(grants.map((g) => ({ ...g, actions: [...g.actions] })));
        break;
      }
      case "task-types":
        setDraftAssignments(assignmentsFromTaskTypes(taskTypes));
        setManualTask({ task_type: "", label: "", assigned_role: "helper" });
        break;
      case "branches":
        setNewBranch({ code: "", name: "" });
        break;
      case "machines":
        setNewMachine({ code: "", label: "", branch_code: "" });
        break;
      case "shifts":
        setNewShift({ code: "", label: "", start_time: "08:00", end_time: "16:00" });
        break;
      case "products":
        setProductsDirty(false);
        break;
      default:
        break;
    }
  }

  function requestTab(next: SettingsTabId) {
    if (next === tab) return;
    if (isTabDirty(tab)) {
      const discard = window.confirm(
        "Ada perubahan yang belum disimpan.\n\nOK = Buang Perubahan dan pindah menu\nCancel = Tetap Mengedit",
      );
      if (!discard) return;
      discardDraftsForTab(tab);
    }
    setError(null);
    setNotice(null);
    setTab(next);
  }

  function toggleDraftAction(
    role: OperationalRole,
    taskType: string,
    action: TaskAction,
    enabled: boolean,
  ) {
    setDraftGrants((prev) => {
      const existing = prev.find((g) => g.role === role && g.task_type === taskType);
      const current = existing?.actions || [];
      const next = enabled
        ? Array.from(new Set([...current, action]))
        : current.filter((a) => a !== action);
      const rest = prev.filter((g) => !(g.role === role && g.task_type === taskType));
      return [
        ...rest,
        {
          role,
          task_type: taskType,
          actions: next,
          is_default_mapping: existing?.is_default_mapping ?? false,
        },
      ];
    });
  }

  async function saveModulePermissions() {
    if (!selectedRoleDoc || selectedRole === "super_admin" || savingModulePerms) return;
    if (!modulePermsDirty) return;
    if (
      !window.confirm(
        "Simpan draft permission ke server? Concurrent edit memakai version check.",
      )
    ) {
      return;
    }
    setSavingModulePerms(true);
    setError(null);
    setNotice(null);
    try {
      await saveRolePermissions(selectedRole, {
        module_permissions: draftPerms,
        expected_version: selectedRoleDoc.version,
        reason: permReason || undefined,
        confirm_sensitive: confirmSensitive,
      });
      showFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
      await loadSettings();
    } catch (err) {
      if (err instanceof SettingsApiError && err.status === 409) {
        const msg = "Konflik versi — muat ulang data lalu simpan lagi.";
        setError(msg);
        showFeedback("error", "Gagal", msg);
        void loadSettings();
      } else {
        const msg = err instanceof Error ? err.message : "Gagal simpan permission.";
        setError(msg);
        showFeedback("error", "Gagal", msg);
      }
    } finally {
      setSavingModulePerms(false);
    }
  }

  async function saveOperationalGrants() {
    if (!opsGrantsDirty || savingOpsGrants) return;
    setSavingOpsGrants(true);
    setError(null);
    setNotice(null);
    const baseline = new Map(
      grants.map((g) => [grantKey(g.role, g.task_type), [...g.actions].sort().join("|")]),
    );
    const changed = draftGrants.filter((g) => {
      const key = grantKey(g.role, g.task_type);
      const next = [...g.actions].sort().join("|");
      return (baseline.get(key) || "") !== next;
    });
    // Also detect removals to empty that disappeared from draft list
    for (const g of grants) {
      const key = grantKey(g.role, g.task_type);
      if (!draftGrants.some((d) => grantKey(d.role, d.task_type) === key)) {
        changed.push({
          role: g.role,
          task_type: g.task_type,
          actions: [],
          is_default_mapping: g.is_default_mapping,
        });
      }
    }
    const unique = new Map<string, RoleActionGrant>();
    for (const g of changed) unique.set(grantKey(g.role, g.task_type), g);

    let ok = 0;
    let fail = 0;
    let lastErr = "";
    const nextServer = [...grants];
    const failedKeys = new Set<string>();

    for (const g of unique.values()) {
      const key = grantKey(g.role, g.task_type);
      try {
        const saved = await setRoleActions(
          g.role as OperationalRole,
          g.task_type,
          g.actions,
        );
        const idx = nextServer.findIndex(
          (x) => x.role === saved.role && x.task_type === saved.task_type,
        );
        if (idx >= 0) nextServer[idx] = saved;
        else nextServer.push(saved);
        ok += 1;
      } catch (err) {
        fail += 1;
        failedKeys.add(key);
        lastErr = err instanceof Error ? err.message : "Gagal menyimpan izin.";
      }
    }

    setGrants(nextServer);
    // Draft: sukses mengikuti server; gagal pertahankan nilai draft sebelumnya.
    setDraftGrants((prev) => {
      const byKey = new Map(prev.map((g) => [grantKey(g.role, g.task_type), g]));
      for (const g of nextServer) {
        const key = grantKey(g.role, g.task_type);
        if (!failedKeys.has(key)) {
          byKey.set(key, { ...g, actions: [...g.actions] });
        }
      }
      return Array.from(byKey.values());
    });

    if (fail === 0) {
      showFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
    } else if (ok === 0) {
      setError(lastErr);
      showFeedback("error", "Gagal", lastErr || "Gagal menyimpan izin operasional.");
    } else {
      const msg = `${ok} tersimpan, ${fail} gagal (bukan atomik). ${lastErr}`;
      setError(msg);
      showFeedback("error", "Sebagian gagal", msg);
    }
    setSavingOpsGrants(false);
  }

  async function saveTaskAssignments() {
    if (!assignmentsDirty || savingAssignments) return;
    setSavingAssignments(true);
    setError(null);
    setNotice(null);
    const baseline = assignmentsFromTaskTypes(taskTypes);
    const dirtyTypes = Object.keys(draftAssignments).filter(
      (k) => draftAssignments[k] !== baseline[k],
    );
    let ok = 0;
    let fail = 0;
    let lastErr = "";
    let nextTypes = [...taskTypes];
    const failedTypes = new Set<string>();

    for (const taskType of dirtyTypes) {
      const role = draftAssignments[taskType];
      if (!role) continue;
      try {
        const updated = await assignTaskTypeRole(taskType, role);
        nextTypes = nextTypes.map((t) =>
          t.task_type === taskType ? { ...t, ...updated } : t,
        );
        ok += 1;
      } catch (err) {
        fail += 1;
        failedTypes.add(taskType);
        lastErr = err instanceof Error ? err.message : "Gagal menyimpan.";
      }
    }

    setTaskTypes(nextTypes);
    setDraftAssignments((prev) => {
      const next = assignmentsFromTaskTypes(nextTypes);
      for (const t of failedTypes) {
        if (prev[t]) next[t] = prev[t];
      }
      return next;
    });
    if (ok > 0) {
      try {
        const refreshed = await fetchRoleActions();
        setGrants(refreshed.items);
        if (!opsGrantsDirty) {
          setDraftGrants(refreshed.items.map((g) => ({ ...g, actions: [...g.actions] })));
        }
      } catch {
        // keep local grants
      }
    }

    if (fail === 0) {
      showFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
    } else if (ok === 0) {
      setError(lastErr);
      showFeedback("error", "Gagal", lastErr || "Gagal menyimpan assignment.");
    } else {
      const msg = `${ok} tersimpan, ${fail} gagal (bukan atomik). ${lastErr}`;
      setError(msg);
      showFeedback("error", "Sebagian gagal", msg);
    }
    setSavingAssignments(false);
  }

  return (
    <div className="ep-settings">
      <p className="ep-settings-eyebrow">Pengaturan V4 — API Mongo (bukan DEMO fixture)</p>
      <div className="ep-settings-layout">
        <aside className="ep-settings-nav" aria-label="Navigasi Pengaturan">
          <label className="ep-settings-nav-mobile">
            <span className="ep-settings-nav-mobile-label">Menu Pengaturan</span>
            <select
              className="ep-settings-nav-select"
              value={tab}
              aria-label="Pilih submenu Pengaturan"
              onChange={(e) => requestTab(e.target.value as SettingsTabId)}
            >
              {SETTINGS_NAV_SECTIONS.map((section) => (
                <optgroup key={section.id} label={section.label}>
                  {section.items.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                      {isTabDirty(item.id) && item.id === tab ? " · belum disimpan" : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <nav className="ep-settings-nav-desktop" aria-label="Submenu Pengaturan">
            {SETTINGS_NAV_SECTIONS.map((section) => (
              <div key={section.id} className="ep-settings-nav-group">
                <p className="ep-settings-nav-group-label">{section.label}</p>
                <ul className="ep-settings-nav-list">
                  {section.items.map((item) => {
                    const active = tab === item.id;
                    const dirty = active && isTabDirty(item.id);
                    return (
                      <li key={item.id}>
                        <button
                          type="button"
                          className={`ep-settings-nav-item${active ? " is-active" : ""}${dirty ? " is-dirty" : ""}`}
                          aria-current={active ? "page" : undefined}
                          onClick={() => requestTab(item.id)}
                        >
                          <NavIcon name={item.icon} size={16} className="ep-settings-nav-ico" />
                          <span>{item.label}</span>
                          {dirty ? (
                            <span className="ep-settings-dirty-dot" title="Belum disimpan" />
                          ) : null}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>
        </aside>

        <div className="ep-settings-main">
          <header className="ep-settings-header">
            <span className="ep-settings-header-ico" aria-hidden>
              <NavIcon name={activeNav.icon} size={18} />
            </span>
            <div className="ep-settings-header-text">
              <h2 className="ep-settings-title">{activeNav.label}</h2>
              <p className="ep-settings-desc">{activeNav.description}</p>
            </div>
            {isTabDirty(tab) ? (
              <span className="ep-settings-dirty-badge">Belum disimpan</span>
            ) : null}
          </header>

          {error ? (
            <p className="ep-settings-alert" role="alert">
              {error}
            </p>
          ) : null}
          {notice ? <p className="ep-settings-notice">{notice}</p> : null}

          <div className="ep-settings-body">
            {tab === "overview" ? (
              <>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Ringkasan data</h3>
                  <p className="ep-settings-card-note">
                    Permission baru default deny. Perubahan grant sensitif wajib konfirmasi.
                    Perubahan editable hanya tersimpan setelah tombol Simpan.
                  </p>
                  <div className="ep-settings-table-wrap">
                    <table className="ep-settings-data-table is-compact">
                      <thead>
                        <tr>
                          <th>Informasi</th>
                          <th className="is-status">Jumlah</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td>Pengguna</td>
                          <td className="is-status">{users.length}</td>
                        </tr>
                        <tr>
                          <td>Role</td>
                          <td className="is-status">{roles.length}</td>
                        </tr>
                        <tr>
                          <td>Jenis tugas Master</td>
                          <td className="is-status">{taskTypes.length}</td>
                        </tr>
                        <tr>
                          <td>Cabang</td>
                          <td className="is-status">{branches.length}</td>
                        </tr>
                        <tr>
                          <td>Mesin</td>
                          <td className="is-status">{machines.length}</td>
                        </tr>
                        <tr>
                          <td>Shift</td>
                          <td className="is-status">{shifts.length}</td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </section>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Pintasan submenu</h3>
                  <div className="ep-settings-shortcuts">
                    {SETTINGS_NAV_ITEMS.filter((item) => item.id !== "overview").map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className="ep-settings-shortcut"
                        onClick={() => requestTab(item.id)}
                      >
                        <NavIcon name={item.icon} size={15} />
                        <span>{item.label}</span>
                      </button>
                    ))}
                  </div>
                </section>
              </>
            ) : null}

            {tab === "users" ? (
              <>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Tambah pengguna</h3>
                  <form
                    className="ep-settings-form-grid"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (savingCreate) return;
                      void (async () => {
                        setSavingCreate(true);
                        setError(null);
                        try {
                          await createSettingsUser({
                            ...newUser,
                            operational_role: newUser.operational_role || null,
                            branch_ids: newUser.branch_ids,
                          });
                          setNewUser(EMPTY_USER);
                          showFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
                          await loadSettings();
                        } catch (err) {
                          const msg =
                            err instanceof Error ? err.message : "Gagal membuat pengguna.";
                          setError(msg);
                          showFeedback("error", "Gagal", msg);
                        } finally {
                          setSavingCreate(false);
                        }
                      })();
                    }}
                  >
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Email</span>
                      <input
                        value={newUser.email}
                        onChange={(e) => setNewUser({ ...newUser, email: e.target.value })}
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Nama</span>
                      <input
                        value={newUser.display_name}
                        onChange={(e) =>
                          setNewUser({ ...newUser, display_name: e.target.value })
                        }
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Password (≥12)</span>
                      <input
                        type="password"
                        value={newUser.password}
                        onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
                        required
                        minLength={12}
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Role</span>
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
                    </label>
                    <fieldset className="ep-settings-field ep-settings-field-span">
                      <legend className="ep-settings-field-label">Cabang</legend>
                      {assignableBranches.length === 0 ? (
                        <p className="ep-settings-hint">
                          Belum ada cabang aktif di Master Cabang. Tambahkan cabang
                          terlebih dahulu.
                        </p>
                      ) : (
                        <div className="ep-settings-check-grid">
                          {assignableBranches.map((b) => {
                            const checked = newUser.branch_ids.includes(b.code);
                            return (
                              <label key={b.code} className="ep-settings-check">
                                <input
                                  type="checkbox"
                                  checked={checked}
                                  onChange={(e) =>
                                    setNewUser({
                                      ...newUser,
                                      branch_ids: toggleBranchId(
                                        newUser.branch_ids,
                                        b.code,
                                        e.target.checked,
                                      ),
                                    })
                                  }
                                />
                                <span>
                                  {b.code} — {b.name}
                                </span>
                              </label>
                            );
                          })}
                        </div>
                      )}
                    </fieldset>
                    <div className="ep-settings-actions">
                      <button
                        type="submit"
                        className={`ep-btn primary${savingCreate ? " is-busy" : ""}`}
                        disabled={savingCreate}
                      >
                        {savingCreate ? "Menyimpan…" : "Tambah pengguna"}
                      </button>
                    </div>
                  </form>
                </section>

                {editingUser ? (
                  <section className="ep-settings-card">
                    <h3 className="ep-settings-card-title">
                      Edit pengguna — {editingUser.email}
                    </h3>
                    <form
                      className="ep-settings-form-grid"
                      onSubmit={(e) => {
                        e.preventDefault();
                        if (savingEditUser || !editingUser) return;
                        void (async () => {
                          setSavingEditUser(true);
                          setError(null);
                          try {
                            await updateSettingsUser(editingUser.id, {
                              display_name: editUserForm.display_name,
                              role: editUserForm.role,
                              operational_role: editUserForm.operational_role || null,
                              branch_ids: editUserForm.branch_ids,
                              reason: "edit-pengguna-ui",
                            });
                            setEditingUser(null);
                            showFeedback(
                              "success",
                              "Berhasil",
                              "Pengaturan berhasil disimpan.",
                            );
                            await loadSettings();
                          } catch (err) {
                            const msg =
                              err instanceof Error
                                ? err.message
                                : "Gagal menyimpan pengguna.";
                            setError(msg);
                            showFeedback("error", "Gagal", msg);
                          } finally {
                            setSavingEditUser(false);
                          }
                        })();
                      }}
                    >
                      <label className="ep-settings-field">
                        <span className="ep-settings-field-label">Nama</span>
                        <input
                          value={editUserForm.display_name}
                          onChange={(e) =>
                            setEditUserForm({
                              ...editUserForm,
                              display_name: e.target.value,
                            })
                          }
                          required
                        />
                      </label>
                      <label className="ep-settings-field">
                        <span className="ep-settings-field-label">Role</span>
                        <select
                          value={editUserForm.role}
                          onChange={(e) =>
                            setEditUserForm({ ...editUserForm, role: e.target.value })
                          }
                          disabled={editingUser.role === "super_admin"}
                        >
                          {roles.map((r) => (
                            <option key={r.role_key} value={r.role_key}>
                              {r.label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <fieldset className="ep-settings-field ep-settings-field-span">
                        <legend className="ep-settings-field-label">Cabang</legend>
                        {editBranchOptions.length === 0 ? (
                          <p className="ep-settings-hint">
                            Belum ada cabang aktif di Master Cabang.
                          </p>
                        ) : (
                          <div className="ep-settings-check-grid">
                            {editBranchOptions.map((b) => {
                              const checked = editUserForm.branch_ids
                                .map((x) => x.toUpperCase())
                                .includes(b.code.toUpperCase());
                              const canAssign = b.is_active;
                              return (
                                <label key={b.code} className="ep-settings-check">
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    disabled={!canAssign && !checked}
                                    onChange={(e) => {
                                      if (!canAssign && e.target.checked) return;
                                      setEditUserForm({
                                        ...editUserForm,
                                        branch_ids: toggleBranchId(
                                          editUserForm.branch_ids,
                                          b.code,
                                          e.target.checked,
                                        ),
                                      });
                                    }}
                                  />
                                  <span>
                                    {b.code} — {b.name}
                                    {!b.is_active ? " (nonaktif)" : ""}
                                  </span>
                                </label>
                              );
                            })}
                          </div>
                        )}
                      </fieldset>
                      <div className="ep-settings-actions">
                        <button
                          type="button"
                          className="ep-btn"
                          disabled={savingEditUser}
                          onClick={() => setEditingUser(null)}
                        >
                          Batal
                        </button>
                        <button
                          type="submit"
                          className={`ep-btn primary${savingEditUser ? " is-busy" : ""}`}
                          disabled={savingEditUser}
                        >
                          {savingEditUser ? "Menyimpan…" : "Simpan perubahan"}
                        </button>
                      </div>
                    </form>
                  </section>
                ) : null}

                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Daftar pengguna</h3>
                  <div className="ep-settings-table-wrap">
                    <table className="ep-settings-data-table">
                      <thead>
                        <tr>
                          <th>Email</th>
                          <th>Nama</th>
                          <th className="is-role">Role</th>
                          <th className="is-role">Ops</th>
                          <th>Cabang</th>
                          <th className="is-status">Aktif</th>
                          <th className="is-actions">Aksi</th>
                        </tr>
                      </thead>
                      <tbody>
                        {users.length === 0 ? (
                          <tr>
                            <td colSpan={7}>
                              <div className="ep-settings-empty">
                                Tidak ada data / akses ditolak.
                              </div>
                            </td>
                          </tr>
                        ) : (
                          users.map((u) => (
                            <tr key={u.id}>
                              <td title={u.email}>{u.email}</td>
                              <td>{u.display_name}</td>
                              <td className="is-role">{u.role}</td>
                              <td className="is-role">{u.operational_role || "—"}</td>
                              <td title={formatUserBranches(u.branch_ids, branches)}>
                                {formatUserBranches(u.branch_ids, branches)}
                              </td>
                              <td className="is-status">
                                <span
                                  className={`ep-settings-pill${u.is_active ? "" : " is-off"}`}
                                >
                                  {u.is_active ? "Ya" : "Tidak"}
                                </span>
                              </td>
                              <td className="is-actions">
                                <button
                                  type="button"
                                  className="ep-btn"
                                  onClick={() => {
                                    setEditingUser(u);
                                    setEditUserForm({
                                      display_name: u.display_name,
                                      role: u.role,
                                      operational_role: u.operational_role || "",
                                      branch_ids: [...(u.branch_ids || [])],
                                    });
                                  }}
                                >
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  className="ep-btn"
                                  disabled={!u.is_active}
                                  onClick={() => {
                                    const reason =
                                      window.prompt(
                                        "Alasan nonaktif (wajib jika ada dependensi):",
                                      ) || "";
                                    void setUserActive(u.id, false, reason)
                                      .then(async () => {
                                        showFeedback(
                                          "success",
                                          "Berhasil",
                                          "Pengaturan berhasil disimpan.",
                                        );
                                        await loadSettings();
                                      })
                                      .catch((err: unknown) => {
                                        const msg =
                                          err instanceof Error
                                            ? err.message
                                            : "Gagal nonaktifkan.";
                                        setError(msg);
                                        showFeedback("error", "Gagal", msg);
                                      });
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
              </>
            ) : null}

            {tab === "roles" ? (
              <>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Role modul</h3>
                  <form
                    className="ep-settings-form-grid"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (savingCreate) return;
                      setSavingCreate(true);
                      void createSettingsRole(newRole.role_key, newRole.label)
                        .then(async () => {
                          setNewRole({ role_key: "", label: "" });
                          showFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
                          await loadSettings();
                        })
                        .catch((err: unknown) => {
                          const msg =
                            err instanceof Error ? err.message : "Gagal buat role.";
                          setError(msg);
                          showFeedback("error", "Gagal", msg);
                        })
                        .finally(() => setSavingCreate(false));
                    }}
                  >
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">role_key</span>
                      <input
                        value={newRole.role_key}
                        onChange={(e) => setNewRole({ ...newRole, role_key: e.target.value })}
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Label</span>
                      <input
                        value={newRole.label}
                        onChange={(e) => setNewRole({ ...newRole, label: e.target.value })}
                        required
                      />
                    </label>
                    <div className="ep-settings-field is-actions">
                      <span className="ep-settings-field-label">&nbsp;</span>
                      <button
                        type="submit"
                        className={`ep-btn primary${savingCreate ? " is-busy" : ""}`}
                        disabled={savingCreate}
                      >
                        {savingCreate ? "Menyimpan…" : "Tambah custom role"}
                      </button>
                    </div>
                  </form>

                  <label className="ep-settings-field" style={{ maxWidth: 360, marginBottom: 12 }}>
                    <span className="ep-settings-field-label">Role aktif</span>
                    <select
                      value={selectedRole}
                      onChange={(e) => {
                        if (modulePermsDirty) {
                          const discard = window.confirm(
                            "Draft permission role ini belum disimpan.\n\nOK = Buang draft dan ganti role\nCancel = Tetap Mengedit",
                          );
                          if (!discard) return;
                        }
                        setSelectedRole(e.target.value);
                      }}
                    >
                      {roles.map((r) => (
                        <option key={r.role_key} value={r.role_key}>
                          {r.label} ({r.kind}) v{r.version}
                        </option>
                      ))}
                    </select>
                  </label>

                  <div className="ep-settings-table-wrap">
                    <table className="ep-settings-data-table is-compact">
                      <thead>
                        <tr>
                          <th>Permission</th>
                          <th className="is-check">Draft</th>
                        </tr>
                      </thead>
                      <tbody>
                        {modulePerms.length === 0 ? (
                          <tr>
                            <td colSpan={2}>
                              <div className="ep-settings-empty">Tidak ada permission katalog.</div>
                            </td>
                          </tr>
                        ) : (
                          modulePerms.map((p) => (
                            <tr key={p}>
                              <td>
                                {p}
                                {sensitivePerms.includes(p) ? (
                                  <span className="ep-settings-pill is-warn" style={{ marginLeft: 8 }}>
                                    sensitif
                                  </span>
                                ) : null}
                              </td>
                              <td className="is-check">
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
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>

                  <div className="ep-settings-form-grid" style={{ marginTop: 12 }}>
                    <label className="ep-settings-field" style={{ gridColumn: "1 / -1" }}>
                      <span className="ep-settings-field-label">
                        Alasan (wajib jika grant sensitif)
                      </span>
                      <input
                        value={permReason}
                        onChange={(e) => setPermReason(e.target.value)}
                      />
                    </label>
                    <label className="ep-settings-check-row">
                      <input
                        type="checkbox"
                        checked={confirmSensitive}
                        onChange={(e) => setConfirmSensitive(e.target.checked)}
                      />
                      Konfirmasi perubahan grant sensitif
                    </label>
                  </div>

                  <div className="ep-settings-actions">
                    {modulePermsDirty ? (
                      <span className="ep-settings-meta">Ada draft belum disimpan</span>
                    ) : (
                      <span className="ep-settings-meta">Tidak ada perubahan</span>
                    )}
                    <button
                      type="button"
                      className={`ep-btn primary${savingModulePerms ? " is-busy" : ""}`}
                      disabled={
                        !selectedRoleDoc ||
                        selectedRole === "super_admin" ||
                        !modulePermsDirty ||
                        savingModulePerms
                      }
                      onClick={() => void saveModulePermissions()}
                    >
                      {savingModulePerms ? "Menyimpan…" : "Simpan permission"}
                    </button>
                  </div>
                </section>

                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Izin aksi operasional</h3>
                  <p className="ep-settings-card-note">
                    Checklist hanya mengubah draft. Penyimpanan per baris role×jenis tugas (bukan
                    atomik).
                  </p>
                  {masterLoading ? (
                    <p className="ep-settings-empty">Memuat…</p>
                  ) : (
                    <>
                      <div className="ep-settings-table-wrap">
                        <table className="ep-settings-data-table is-wide">
                          <thead>
                            <tr>
                              <th className="is-role">Role</th>
                              <th className="is-type">Jenis tugas</th>
                              {actions.map((a) => (
                                <th key={a} className="is-check">
                                  {ACTION_LABEL[a] || a}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {rolesOps.flatMap((role) =>
                              taskTypes.map((t) => {
                                const key = grantKey(role, t.task_type);
                                const granted = draftGrantMap.get(key) || [];
                                if (granted.length === 0 && t.assigned_role !== role) {
                                  return null;
                                }
                                return (
                                  <tr key={key}>
                                    <td className="is-role">{ROLE_LABEL[role] || role}</td>
                                    <td className="is-type" title={t.label}>
                                      {t.label}
                                    </td>
                                    {actions.map((a) => (
                                      <td key={a} className="is-check">
                                        <input
                                          type="checkbox"
                                          checked={granted.includes(a)}
                                          disabled={savingOpsGrants}
                                          onChange={(e) =>
                                            toggleDraftAction(
                                              role,
                                              t.task_type,
                                              a,
                                              e.target.checked,
                                            )
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
                      <div className="ep-settings-actions">
                        {opsGrantsDirty ? (
                          <span className="ep-settings-meta">Ada draft belum disimpan</span>
                        ) : (
                          <span className="ep-settings-meta">Tidak ada perubahan</span>
                        )}
                        <button
                          type="button"
                          className={`ep-btn primary${savingOpsGrants ? " is-busy" : ""}`}
                          disabled={!opsGrantsDirty || savingOpsGrants}
                          onClick={() => void saveOperationalGrants()}
                        >
                          {savingOpsGrants ? "Menyimpan…" : "Simpan izin operasional"}
                        </button>
                      </div>
                    </>
                  )}
                </section>
              </>
            ) : null}

            {tab === "task-types" ? (
              <>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Tambah jenis tugas manual</h3>
                  <form
                    className="ep-settings-form-grid"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (savingCreate) return;
                      setSavingCreate(true);
                      void createManualTaskType({
                        task_type: manualTask.task_type,
                        label: manualTask.label,
                        scope: "po",
                        assigned_role: manualTask.assigned_role,
                      })
                        .then(async () => {
                          setManualTask({
                            task_type: "",
                            label: "",
                            assigned_role: "helper",
                          });
                          showFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
                          await loadMaster();
                        })
                        .catch((err: unknown) => {
                          const msg =
                            err instanceof Error ? err.message : "Gagal buat task type.";
                          setError(msg);
                          showFeedback("error", "Gagal", msg);
                        })
                        .finally(() => setSavingCreate(false));
                    }}
                  >
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">task_type</span>
                      <input
                        value={manualTask.task_type}
                        onChange={(e) =>
                          setManualTask({ ...manualTask, task_type: e.target.value })
                        }
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Label</span>
                      <input
                        value={manualTask.label}
                        onChange={(e) =>
                          setManualTask({ ...manualTask, label: e.target.value })
                        }
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Role penanggung jawab</span>
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
                    </label>
                    <div className="ep-settings-actions">
                      <button
                        type="submit"
                        className={`ep-btn primary${savingCreate ? " is-busy" : ""}`}
                        disabled={savingCreate}
                      >
                        {savingCreate ? "Menyimpan…" : "Tambah manual"}
                      </button>
                    </div>
                  </form>
                </section>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Jenis tugas terdaftar</h3>
                  <div className="ep-settings-table-wrap">
                    <table className="ep-settings-data-table">
                      <thead>
                        <tr>
                          <th>Jenis</th>
                          <th className="is-status">Cakupan</th>
                          <th style={{ width: 180 }}>Role penanggung jawab</th>
                          <th className="is-role">Pemetaan</th>
                        </tr>
                      </thead>
                      <tbody>
                        {taskTypes.length === 0 ? (
                          <tr>
                            <td colSpan={4}>
                              <div className="ep-settings-empty">Belum ada jenis tugas.</div>
                            </td>
                          </tr>
                        ) : (
                          taskTypes.map((t) => (
                            <tr key={t.task_type}>
                              <td>
                                {t.label}
                                {t.coordination ? " · koordinasi" : ""}
                              </td>
                              <td className="is-status">{t.scope}</td>
                              <td>
                                <select
                                  className="ep-settings-inline-select"
                                  value={draftAssignments[t.task_type] || t.assigned_role}
                                  disabled={savingAssignments}
                                  onChange={(e) =>
                                    setDraftAssignments((prev) => ({
                                      ...prev,
                                      [t.task_type]: e.target.value as OperationalRole,
                                    }))
                                  }
                                >
                                  {rolesOps.map((r) => (
                                    <option key={r} value={r}>
                                      {ROLE_LABEL[r] || r}
                                    </option>
                                  ))}
                                </select>
                              </td>
                              <td className="is-role">
                                {t.is_default_mapping ? "Default Owner" : "Dikonfigurasi"}
                              </td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                  <div className="ep-settings-actions">
                    {assignmentsDirty ? (
                      <span className="ep-settings-meta">Ada draft belum disimpan</span>
                    ) : (
                      <span className="ep-settings-meta">Tidak ada perubahan</span>
                    )}
                    <button
                      type="button"
                      className={`ep-btn primary${savingAssignments ? " is-busy" : ""}`}
                      disabled={!assignmentsDirty || savingAssignments}
                      onClick={() => void saveTaskAssignments()}
                    >
                      {savingAssignments ? "Menyimpan…" : "Simpan assignment role"}
                    </button>
                  </div>
                </section>
              </>
            ) : null}

            {tab === "branches" ? (
              <>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Simpan cabang</h3>
                  <form
                    className="ep-settings-form-grid"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (savingOrg) return;
                      setSavingOrg(true);
                      void upsertBranch(newBranch.code, newBranch.name, true)
                        .then(async () => {
                          setNewBranch({ code: "", name: "" });
                          showFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
                          await loadSettings();
                        })
                        .catch((err: unknown) => {
                          const msg =
                            err instanceof Error ? err.message : "Gagal cabang.";
                          setError(msg);
                          showFeedback("error", "Gagal", msg);
                        })
                        .finally(() => setSavingOrg(false));
                    }}
                  >
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Kode</span>
                      <input
                        value={newBranch.code}
                        onChange={(e) => setNewBranch({ ...newBranch, code: e.target.value })}
                        required
                        pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,15}"
                        title="A-Z / 0-9 / _ / - (max 16), tanpa spasi"
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
                  <h3 className="ep-settings-card-title">Daftar cabang</h3>
                  <div className="ep-settings-table-wrap">
                    <table className="ep-settings-data-table is-compact">
                      <thead>
                        <tr>
                          <th style={{ width: 140 }}>Kode</th>
                          <th>Nama</th>
                          <th className="is-status">Aktif</th>
                          <th className="is-actions">Aksi</th>
                        </tr>
                      </thead>
                      <tbody>
                        {branches.length === 0 ? (
                          <tr>
                            <td colSpan={4}>
                              <div className="ep-settings-empty">Belum ada cabang / akses ditolak.</div>
                            </td>
                          </tr>
                        ) : (
                          branches.map((b) => (
                            <tr key={b.code}>
                              <td>{b.code}</td>
                              <td>{b.name}</td>
                              <td className="is-status">
                                <span
                                  className={`ep-settings-pill${b.is_active ? "" : " is-off"}`}
                                >
                                  {b.is_active ? "Ya" : "Tidak"}
                                </span>
                              </td>
                              <td className="is-actions">
                                <button
                                  type="button"
                                  className="ep-btn"
                                  disabled={savingOrg}
                                  onClick={() => {
                                    const next = !b.is_active;
                                    const reason = next
                                      ? ""
                                      : window.prompt(
                                          "Alasan nonaktif (wajib jika masih dipakai pengguna):",
                                        ) || "";
                                    setSavingOrg(true);
                                    void setBranchActive(b.code, next, reason || undefined)
                                      .then(async () => {
                                        showFeedback(
                                          "success",
                                          "Berhasil",
                                          "Pengaturan berhasil disimpan.",
                                        );
                                        await loadSettings();
                                      })
                                      .catch((err: unknown) => {
                                        const msg =
                                          err instanceof Error
                                            ? err.message
                                            : "Gagal ubah status cabang.";
                                        setError(msg);
                                        showFeedback("error", "Gagal", msg);
                                      })
                                      .finally(() => setSavingOrg(false));
                                  }}
                                >
                                  {b.is_active ? "Nonaktifkan" : "Aktifkan"}
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
            ) : null}

            {tab === "machines" ? (
              <>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Simpan mesin</h3>
                  <form
                    className="ep-settings-form-grid"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (savingOrg) return;
                      setSavingOrg(true);
                      void upsertMachine(newMachine)
                        .then(async () => {
                          setNewMachine({ code: "", label: "", branch_code: "" });
                          showFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
                          await loadSettings();
                        })
                        .catch((err: unknown) => {
                          const msg =
                            err instanceof Error ? err.message : "Gagal mesin.";
                          setError(msg);
                          showFeedback("error", "Gagal", msg);
                        })
                        .finally(() => setSavingOrg(false));
                    }}
                  >
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Kode</span>
                      <input
                        value={newMachine.code}
                        onChange={(e) => setNewMachine({ ...newMachine, code: e.target.value })}
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Label</span>
                      <input
                        value={newMachine.label}
                        onChange={(e) =>
                          setNewMachine({ ...newMachine, label: e.target.value })
                        }
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Cabang (opsional)</span>
                      <input
                        value={newMachine.branch_code}
                        onChange={(e) =>
                          setNewMachine({ ...newMachine, branch_code: e.target.value })
                        }
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
                  <div className="ep-settings-table-wrap">
                    <table className="ep-settings-data-table is-compact">
                      <thead>
                        <tr>
                          <th style={{ width: 120 }}>Kode</th>
                          <th>Label</th>
                          <th style={{ width: 140 }}>Cabang</th>
                        </tr>
                      </thead>
                      <tbody>
                        {machines.length === 0 ? (
                          <tr>
                            <td colSpan={3}>
                              <div className="ep-settings-empty">Belum ada mesin / akses ditolak.</div>
                            </td>
                          </tr>
                        ) : (
                          machines.map((m) => (
                            <tr key={m.code}>
                              <td>{m.code}</td>
                              <td>{m.label}</td>
                              <td>{m.branch_code || "—"}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>
                </section>
              </>
            ) : null}

            {tab === "shifts" ? (
              <>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Simpan shift</h3>
                  <form
                    className="ep-settings-form-grid"
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (savingOrg) return;
                      setSavingOrg(true);
                      void upsertShift(newShift)
                        .then(async () => {
                          setNewShift({
                            code: "",
                            label: "",
                            start_time: "08:00",
                            end_time: "16:00",
                          });
                          showFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
                          await loadSettings();
                        })
                        .catch((err: unknown) => {
                          const msg =
                            err instanceof Error ? err.message : "Gagal shift.";
                          setError(msg);
                          showFeedback("error", "Gagal", msg);
                        })
                        .finally(() => setSavingOrg(false));
                    }}
                  >
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Kode</span>
                      <input
                        value={newShift.code}
                        onChange={(e) => setNewShift({ ...newShift, code: e.target.value })}
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Label</span>
                      <input
                        value={newShift.label}
                        onChange={(e) => setNewShift({ ...newShift, label: e.target.value })}
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Mulai (HH:MM)</span>
                      <input
                        value={newShift.start_time}
                        onChange={(e) =>
                          setNewShift({ ...newShift, start_time: e.target.value })
                        }
                        required
                      />
                    </label>
                    <label className="ep-settings-field">
                      <span className="ep-settings-field-label">Selesai (HH:MM)</span>
                      <input
                        value={newShift.end_time}
                        onChange={(e) =>
                          setNewShift({ ...newShift, end_time: e.target.value })
                        }
                        required
                      />
                    </label>
                    <div className="ep-settings-actions">
                      <button
                        type="submit"
                        className={`ep-btn primary${savingOrg ? " is-busy" : ""}`}
                        disabled={savingOrg}
                      >
                        {savingOrg ? "Menyimpan…" : "Simpan shift"}
                      </button>
                    </div>
                  </form>
                </section>
                <section className="ep-settings-card">
                  <h3 className="ep-settings-card-title">Daftar shift</h3>
                  <div className="ep-settings-table-wrap">
                    <table className="ep-settings-data-table is-compact">
                      <thead>
                        <tr>
                          <th style={{ width: 120 }}>Kode</th>
                          <th>Label</th>
                          <th className="is-status">Mulai</th>
                          <th className="is-status">Selesai</th>
                        </tr>
                      </thead>
                      <tbody>
                        {shifts.length === 0 ? (
                          <tr>
                            <td colSpan={4}>
                              <div className="ep-settings-empty">Belum ada shift / akses ditolak.</div>
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
            ) : null}

            {tab === "products" ? (
              <ProductsSettingsPanel
                onError={setError}
                onDirtyChange={setProductsDirty}
                onFeedback={showFeedback}
              />
            ) : null}

            {tab === "workflow" ? (
              <section className="ep-settings-card">
                <h3 className="ep-settings-card-title">Kebijakan workflow</h3>
                <p className="ep-settings-card-note">Read-only — tidak ada editor status bebas.</p>
                <div className="ep-settings-table-wrap">
                  <table className="ep-settings-data-table">
                    <thead>
                      <tr>
                        <th>Kebijakan</th>
                        <th className="is-status">Status</th>
                        <th>Catatan</th>
                      </tr>
                    </thead>
                    <tbody>
                      {policies.length === 0 ? (
                        <tr>
                          <td colSpan={3}>
                            <div className="ep-settings-empty">Tidak ada kebijakan.</div>
                          </td>
                        </tr>
                      ) : (
                        policies.map((p) => (
                          <tr key={p.id}>
                            <td>{p.label}</td>
                            <td className="is-status">
                              <span
                                className={`ep-settings-pill${p.status === "unavailable" ? " is-off" : ""}`}
                              >
                                {p.status}
                              </span>
                            </td>
                            <td>{p.note}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </section>
            ) : null}

            {tab === "accurate" ? (
              <section className="ep-settings-card">
                <h3 className="ep-settings-card-title">Status Accurate Online</h3>
                <p className="ep-settings-card-note">
                  Status non-secret saja. Secret tidak ditampilkan.
                </p>
                <div className="ep-settings-table-wrap">
                  <table className="ep-settings-data-table is-compact">
                    <thead>
                      <tr>
                        <th>Parameter</th>
                        <th className="is-status">Status</th>
                        <th>Keterangan</th>
                      </tr>
                    </thead>
                    <tbody>
                      {!accurate ? (
                        <tr>
                          <td colSpan={3}>
                            <div className="ep-settings-empty">
                              Tidak dapat memuat status / akses ditolak.
                            </div>
                          </td>
                        </tr>
                      ) : (
                        <>
                          <tr>
                            <td>Base URL</td>
                            <td className="is-status">
                              {accurate.configured.base_url ? "Terkonfigurasi" : "Belum"}
                            </td>
                            <td>—</td>
                          </tr>
                          <tr>
                            <td>API token</td>
                            <td className="is-status">
                              {accurate.configured.api_token ? "Terkonfigurasi" : "Belum"}
                            </td>
                            <td>Nilai secret disembunyikan</td>
                          </tr>
                          <tr>
                            <td>Signature</td>
                            <td className="is-status">
                              {accurate.configured.signature ? "Terkonfigurasi" : "Belum"}
                            </td>
                            <td>Nilai secret disembunyikan</td>
                          </tr>
                          <tr>
                            <td>DB ID</td>
                            <td className="is-status">
                              {accurate.configured.db_id ? "Terkonfigurasi" : "Belum"}
                            </td>
                            <td>—</td>
                          </tr>
                          <tr>
                            <td>SJ live sync</td>
                            <td className="is-status">
                              <span
                                className={`ep-settings-pill${accurate.sj_live_sync_enabled ? "" : " is-off"}`}
                              >
                                {accurate.sj_live_sync_enabled ? "ON" : "OFF"}
                              </span>
                            </td>
                            <td>{accurate.note}</td>
                          </tr>
                        </>
                      )}
                    </tbody>
                  </table>
                </div>
                <div className="ep-settings-actions">
                  <button
                    type="button"
                    className="ep-btn"
                    disabled
                    title="SJ live sync tetap OFF"
                  >
                    Sync production (disabled)
                  </button>
                </div>
              </section>
            ) : null}

            {tab === "audit" ? (
              <section className="ep-settings-card">
                <h3 className="ep-settings-card-title">Audit Log</h3>
                <p className="ep-settings-card-note">Append-only · modul settings.</p>
                <div className="ep-settings-table-wrap">
                  <table className="ep-settings-data-table">
                    <thead>
                      <tr>
                        <th style={{ width: 160 }}>Waktu</th>
                        <th>Aksi</th>
                        <th style={{ width: 180 }}>Actor</th>
                        <th style={{ width: 180 }}>Target</th>
                      </tr>
                    </thead>
                    <tbody>
                      {audits.length === 0 ? (
                        <tr>
                          <td colSpan={4}>
                            <div className="ep-settings-empty">
                              Belum ada entri / akses ditolak.
                            </div>
                          </td>
                        </tr>
                      ) : (
                        audits.map((a, idx) => (
                          <tr key={`${a.action}-${idx}`}>
                            <td>{a.created_at ? String(a.created_at) : "—"}</td>
                            <td title={a.action}>{a.action}</td>
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
        </div>
      </div>

      <ConfirmationSuccessOverlay
        open={Boolean(feedback)}
        tone={feedback?.tone || "success"}
        title={feedback?.title || ""}
        message={feedback?.message || ""}
        global
        autoCloseMs={feedback?.tone === "success" ? 2200 : 0}
        onClose={() => setFeedback(null)}
      />
    </div>
  );
}

function ProductsSettingsPanel({
  onError,
  onDirtyChange,
  onFeedback,
}: {
  onError: (msg: string | null) => void;
  onDirtyChange: (dirty: boolean) => void;
  onFeedback: (tone: "success" | "error", title: string, message: string) => void;
}) {
  const [items, setItems] = useState<Array<{ item_code: string; screen_size?: string }>>([]);
  const [unsetLabel, setUnsetLabel] = useState("Belum diatur");
  const [itemCode, setItemCode] = useState("");
  const [screenSize, setScreenSize] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    onDirtyChange(Boolean(itemCode.trim()) || Boolean(screenSize.trim()));
  }, [itemCode, screenSize, onDirtyChange]);

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetchSettingsProducts();
        setItems(res.items);
        setUnsetLabel(res.screen_unset_label);
      } catch (err) {
        onError(err instanceof Error ? err.message : "Gagal memuat Master Produk");
      }
    })();
  }, [onError]);

  async function onSave(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    onError(null);
    try {
      await saveProductScreenSize(itemCode.trim(), screenSize);
      const res = await fetchSettingsProducts();
      setItems(res.items);
      setItemCode("");
      setScreenSize("");
      onFeedback("success", "Berhasil", "Pengaturan berhasil disimpan.");
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Gagal menyimpan ukuran screen";
      onError(msg);
      onFeedback("error", "Gagal", msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Simpan screen_size</h3>
        <p className="ep-settings-card-note">
          Satu sumber data <code>master_products</code>. String kosong = {unsetLabel}.
        </p>
        <form className="ep-settings-form-grid" onSubmit={onSave}>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">item_code</span>
            <input
              value={itemCode}
              onChange={(e) => setItemCode(e.target.value)}
              required
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Ukuran screen</span>
            <input
              value={screenSize}
              onChange={(e) => setScreenSize(e.target.value)}
            />
          </label>
          <div className="ep-settings-actions">
            <button
              type="submit"
              className={`ep-btn primary${busy ? " is-busy" : ""}`}
              disabled={busy || !itemCode.trim()}
            >
              {busy ? "Menyimpan…" : "Simpan"}
            </button>
          </div>
        </form>
      </section>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Daftar produk</h3>
        <div className="ep-settings-table-wrap">
          <table className="ep-settings-data-table is-compact">
            <thead>
              <tr>
                <th>item_code</th>
                <th style={{ width: 180 }}>screen_size</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={2}>
                    <div className="ep-settings-empty">Belum ada data / akses ditolak.</div>
                  </td>
                </tr>
              ) : (
                items.map((it) => (
                  <tr key={it.item_code}>
                    <td>{it.item_code}</td>
                    <td>{it.screen_size?.trim() ? it.screen_size : unsetLabel}</td>
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
