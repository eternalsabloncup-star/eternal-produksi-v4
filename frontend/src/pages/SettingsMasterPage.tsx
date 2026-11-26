import { useCallback, useEffect, useMemo, useState } from "react";
import { NavIcon } from "../app/icons";
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
  createSettingsUser,
  createSettingsRole,
  deactivateSettingsRole,
  deactivateTaskType,
  duplicateSettingsRole,
  fetchAccurateStatus,
  fetchAuditLog,
  fetchBranches,
  fetchMachines,
  fetchSettingsCatalog,
  fetchSettingsRoles,
  fetchSettingsUsers,
  fetchShifts,
  fetchUserSessions,
  resetUserPassword,
  revokeUserSession,
  saveRolePermissions,
  setBranchAccurateMapping,
  setBranchActive,
  setUserActive,
  SettingsApiError,
  updateSettingsRoleLabel,
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
import { DetailDrawer } from "../components/DetailDrawer";
import { SettingsConfirmDialog } from "./settings/SettingsConfirmDialog";
import { useSettingsConfirm } from "./settings/useSettingsConfirm";
import {
  SETTINGS_NAV_ITEMS,
  SETTINGS_NAV_SECTIONS,
  navItemById,
  type SettingsTabId,
} from "./settings/settingsNav";
import {
  assignmentsFromTaskTypes,
  baselineModulePerms,
  countChangedGrants,
  grantKey,
  modulePermsAreDirty,
  serializeGrants,
} from "./settings/settingsDraftUtils";
import { OpsGrantsPanel } from "./settings/panels/OpsGrantsPanel";
import { ModulePermsPanel } from "./settings/panels/ModulePermsPanel";
import { UsersPanel, type UserFormState } from "./settings/panels/UsersPanel";
import { RolesCatalogPanel } from "./settings/panels/RolesCatalogPanel";
import { TaskAssignPanel, TaskTypesPanel } from "./settings/panels/TaskPanels";
import { BranchesPanel, MachinesPanel, ShiftsPanel } from "./settings/panels/OrgPanels";
import {
  ProductsListPanel,
  ProductScreenPanel,
} from "./settings/panels/ProductsPanels";
import {
  AccuratePanel,
  AuditPanel,
  OverviewPanel,
  WorkflowPanel,
} from "./settings/panels/SystemPanels";
import { JobsBoardPanel } from "./settings/panels/JobsBoardPanel";
import { OrgReadinessBanner } from "./settings/panels/OrgReadinessBanner";
import {
  classifyListResult,
  loadStateMessage,
  type ResourceLoadState,
  type SettingsResourceKey,
} from "./settings/loadState";

export type { SettingsTabId };
export {
  SETTINGS_NAV_ITEMS,
  SETTINGS_NAV_SECTIONS,
  navItemById,
  grantKey,
  serializeGrants,
  assignmentsFromTaskTypes,
};
export { serializePerms } from "./settings/settingsDraftUtils";

const EMPTY_USER: UserFormState = {
  email: "",
  display_name: "",
  password: "",
  role: "user",
  operational_role: "",
  branch_ids: [],
};

type FeedbackState = {
  tone: "success" | "error";
  title: string;
  message: string;
};

type LoadErrorKind = "empty" | "forbidden" | "load" | "conflict" | "partial" | null;

