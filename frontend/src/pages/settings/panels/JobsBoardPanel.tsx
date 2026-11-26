import { useMemo, useState, type FormEvent } from "react";
import { DetailDrawer } from "../../../components/DetailDrawer";
import type {
  MasterTaskType,
  OperationalRole,
  RoleActionGrant,
  TaskAction,
} from "../../../api/operational";
import type { SettingsBranch, SettingsUser } from "../../../api/settings";
import { computeJobConfigStatus } from "../jobConfigStatus";
import type { ResourceLoadState } from "../loadState";
import { actionLabel, roleWorkLabel } from "../settingsLabels";
import { jobConfigStatusLabel, type SettingsTabId } from "../settingsNav";
import type { SettingsUxMode } from "../settingsUxMode";
import { grantKey, toggleDraftAction } from "../settingsDraftUtils";
import { OpsGrantsPanel } from "./OpsGrantsPanel";
import { TaskAssignPanel, TaskTypesPanel } from "./TaskPanels";

type ManualTask = {
  task_type: string;
  label: string;
  assigned_role: string;
};

type AskConfirm = (args: {
  title: string;
  message: string;
  confirmLabel?: string;
  details?: string[];
  requireReason?: boolean;
  reasonLabel?: string;
  tone?: "default" | "danger";
}) => Promise<{ ok: boolean; reason: string }>;

type Props = {
  taskTypes: MasterTaskType[];
  rolesOps: OperationalRole[];
  users: SettingsUser[];
  branches: SettingsBranch[];
  usersLoad: ResourceLoadState;
  grantsLoad: ResourceLoadState;
  branchesLoad: ResourceLoadState;
  actions: TaskAction[];
  grants: RoleActionGrant[];
  draftGrants: RoleActionGrant[];
  setDraftGrants: (
    next: RoleActionGrant[] | ((p: RoleActionGrant[]) => RoleActionGrant[]),
  ) => void;
  draftAssignments: Record<string, OperationalRole>;
  setDraftAssignments: (
    next:
      | Record<string, OperationalRole>
      | ((p: Record<string, OperationalRole>) => Record<string, OperationalRole>),
  ) => void;
  assignmentDirtyCount: number;
  masterLoading: boolean;
  savingAssignments: boolean;
  savingOpsGrants: boolean;
  savingCreate: boolean;
  manualTask: ManualTask;
  setManualTask: (v: ManualTask) => void;
  onCreateType: (e: FormEvent) => void;
  onDeactivateType: (t: MasterTaskType) => void;
  onSaveAssignments: () => void;
  onDiscardAssignments: () => void;
  onSaveGrants: () => void;
  onDiscardGrants: () => void;
  askConfirm: AskConfirm;
  onOpenLeaf: (id: SettingsTabId) => void;
  uxMode?: SettingsUxMode;
};

type BoardSection = "status" | "types" | "assign" | "grants";