/** Pengaturan V4 — Settings Center V2; draft lokal → Simpan eksplisit. */
export function SettingsMasterPage() {
  const confirm = useSettingsConfirm();
  const [tab, setTab] = useState<SettingsTabId>("overview");
  const [error, setError] = useState<string | null>(null);
  const [errorKind, setErrorKind] = useState<LoadErrorKind>(null);
  const [feedback, setFeedback] = useState<FeedbackState | null>(null);
  const [resourceLoad, setResourceLoad] = useState<
    Partial<Record<SettingsResourceKey, ResourceLoadState>>
  >({});

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
  const [policyGrants, setPolicyGrants] = useState<
    Record<string, Array<{ permission: string; source: string; revocable: boolean; note?: string }>>
  >({});
  const [draftPerms, setDraftPerms] = useState<string[]>([]);
  const [selectedRole, setSelectedRole] = useState<string>("");
  const [permReason, setPermReason] = useState("");
  const [confirmSensitive, setConfirmSensitive] = useState(false);
  const [branches, setBranches] = useState<SettingsBranch[]>([]);
  const [mappingEditCode, setMappingEditCode] = useState<string | null>(null);
  const [mappingEditValue, setMappingEditValue] = useState("");
  const [savingMapping, setSavingMapping] = useState(false);
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
  const [newMachine, setNewMachine] = useState({
    code: "",
    label: "",
    branch_code: "",
    machine_type: "",
    output_per_shift_pcs: "",
  });
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
  const [sessionsUser, setSessionsUser] = useState<SettingsUser | null>(null);
  const [sessions, setSessions] = useState<Array<{ id: string; created_at?: string; expires_at?: string }>>([]);
  const [sessionsLoading, setSessionsLoading] = useState(false);

  const activeNav = navItemById(tab);

  const showFeedback = useCallback(
    (tone: FeedbackState["tone"], title: string, message: string) => {
      setFeedback({ tone, title, message });
    },
    [],
  );

  const reportError = useCallback((msg: string, kind: LoadErrorKind = "load") => {
    setError(msg);
    setErrorKind(kind);
  }, []);

  const loadMaster = useCallback(async () => {
    setMasterLoading(true);
    setResourceLoad((prev) => ({ ...prev, masterJobs: "loading" }));
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
      setResourceLoad((prev) => ({
        ...prev,
        masterJobs: classifyListResult(true, types.items.length),
      }));
    } catch (err) {
      setResourceLoad((prev) => ({ ...prev, masterJobs: "LOAD_FAILED" }));
      reportError(
        err instanceof Error ? err.message : "Gagal memuat data master pekerjaan.",
        "load",
      );
    } finally {
      setMasterLoading(false);
    }
  }, [reportError]);

  const loadSettings = useCallback(async () => {
    setError(null);
    setErrorKind(null);
    try {
      const cat = await fetchSettingsCatalog();
      setModulePerms(cat.module_permissions);
      setSensitivePerms(cat.sensitive_module_permissions);
      setPolicyGrants(cat.policy_grants ?? {});
      setPolicies(cat.workflow_policies.items);

      type Bag<T> = { ok: true; items: T[]; forbidden?: boolean } | { ok: false; items: T[]; forbidden?: boolean };

      const wrap = async <T,>(
        p: Promise<{ items: T[] }>,
      ): Promise<Bag<T>> => {
        try {
          const res = await p;
          return { ok: true, items: res.items };
        } catch (err) {
          const forbidden =
            err instanceof SettingsApiError && err.status === 403;
          return { ok: false, items: [], forbidden };
        }
      };

      const [u, r, b, m, s, a, acc] = await Promise.all([
        wrap(fetchSettingsUsers()),
        wrap(fetchSettingsRoles()),
        wrap(fetchBranches()),
        wrap(fetchMachines()),
        wrap(fetchShifts()),
        wrap(fetchAuditLog({ module: "settings", limit: 50 })),
        fetchAccurateStatus()
          .then((v) => ({ ok: true as const, value: v }))
          .catch(() => ({ ok: false as const, value: null })),
      ]);

      // Jangan hapus data sukses sebelumnya bila request gagal.
      if (u.ok) setUsers(u.items);
      if (r.ok) {
        setRoles(r.items);
        setSelectedRole((prev) => prev || (r.items[0]?.role_key ?? ""));
      }
      if (b.ok) setBranches(b.items);
      if (m.ok) setMachines(m.items);
      if (s.ok) setShifts(s.items);
      if (a.ok) setAudits(a.items);
      if (acc.ok) setAccurate(acc.value);

      setResourceLoad((prev) => ({
        ...prev,
        users: classifyListResult(u.ok, u.items.length, Boolean(u.forbidden)),
        roles: classifyListResult(r.ok, r.items.length, Boolean(r.forbidden)),
        branches: classifyListResult(b.ok, b.items.length, Boolean(b.forbidden)),
        machines: classifyListResult(m.ok, m.items.length, Boolean(m.forbidden)),
        shifts: classifyListResult(s.ok, s.items.length, Boolean(s.forbidden)),
        audits: classifyListResult(a.ok, a.items.length, Boolean(a.forbidden)),
        accurate: acc.ok ? (acc.value ? "SUCCESS" : "DATA_EMPTY") : "LOAD_FAILED",
      }));

      const failed = [u, r, b, m, s, a].filter((x) => !x.ok);
      if (failed.length > 0 && failed.length < 6) {
        setErrorKind("partial");
        setError("Sebagian data pengaturan gagal dimuat. Tombol Muat Ulang tersedia per bagian.");
      } else if (failed.length === 6) {
        reportError("Gagal memuat Pengaturan.", "load");
      }
    } catch (err) {
      reportError(
        err instanceof Error ? err.message : "Gagal memuat Pengaturan.",
        "load",
      );
    }
  }, [reportError]);

  useEffect(() => {
    void loadMaster();
    void loadSettings();
  }, [loadMaster, loadSettings]);

  useEffect(() => {
    const role = roles.find((x) => x.role_key === selectedRole);
    setDraftPerms(
      baselineModulePerms(role ? role.module_permissions : [], policyGrants[selectedRole]),
    );
    setConfirmSensitive(false);
    setPermReason("");
  }, [selectedRole, roles, policyGrants]);

  const selectedRoleDoc = roles.find((r) => r.role_key === selectedRole);

  const modulePermsDirty = selectedRoleDoc
    ? modulePermsAreDirty(
        draftPerms,
        selectedRoleDoc.module_permissions,
        policyGrants[selectedRole],
      )
    : false;

  const opsGrantsDirty = serializeGrants(draftGrants) !== serializeGrants(grants);

  const assignmentsDirty = useMemo(() => {
    const baseline = assignmentsFromTaskTypes(taskTypes);
    const keys = new Set([...Object.keys(baseline), ...Object.keys(draftAssignments)]);
    for (const k of keys) {
      if ((draftAssignments[k] || "") !== (baseline[k] || "")) return true;
    }
    return false;
  }, [draftAssignments, taskTypes]);

  const assignmentDirtyCount = useMemo(() => {
    const baseline = assignmentsFromTaskTypes(taskTypes);
    let n = 0;
    const keys = new Set([...Object.keys(baseline), ...Object.keys(draftAssignments)]);
    for (const k of keys) {
      if ((draftAssignments[k] || "") !== (baseline[k] || "")) n += 1;
    }
    return n;
  }, [draftAssignments, taskTypes]);

  const formDirtyUsers =
    Boolean(newUser.email.trim()) ||
    Boolean(newUser.display_name.trim()) ||
    Boolean(newUser.password) ||
    newUser.branch_ids.length > 0 ||
    editingUser != null;
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
      case "roles-catalog":
        return formDirtyRole;
      case "module-perms":
        return modulePermsDirty;
      case "ops-grants":
      case "jobs-board":
        return opsGrantsDirty || assignmentsDirty || formDirtyManual;
      case "task-types":
        return formDirtyManual;
      case "task-assign":
        return assignmentsDirty;
      case "branches":
        return formDirtyBranch || mappingEditCode != null;
      case "machines":
        return formDirtyMachine;
      case "shifts":
        return formDirtyShift;
      case "products":
      case "product-screen":
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
      case "roles-catalog":
        setNewRole({ role_key: "", label: "" });
        break;
      case "module-perms": {
        const role = roles.find((x) => x.role_key === selectedRole);
        // Phase 0: keep non-revocable policy grants in discarded baseline.
        setDraftPerms(
          baselineModulePerms(
            role ? role.module_permissions : [],
            policyGrants[selectedRole],
          ),
        );
        setConfirmSensitive(false);
        setPermReason("");
        break;
      }
      case "ops-grants":
        setDraftGrants(grants.map((g) => ({ ...g, actions: [...g.actions] })));
        break;
      case "jobs-board":
        setDraftGrants(grants.map((g) => ({ ...g, actions: [...g.actions] })));
        setDraftAssignments(assignmentsFromTaskTypes(taskTypes));
        setManualTask({ task_type: "", label: "", assigned_role: "helper" });
        break;
      case "task-types":
        setManualTask({ task_type: "", label: "", assigned_role: "helper" });
        break;
      case "task-assign":
        setDraftAssignments(assignmentsFromTaskTypes(taskTypes));
        break;
      case "products":
        setProductsDirty(false);
        break;
      case "branches":
        setNewBranch({ code: "", name: "" });
        setMappingEditCode(null);
        setMappingEditValue("");
        break;
      case "machines":
        setNewMachine({
          code: "",
          label: "",
          branch_code: "",
          machine_type: "",
          output_per_shift_pcs: "",
        });
        break;
      case "shifts":
        setNewShift({ code: "", label: "", start_time: "08:00", end_time: "16:00" });
        break;
      case "product-screen":
        setProductsDirty(false);
        break;
      default:
        break;
    }
  }

  async function requestTab(next: SettingsTabId) {
    if (next === tab) return;
    if (isTabDirty(tab)) {
      const res = await confirm.ask({
        title: "Perubahan belum disimpan",
        message:
          "Ada perubahan yang belum disimpan di halaman ini. Buang perubahan dan pindah menu?",
        details: ["Batalkan = tetap di sini dan lanjut mengedit."],
        confirmLabel: "Buang dan pindah",
        cancelLabel: "Tetap di sini",
        tone: "danger",
      });
      if (!res.ok) return;
      discardDraftsForTab(tab);
    }
    setError(null);
    setErrorKind(null);
    setTab(next);
  }

  async function saveModulePermissions() {
    if (!selectedRoleDoc || selectedRole === "super_admin" || savingModulePerms) return;
    if (!modulePermsDirty) return;
    const res = await confirm.ask({
      title: "Simpan hak akses menu?",
      message: `Simpan hak akses menu untuk peran "${selectedRoleDoc.label}"?`,
      details: [
        "Jika orang lain ikut mengubah, sistem akan meminta Anda muat ulang.",
        "Hak sensitif memerlukan centang konfirmasi dan alasan.",
      ],
      confirmLabel: "Simpan hak akses menu",
    });
    if (!res.ok) return;
    setSavingModulePerms(true);
    setError(null);
    setErrorKind(null);
    try {
      await saveRolePermissions(selectedRole, {
        module_permissions: draftPerms,
        expected_version: selectedRoleDoc.version,
        reason: permReason || undefined,
        confirm_sensitive: confirmSensitive,
      });
      showFeedback("success", "Berhasil", "Hak akses menu berhasil disimpan.");
      await loadSettings();
    } catch (err) {
      if (err instanceof SettingsApiError && err.status === 409) {
        const msg =
          "Ada perubahan dari pengguna lain. Muat ulang data lalu simpan lagi.";
        reportError(msg, "conflict");
        showFeedback("error", "Konflik perubahan", msg);
        void loadSettings();
      } else {
        const msg =
          err instanceof Error ? err.message : "Gagal menyimpan hak akses menu.";
        reportError(msg, "load");
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
    setErrorKind(null);
    const baseline = new Map(
      grants.map((g) => [grantKey(g.role, g.task_type), [...g.actions].sort().join("|")]),
    );
    const changed = draftGrants.filter((g) => {
      const key = grantKey(g.role, g.task_type);
      const next = [...g.actions].sort().join("|");
      return (baseline.get(key) || "") !== next;
    });
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
    const failedLabels: string[] = [];

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
        failedLabels.push(key);
        lastErr = err instanceof Error ? err.message : "Gagal menyimpan izin.";
      }
    }

    setGrants(nextServer);
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
      showFeedback("success", "Berhasil", "Izin pekerjaan berhasil disimpan.");
    } else if (ok === 0) {
      reportError(lastErr, "load");
      showFeedback("error", "Gagal", lastErr || "Gagal menyimpan izin pekerjaan.");
    } else {
      const msg = `${ok} berhasil, ${fail} gagal (disimpan per baris). ${lastErr}`;
      reportError(`${msg} Baris gagal: ${failedLabels.join(", ")}`, "partial");
      showFeedback("error", "Sebagian berhasil", msg);
    }
    setSavingOpsGrants(false);
  }

  async function saveTaskAssignments() {
    if (!assignmentsDirty || savingAssignments) return;
    setSavingAssignments(true);
    setError(null);
    setErrorKind(null);
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
      showFeedback("success", "Berhasil", "Penanggung jawab berhasil disimpan.");
    } else if (ok === 0) {
      reportError(lastErr, "load");
      showFeedback("error", "Gagal", lastErr || "Gagal menyimpan penanggung jawab.");
    } else {
      const msg = `${ok} berhasil, ${fail} gagal (disimpan per jenis). ${lastErr}`;
      reportError(`${msg} Gagal: ${[...failedTypes].join(", ")}`, "partial");
      showFeedback("error", "Sebagian berhasil", msg);
    }
    setSavingAssignments(false);
  }

  async function onSelectRoleForPerms(nextKey: string) {
    if (nextKey === selectedRole) return;
    if (modulePermsDirty) {
      const res = await confirm.ask({
        title: "Perubahan belum disimpan",
        message: "Draft hak akses peran ini belum disimpan. Buang draft dan ganti peran?",
        confirmLabel: "Buang draft",
        cancelLabel: "Tetap mengedit",
        tone: "danger",
      });
      if (!res.ok) return;
    }
    setSelectedRole(nextKey);
  }

  return (
    <div className="ep-settings">
      <p className="ep-settings-eyebrow">Pengaturan Eternal V4</p>
      <div className="ep-settings-layout">
        <aside className="ep-settings-nav" aria-label="Navigasi Pengaturan">
          <label className="ep-settings-nav-mobile">
            <span className="ep-settings-nav-mobile-label">Menu Pengaturan</span>
            <select
              className="ep-settings-nav-select"
              value={tab}
              aria-label="Pilih menu Pengaturan"
              onChange={(e) => void requestTab(e.target.value as SettingsTabId)}
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
          <nav className="ep-settings-nav-desktop" aria-label="Menu Pengaturan">
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
                          onClick={() => void requestTab(item.id)}
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
            <div
              className={`ep-settings-alert${errorKind === "partial" ? " is-partial" : ""}`}
              role="alert"
            >
              <p>
                {errorKind === "conflict"
                  ? "Konflik perubahan — "
                  : errorKind === "partial"
                    ? "Sebagian berhasil — "
                    : errorKind === "forbidden"
                      ? "Akses ditolak — "
                      : errorKind === "load"
                        ? "Gagal memuat — "
                        : ""}
                {error}
              </p>
              {errorKind === "load" || errorKind === "partial" ? (
                <button
                  type="button"
                  className="ep-btn"
                  onClick={() => {
                    void loadMaster();
                    void loadSettings();
                  }}
                >
                  Muat ulang
                </button>
              ) : null}
            </div>
          ) : null}

          {(["users", "roles", "branches", "machines", "shifts", "masterJobs"] as SettingsResourceKey[]).map(
            (key) => {
              const st = resourceLoad[key];
              if (st !== "LOAD_FAILED" && st !== "FORBIDDEN") return null;
              const label =
                key === "masterJobs"
                  ? "master pekerjaan"
                  : key === "users"
                    ? "pengguna"
                    : key === "roles"
                      ? "peran"
                      : key === "branches"
                        ? "cabang"
                        : key === "machines"
                          ? "mesin"
                          : "shift";
              return (
                <div key={key} className="ep-settings-load-banner is-error" role="status">
                  <p>{loadStateMessage(st, label)}</p>
                  <button
                    type="button"
                    className="ep-btn"
                    onClick={() => {
                      if (key === "masterJobs") void loadMaster();
                      else void loadSettings();
                    }}
                  >
                    Muat ulang
                  </button>
                </div>
              );
            },
          )}

          <div className="ep-settings-body">
            {tab === "overview" ? (
              <OverviewPanel
                counts={{
                  users: users.length,
                  roles: roles.length,
                  taskTypes: taskTypes.length,
                  branches: branches.length,
                  machines: machines.length,
                  shifts: shifts.length,
                }}
                onNavigate={(id) => void requestTab(id)}
              />
            ) : null}

            {tab === "users" ? (
              <UsersPanel
                users={users}
                roles={roles}
                rolesOps={rolesOps}
                branches={branches}
                policyGrants={policyGrants}
                newUser={newUser}
                setNewUser={setNewUser}
                editingUser={editingUser}
                editUserForm={editUserForm}
                setEditUserForm={setEditUserForm}
                setEditingUser={setEditingUser}
                savingCreate={savingCreate}
                savingEditUser={savingEditUser}
                onCreate={(e) => {
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
                      showFeedback("success", "Berhasil", "Pengguna berhasil ditambahkan.");
                      await loadSettings();
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal membuat pengguna.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    } finally {
                      setSavingCreate(false);
                    }
                  })();
                }}
                onSaveEdit={(e) => {
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
                      showFeedback("success", "Berhasil", "Data pengguna berhasil disimpan.");
                      await loadSettings();
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal menyimpan pengguna.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    } finally {
                      setSavingEditUser(false);
                    }
                  })();
                }}
                onDeactivate={(u) => {
                  void (async () => {
                    const res = await confirm.ask({
                      title: "Nonaktifkan akun?",
                      message: `Nonaktifkan ${u.email}? Sesi login aktif akan dicabut.`,
                      requireReason: true,
                      reasonLabel: "Alasan (wajib jika masih terkait tugas terbuka)",
                      confirmLabel: "Nonaktifkan",
                      tone: "danger",
                    });
                    if (!res.ok) return;
                    try {
                      await setUserActive(u.id, false, res.reason);
                      showFeedback("success", "Berhasil", "Akun dinonaktifkan.");
                      await loadSettings();
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal menonaktifkan.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    }
                  })();
                }}
                onActivate={(u) => {
                  void (async () => {
                    const res = await confirm.ask({
                      title: "Aktifkan kembali akun?",
                      message: `Aktifkan kembali ${u.email}?`,
                      confirmLabel: "Aktifkan",
                    });
                    if (!res.ok) return;
                    try {
                      await setUserActive(u.id, true);
                      showFeedback("success", "Berhasil", "Akun diaktifkan kembali.");
                      await loadSettings();
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal mengaktifkan.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    }
                  })();
                }}
                onResetPassword={(u) => {
                  void (async () => {
                    const res = await confirm.ask({
                      title: "Reset kata sandi?",
                      message: `Reset kata sandi untuk ${u.email}? Semua sesi login akan dicabut.`,
                      requireReason: true,
                      reasonLabel: "Alasan",
                      confirmLabel: "Reset kata sandi",
                      tone: "danger",
                      extraField: {
                        label: "Kata sandi baru (minimal 12 karakter)",
                        inputType: "password",
                        required: true,
                        minLength: 12,
                      },
                    });
                    if (!res.ok) return;
                    try {
                      await resetUserPassword(u.id, res.extra, res.reason);
                      showFeedback("success", "Berhasil", "Kata sandi berhasil direset.");
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal reset kata sandi.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    }
                  })();
                }}
                onManageSessions={(u) => {
                  void (async () => {
                    setSessionsUser(u);
                    setSessionsLoading(true);
                    try {
                      const res = await fetchUserSessions(u.id);
                      setSessions(res.items);
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal memuat sesi.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                      setSessionsUser(null);
                    } finally {
                      setSessionsLoading(false);
                    }
                  })();
                }}
                onOpenModulePerms={(roleKey) => {
                  setSelectedRole(roleKey);
                  void requestTab("module-perms");
                }}
              />
            ) : null}

            {tab === "roles-catalog" ? (
              <RolesCatalogPanel
                roles={roles}
                newRole={newRole}
                setNewRole={setNewRole}
                savingCreate={savingCreate}
                onCreate={(e) => {
                  e.preventDefault();
                  if (savingCreate) return;
                  setSavingCreate(true);
                  void createSettingsRole(newRole.role_key, newRole.label)
                    .then(async () => {
                      setNewRole({ role_key: "", label: "" });
                      showFeedback("success", "Berhasil", "Peran sistem berhasil ditambahkan.");
                      await loadSettings();
                    })
                    .catch((err: unknown) => {
                      const msg =
                        err instanceof Error ? err.message : "Gagal menambah peran.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    })
                    .finally(() => setSavingCreate(false));
                }}
                onDuplicate={(role) => {
                  void (async () => {
                    const keyRes = await confirm.ask({
                      title: "Gandakan peran — kode baru",
                      message: `Gandakan "${role.label}". Hak sensitif tidak ikut disalin.`,
                      confirmLabel: "Lanjut",
                      extraField: {
                        label: "Kode peran baru",
                        required: true,
                        placeholder: "contoh: supervisor-cabang",
                      },
                    });
                    if (!keyRes.ok) return;
                    const labelRes = await confirm.ask({
                      title: "Gandakan peran — nama tampilan",
                      message: `Kode baru: ${keyRes.extra}`,
                      confirmLabel: "Gandakan peran",
                      extraField: {
                        label: "Nama tampilan",
                        required: true,
                        defaultValue: `${role.label} (salinan)`,
                      },
                    });
                    if (!labelRes.ok) return;
                    try {
                      await duplicateSettingsRole(
                        role.role_key,
                        keyRes.extra,
                        labelRes.extra,
                      );
                      showFeedback("success", "Berhasil", "Peran berhasil digandakan.");
                      await loadSettings();
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal menggandakan peran.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    }
                  })();
                }}
                onDeactivate={(role) => {
                  void (async () => {
                    const res = await confirm.ask({
                      title: "Nonaktifkan peran?",
                      message: `Nonaktifkan "${role.label}"? Gagal jika masih dipakai pengguna aktif.`,
                      requireReason: true,
                      reasonLabel: "Alasan",
                      confirmLabel: "Nonaktifkan",
                      tone: "danger",
                    });
                    if (!res.ok) return;
                    try {
                      await deactivateSettingsRole(role.role_key, res.reason);
                      showFeedback("success", "Berhasil", "Peran dinonaktifkan.");
                      await loadSettings();
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal menonaktifkan peran.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    }
                  })();
                }}
                onRename={(role) => {
                  void (async () => {
                    const res = await confirm.ask({
                      title: "Ubah nama peran",
                      message: `Peran: ${role.role_key}`,
                      confirmLabel: "Simpan nama",
                      extraField: {
                        label: "Nama tampilan baru",
                        required: true,
                        defaultValue: role.label,
                      },
                    });
                    if (!res.ok || res.extra === role.label) return;
                    try {
                      await updateSettingsRoleLabel(role.role_key, res.extra);
                      showFeedback("success", "Berhasil", "Nama peran diperbarui.");
                      await loadSettings();
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal mengubah nama peran.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    }
                  })();
                }}
              />
            ) : null}

            {tab === "module-perms" ? (
              <ModulePermsPanel
                roles={roles}
                selectedRole={selectedRole}
                onSelectRole={onSelectRoleForPerms}
                modulePerms={modulePerms}
                sensitivePerms={sensitivePerms}
                policyGrants={policyGrants}
                draftPerms={draftPerms}
                setDraftPerms={setDraftPerms}
                permReason={permReason}
                setPermReason={setPermReason}
                confirmSensitive={confirmSensitive}
                setConfirmSensitive={setConfirmSensitive}
                saving={savingModulePerms}
                onSave={() => void saveModulePermissions()}
                onDiscard={() => discardDraftsForTab("module-perms")}
                askConfirm={confirm.ask}
              />
            ) : null}

            {tab === "jobs-board" ? (
              <JobsBoardPanel
                taskTypes={taskTypes}
                rolesOps={rolesOps}
                users={users}
                branches={branches}
                usersLoad={resourceLoad.users || "idle"}
                grantsLoad={resourceLoad.masterJobs || "idle"}
                branchesLoad={resourceLoad.branches || "idle"}
                actions={actions}
                grants={grants}
                draftGrants={draftGrants}
                setDraftGrants={setDraftGrants}
                draftAssignments={draftAssignments}
                setDraftAssignments={setDraftAssignments}
                assignmentDirtyCount={assignmentDirtyCount}
                masterLoading={masterLoading}
                savingAssignments={savingAssignments}
                savingOpsGrants={savingOpsGrants}
                savingCreate={savingCreate}
                manualTask={manualTask}
                setManualTask={setManualTask}
                onCreateType={(e) => {
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
                      showFeedback("success", "Berhasil", "Jenis pekerjaan ditambahkan.");
                      await loadMaster();
                    })
                    .catch((err: unknown) => {
                      const msg =
                        err instanceof Error
                          ? err.message
                          : "Gagal menambah jenis pekerjaan.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    })
                    .finally(() => setSavingCreate(false));
                }}
                onDeactivateType={(t) => {
                  void (async () => {
                    const res = await confirm.ask({
                      title: "Nonaktifkan jenis pekerjaan?",
                      message: `Nonaktifkan "${t.label}"?`,
                      requireReason: true,
                      reasonLabel: "Alasan",
                      confirmLabel: "Nonaktifkan",
                      tone: "danger",
                    });
                    if (!res.ok) return;
                    try {
                      await deactivateTaskType(t.task_type, res.reason);
                      showFeedback("success", "Berhasil", "Jenis pekerjaan dinonaktifkan.");
                      await loadMaster();
                    } catch (err) {
                      const msg =
                        err instanceof Error
                          ? err.message
                          : "Gagal menonaktifkan jenis pekerjaan.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    }
                  })();
                }}
                onSaveAssignments={() => void saveTaskAssignments()}
                onDiscardAssignments={() => discardDraftsForTab("task-assign")}
                onSaveGrants={() => void saveOperationalGrants()}
                onDiscardGrants={() => discardDraftsForTab("ops-grants")}
                askConfirm={confirm.ask}
                onOpenLeaf={(id) => void requestTab(id)}
              />
            ) : null}

            {tab === "ops-grants" ? (
              <OpsGrantsPanel
                rolesOps={rolesOps}
                taskTypes={taskTypes}
                actions={actions}
                grants={grants}
                draftGrants={draftGrants}
                setDraftGrants={setDraftGrants}
                loading={masterLoading}
                saving={savingOpsGrants}
                onSave={() => void saveOperationalGrants()}
                onDiscard={() => discardDraftsForTab("ops-grants")}
                askConfirm={confirm.ask}
              />
            ) : null}

            {tab === "task-types" ? (
              <TaskTypesPanel
                taskTypes={taskTypes}
                rolesOps={rolesOps}
                manualTask={manualTask}
                setManualTask={setManualTask}
                savingCreate={savingCreate}
                onCreate={(e) => {
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
                      showFeedback("success", "Berhasil", "Jenis pekerjaan ditambahkan.");
                      await loadMaster();
                    })
                    .catch((err: unknown) => {
                      const msg =
                        err instanceof Error
                          ? err.message
                          : "Gagal menambah jenis pekerjaan.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    })
                    .finally(() => setSavingCreate(false));
                }}
                onDeactivate={(t) => {
                  void (async () => {
                    const res = await confirm.ask({
                      title: "Nonaktifkan jenis pekerjaan?",
                      message: `Nonaktifkan "${t.label}"?`,
                      requireReason: true,
                      reasonLabel: "Alasan",
                      confirmLabel: "Nonaktifkan",
                      tone: "danger",
                    });
                    if (!res.ok) return;
                    try {
                      await deactivateTaskType(t.task_type, res.reason);
                      showFeedback("success", "Berhasil", "Jenis pekerjaan dinonaktifkan.");
                      await loadMaster();
                    } catch (err) {
                      const msg =
                        err instanceof Error
                          ? err.message
                          : "Gagal menonaktifkan jenis pekerjaan.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    }
                  })();
                }}
              />
            ) : null}

            {tab === "task-assign" ? (
              <TaskAssignPanel
                taskTypes={taskTypes}
                rolesOps={rolesOps}
                draftAssignments={draftAssignments}
                setDraftAssignments={setDraftAssignments}
                dirtyCount={assignmentDirtyCount}
                saving={savingAssignments}
                onSave={() => void saveTaskAssignments()}
                onDiscard={() => discardDraftsForTab("task-assign")}
                askConfirm={confirm.ask}
              />
            ) : null}

            {tab === "branches" || tab === "machines" || tab === "shifts" ? (
              <OrgReadinessBanner
                branches={branches}
                machines={machines}
                shifts={shifts}
              />
            ) : null}

            {tab === "branches" ? (
              <BranchesPanel
                branches={branches}
                newBranch={newBranch}
                setNewBranch={setNewBranch}
                mappingEditCode={mappingEditCode}
                mappingEditValue={mappingEditValue}
                setMappingEditCode={setMappingEditCode}
                setMappingEditValue={setMappingEditValue}
                savingOrg={savingOrg}
                savingMapping={savingMapping}
                onSaveBranch={(e) => {
                  e.preventDefault();
                  if (savingOrg) return;
                  setSavingOrg(true);
                  void upsertBranch(newBranch.code, newBranch.name, true)
                    .then(async () => {
                      setNewBranch({ code: "", name: "" });
                      showFeedback("success", "Berhasil", "Cabang berhasil disimpan.");
                      await loadSettings();
                    })
                    .catch((err: unknown) => {
                      const msg =
                        err instanceof Error ? err.message : "Gagal menyimpan cabang.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    })
                    .finally(() => setSavingOrg(false));
                }}
                onToggleActive={(b) => {
                  void (async () => {
                    const next = !b.is_active;
                    let reason = "";
                    if (!next) {
                      const res = await confirm.ask({
                        title: "Nonaktifkan cabang?",
                        message: `Nonaktifkan cabang ${b.code}?`,
                        requireReason: true,
                        reasonLabel: "Alasan (wajib jika masih dipakai pengguna)",
                        confirmLabel: "Nonaktifkan",
                        tone: "danger",
                      });
                      if (!res.ok) return;
                      reason = res.reason;
                    } else {
                      const res = await confirm.ask({
                        title: "Aktifkan cabang?",
                        message: `Aktifkan cabang ${b.code}?`,
                        confirmLabel: "Aktifkan",
                      });
                      if (!res.ok) return;
                    }
                    setSavingOrg(true);
                    try {
                      await setBranchActive(b.code, next, reason || undefined);
                      showFeedback("success", "Berhasil", "Status cabang diperbarui.");
                      await loadSettings();
                    } catch (err) {
                      const msg =
                        err instanceof Error
                          ? err.message
                          : "Gagal mengubah status cabang.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    } finally {
                      setSavingOrg(false);
                    }
                  })();
                }}
                onConfirmMapping={(b) => {
                  void (async () => {
                    const trimmed = mappingEditValue.trim();
                    const res = await confirm.ask({
                      title: trimmed ? "Simpan tautan Accurate?" : "Hapus tautan Accurate?",
                      message: trimmed
                        ? `Simpan tautan ${b.code} → Accurate ${trimmed}? Perubahan tidak menggeser kode cabang PO historis.`
                        : `Hapus tautan Accurate untuk ${b.code}?`,
                      requireReason: true,
                      reasonLabel: "Alasan perubahan tautan (wajib)",
                      confirmLabel: "Simpan tautan",
                    });
                    if (!res.ok) return;
                    setSavingMapping(true);
                    try {
                      await setBranchAccurateMapping(
                        b.code,
                        trimmed ? trimmed : null,
                        res.reason,
                      );
                      setMappingEditCode(null);
                      setMappingEditValue("");
                      showFeedback("success", "Berhasil", "Tautan Accurate disimpan.");
                      await loadSettings();
                    } catch (err) {
                      const msg =
                        err instanceof Error ? err.message : "Gagal menyimpan tautan.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    } finally {
                      setSavingMapping(false);
                    }
                  })();
                }}
              />
            ) : null}

            {tab === "machines" ? (
              <MachinesPanel
                machines={machines}
                newMachine={newMachine}
                setNewMachine={setNewMachine}
                branches={branches}
                savingOrg={savingOrg}
                onSave={(e) => {
                  e.preventDefault();
                  if (savingOrg) return;
                  setSavingOrg(true);
                  void upsertMachine({
                    code: newMachine.code,
                    label: newMachine.label,
                    branch_code: newMachine.branch_code || undefined,
                    machine_type: newMachine.machine_type || undefined,
                    output_per_shift_pcs: newMachine.output_per_shift_pcs.trim()
                      ? Number(newMachine.output_per_shift_pcs)
                      : undefined,
                  })
                    .then(async () => {
                      setNewMachine({
                        code: "",
                        label: "",
                        branch_code: "",
                        machine_type: "",
                        output_per_shift_pcs: "",
                      });
                      showFeedback("success", "Berhasil", "Mesin berhasil disimpan.");
                      await loadSettings();
                    })
                    .catch((err: unknown) => {
                      const msg =
                        err instanceof Error ? err.message : "Gagal menyimpan mesin.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    })
                    .finally(() => setSavingOrg(false));
                }}
              />
            ) : null}

            {tab === "shifts" ? (
              <ShiftsPanel
                shifts={shifts}
                newShift={newShift}
                setNewShift={setNewShift}
                savingOrg={savingOrg}
                onSave={(e) => {
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
                      showFeedback("success", "Berhasil", "Jam kerja berhasil disimpan.");
                      await loadSettings();
                    })
                    .catch((err: unknown) => {
                      const msg =
                        err instanceof Error ? err.message : "Gagal menyimpan jam kerja.";
                      reportError(msg);
                      showFeedback("error", "Gagal", msg);
                    })
                    .finally(() => setSavingOrg(false));
                }}
              />
            ) : null}

            {tab === "products" ? (
              <ProductsListPanel
                onError={(msg) => (msg ? reportError(msg) : setError(null))}
                onDirtyChange={setProductsDirty}
                onFeedback={showFeedback}
              />
            ) : null}

            {tab === "product-screen" ? (
              <ProductScreenPanel
                onError={(msg) => (msg ? reportError(msg) : setError(null))}
                onDirtyChange={setProductsDirty}
                onFeedback={showFeedback}
              />
            ) : null}

            {tab === "workflow" ? <WorkflowPanel policies={policies} /> : null}

            {tab === "accurate" ? (
              <AccuratePanel
                accurate={accurate}
                onGoBranches={() => void requestTab("branches")}
              />
            ) : null}

            {tab === "audit" ? <AuditPanel audits={audits} /> : null}
          </div>
        </div>
      </div>

      <DetailDrawer
        open={Boolean(sessionsUser)}
        title="Sesi login"
        subtitle={sessionsUser?.email}
        onClose={() => setSessionsUser(null)}
      >
        {sessionsLoading ? (
          <p className="ep-settings-empty">Memuat sesi…</p>
        ) : sessions.length === 0 ? (
          <p className="ep-settings-empty">Tidak ada sesi aktif.</p>
        ) : (
          <div className="ep-settings-table-wrap">
            <table className="ep-settings-data-table is-compact">
              <thead>
                <tr>
                  <th>Dibuat</th>
                  <th>Kedaluwarsa</th>
                  <th className="is-actions">Aksi</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.id}>
                    <td>{s.created_at ? String(s.created_at) : "—"}</td>
                    <td>{s.expires_at ? String(s.expires_at) : "—"}</td>
                    <td className="is-actions">
                      <button
                        type="button"
                        className="ep-btn"
                        onClick={() => {
                          if (!sessionsUser) return;
                          void (async () => {
                            const res = await confirm.ask({
                              title: "Cabut sesi login?",
                              message: "Pengguna harus masuk ulang pada perangkat ini.",
                              confirmLabel: "Cabut sesi",
                              tone: "danger",
                            });
                            if (!res.ok) return;
                            try {
                              await revokeUserSession(sessionsUser.id, s.id);
                              const next = await fetchUserSessions(sessionsUser.id);
                              setSessions(next.items);
                              showFeedback("success", "Berhasil", "Sesi dicabut.");
                            } catch (err) {
                              const msg =
                                err instanceof Error ? err.message : "Gagal mencabut sesi.";
                              showFeedback("error", "Gagal", msg);
                            }
                          })();
                        }}
                      >
                        Cabut
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </DetailDrawer>

      <SettingsConfirmDialog
        open={confirm.open}
        request={confirm.request}
        busy={confirm.busy}
        onCancel={confirm.onCancel}
        onConfirm={confirm.onConfirm}
      />

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

// re-export helpers used by tests / tooling
export { countChangedGrants };