export function JobsBoardPanel(props: Props) {
  const [section, setSection] = useState<BoardSection>("status");
  const [manageTask, setManageTask] = useState<MasterTaskType | null>(null);
  const uxMode = props.uxMode || "simple";

  const rows = useMemo(() => {
    const load = {
      usersLoad: props.usersLoad,
      grantsLoad: props.grantsLoad,
      branchesLoad: props.branchesLoad,
    };
    return props.taskTypes.map((task) => {
      const status = computeJobConfigStatus({
        task,
        grants: props.grants,
        users: props.users,
        branches: props.branches,
        load,
      });
      const assigned =
        props.draftAssignments[task.task_type] || task.assigned_role;
      const grant = props.draftGrants.find(
        (g) => g.role === assigned && g.task_type === task.task_type,
      );
      const allowedRoles = Array.from(
        new Set(
          props.grants
            .filter((g) => g.task_type === task.task_type && (g.actions || []).length > 0)
            .map((g) => g.role),
        ),
      );
      const dependents = props.users.filter(
        (u) =>
          u.is_active &&
          (u.operational_role || "").trim() === (task.assigned_role || "").trim(),
      );
      return {
        task,
        status,
        allowedRoles,
        actions: grant?.actions || [],
        dependents,
        assigned,
      };
    });
  }, [
    props.taskTypes,
    props.grants,
    props.draftGrants,
    props.draftAssignments,
    props.users,
    props.branches,
    props.usersLoad,
    props.grantsLoad,
    props.branchesLoad,
  ]);

  const manageAssigned =
    manageTask
      ? props.draftAssignments[manageTask.task_type] || manageTask.assigned_role
      : null;
  const manageGrantActions =
    manageTask && manageAssigned
      ? props.draftGrants.find(
          (g) => g.role === manageAssigned && g.task_type === manageTask.task_type,
        )?.actions || []
      : [];
  const baselineAssigned = manageTask?.assigned_role;
  /** Baseline izin for the role currently selected in the editor (not the prior assignee). */
  const baselineGrantForManageRole =
    manageTask && manageAssigned
      ? props.grants.find(
          (g) => g.role === manageAssigned && g.task_type === manageTask.task_type,
        )?.actions || []
      : [];
  const assignDirty =
    Boolean(manageTask) &&
    Boolean(manageAssigned) &&
    manageAssigned !== baselineAssigned;
  const grantDirty =
    Boolean(manageTask) &&
    Boolean(manageAssigned) &&
    [...manageGrantActions].sort().join("|") !==
      [...baselineGrantForManageRole].sort().join("|");

  async function saveManageAssignment() {
    if (!manageTask || !manageAssigned || !assignDirty) return;
    const res = await props.askConfirm({
      title: "Simpan penanggung jawab?",
      message:
        "Hanya mengubah penanggung jawab. Izin aksi tidak diubah otomatis. Penyimpanan per jenis (bukan atomic dengan izin).",
      details: [
        `Jenis: ${manageTask.label} (${manageTask.task_type})`,
        `Dari: ${baselineAssigned ? roleWorkLabel(baselineAssigned) : "—"}`,
        `Ke: ${roleWorkLabel(manageAssigned)}`,
      ],
      confirmLabel: "Simpan penanggung jawab",
    });
    if (!res.ok) return;
    props.onSaveAssignments();
  }

  async function saveManageGrants() {
    if (!manageTask || !manageAssigned || !grantDirty) return;
    const res = await props.askConfirm({
      title: "Simpan izin pekerjaan?",
      message:
        "Hanya mengubah izin aksi untuk pasangan peran×jenis ini. Penanggung jawab tidak diubah. Penyimpanan per baris; gagal sebagian mungkin terjadi.",
      details: [
        `Jenis: ${manageTask.label}`,
        `Peran: ${roleWorkLabel(manageAssigned)}`,
        `Aksi baru: ${manageGrantActions.length ? manageGrantActions.join(", ") : "(kosong)"}`,
        `Sebelumnya: ${
          baselineGrantForManageRole.length
            ? baselineGrantForManageRole.join(", ")
            : "(kosong)"
        }`,
      ],
      confirmLabel: "Simpan izin",
    });
    if (!res.ok) return;
    props.onSaveGrants();
  }

  const showAdvancedSections = uxMode === "advanced" || section !== "status";

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Pekerjaan & penugasan</h3>
        <p className="ep-settings-card-note">
          Satu ruang kerja: jenis pekerjaan, penanggung jawab, dan izin. Mengubah
          penanggung jawab tidak mengubah izin, dan sebaliknya. Grant kosong tidak
          dipulihkan otomatis.
        </p>
        {uxMode === "advanced" ? (
          <div className="ep-settings-subtabs" role="tablist" aria-label="Bagian pekerjaan">
            {(
              [
                ["status", "Status & kelola"],
                ["types", "Jenis pekerjaan"],
                ["assign", "Penanggung jawab"],
                ["grants", "Matriks izin"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={section === id}
                className={`ep-settings-subtab${section === id ? " is-active" : ""}`}
                onClick={() => setSection(id)}
              >
                {label}
              </button>
            ))}
          </div>
        ) : null}
      </section>

      {section === "status" || uxMode === "simple" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Konfigurasi jenis pekerjaan</h3>
          <div className="ep-settings-table-wrap">
            <table className="ep-settings-data-table">
              <thead>
                <tr>
                  <th>Jenis pekerjaan</th>
                  <th className="is-role">Penanggung jawab</th>
                  <th>Izin pekerjaan</th>
                  <th className="is-status">Status</th>
                  <th>Kelola</th>
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 ? (
                  <tr>
                    <td colSpan={5}>
                      <div className="ep-settings-empty">Belum ada jenis pekerjaan.</div>
                    </td>
                  </tr>
                ) : (
                  rows.map((row) => (
                    <tr key={row.task.task_type}>
                      <td>{row.task.label}</td>
                      <td className="is-role">
                        {row.assigned ? roleWorkLabel(row.assigned) : "—"}
                      </td>
                      <td>
                        {row.actions.length
                          ? row.actions.map((a) => actionLabel(a)).join(", ")
                          : "—"}
                      </td>
                      <td className="is-status">
                        <span
                          className={`ep-settings-status-pill is-${
                            row.status === "SIAP" ? "ok" : "warn"
                          }`}
                        >
                          {jobConfigStatusLabel(row.status)}
                        </span>
                      </td>
                      <td>
                        <button
                          type="button"
                          className="ep-btn"
                          onClick={() => setManageTask(row.task)}
                        >
                          Kelola
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          {uxMode === "advanced" ? (
            <div className="ep-settings-actions" style={{ marginTop: 12 }}>
              <button
                type="button"
                className="ep-btn"
                onClick={() => props.onOpenLeaf("ops-grants")}
              >
                Buka matriks izin penuh
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

      {showAdvancedSections && section === "types" ? (
        <TaskTypesPanel
          taskTypes={props.taskTypes}
          rolesOps={props.rolesOps}
          manualTask={props.manualTask}
          setManualTask={props.setManualTask}
          savingCreate={props.savingCreate}
          onCreate={props.onCreateType}
          onDeactivate={props.onDeactivateType}
        />
      ) : null}

      {showAdvancedSections && section === "assign" ? (
        <TaskAssignPanel
          taskTypes={props.taskTypes}
          rolesOps={props.rolesOps}
          draftAssignments={props.draftAssignments}
          setDraftAssignments={props.setDraftAssignments}
          dirtyCount={props.assignmentDirtyCount}
          saving={props.savingAssignments}
          onSave={props.onSaveAssignments}
          onDiscard={props.onDiscardAssignments}
          askConfirm={props.askConfirm}
        />
      ) : null}

      {showAdvancedSections && section === "grants" ? (
        <OpsGrantsPanel
          rolesOps={props.rolesOps}
          taskTypes={props.taskTypes}
          actions={props.actions}
          grants={props.grants}
          draftGrants={props.draftGrants}
          setDraftGrants={props.setDraftGrants}
          loading={props.masterLoading}
          saving={props.savingOpsGrants}
          onSave={props.onSaveGrants}
          onDiscard={props.onDiscardGrants}
          askConfirm={props.askConfirm}
        />
      ) : null}

      <DetailDrawer
        open={Boolean(manageTask)}
        title={manageTask ? `Kelola: ${manageTask.label}` : "Kelola pekerjaan"}
        subtitle="Penanggung jawab dan izin disimpan terpisah"
        onClose={() => setManageTask(null)}
        footer={
          <div className="ep-settings-sticky-save-actions">
            <button type="button" className="ep-btn" onClick={() => setManageTask(null)}>
              Tutup
            </button>
            <button
              type="button"
              className={`ep-btn${props.savingAssignments ? " is-busy" : ""}`}
              disabled={!assignDirty || props.savingAssignments}
              onClick={() => void saveManageAssignment()}
            >
              Simpan penanggung jawab
            </button>
            <button
              type="button"
              className={`ep-btn primary${props.savingOpsGrants ? " is-busy" : ""}`}
              disabled={!grantDirty || props.savingOpsGrants}
              onClick={() => void saveManageGrants()}
            >
              Simpan izin
            </button>
          </div>
        }
      >
        {manageTask ? (
          <div className="ep-settings-form-grid">
            <label className="ep-settings-field">
              <span className="ep-settings-field-label">Penanggung jawab</span>
              <select
                value={manageAssigned || ""}
                onChange={(e) => {
                  const role = e.target.value as OperationalRole;
                  props.setDraftAssignments((prev) => ({
                    ...prev,
                    [manageTask.task_type]: role,
                  }));
                }}
              >
                {props.rolesOps.map((r) => (
                  <option key={r} value={r}>
                    {roleWorkLabel(r)}
                  </option>
                ))}
              </select>
            </label>
            <div className="ep-settings-field ep-settings-field-span">
              <span className="ep-settings-field-label">Izin aksi (peran di atas)</span>
              <p className="ep-settings-card-note">
                Centang tidak mengubah penanggung jawab. Tidak ada “centang semua”
                diam-diam di editor ini.
              </p>
              <div className="ep-settings-check-grid">
                {props.actions.map((action) => {
                  const checked = manageGrantActions.includes(action);
                  return (
                    <label key={action} className="ep-settings-check">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          if (!manageAssigned) return;
                          props.setDraftGrants((prev) =>
                            toggleDraftAction(
                              prev,
                              manageAssigned,
                              manageTask.task_type,
                              action,
                              e.target.checked,
                            ),
                          );
                        }}
                      />
                      <span>{actionLabel(action)}</span>
                    </label>
                  );
                })}
              </div>
            </div>
            <div className="ep-settings-field ep-settings-field-span">
              <span className="ep-settings-field-label">Pengguna terkait (aktif)</span>
              <p className="ep-settings-card-note">
                {rows.find((r) => r.task.task_type === manageTask.task_type)?.dependents
                  .length
                  ? rows
                      .find((r) => r.task.task_type === manageTask.task_type)!
                      .dependents.map((u) => u.display_name || u.email)
                      .join(", ")
                  : "Belum ada pengguna aktif dengan peran ini."}
              </p>
              <p className="ep-settings-hint">
                Kunci draft: {grantKey(manageAssigned || "—", manageTask.task_type)}
              </p>
            </div>
          </div>
        ) : null}
      </DetailDrawer>
    </>
  );
}
