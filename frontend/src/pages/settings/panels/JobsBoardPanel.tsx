import { useMemo, useState, type FormEvent } from "react";
import type {
  MasterTaskType,
  OperationalRole,
  RoleActionGrant,
  TaskAction,
} from "../../../api/operational";
import type { SettingsBranch, SettingsUser } from "../../../api/settings";
import { computeJobConfigStatus } from "../jobConfigStatus";
import type { ResourceLoadState } from "../loadState";
import { jobConfigStatusLabel, type SettingsTabId } from "../settingsNav";
import { roleWorkLabel } from "../settingsLabels";
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
};

type BoardSection = "status" | "types" | "assign" | "grants";

export function JobsBoardPanel(props: Props) {
  const [section, setSection] = useState<BoardSection>("status");

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
      const grant = props.grants.find(
        (g) => g.role === task.assigned_role && g.task_type === task.task_type,
      );
      const allowedRoles = Array.from(
        new Set(
          props.grants
            .filter((g) => g.task_type === task.task_type && (g.actions || []).length > 0)
            .map((g) => g.role),
        ),
      );
      return {
        task,
        status,
        allowedRoles,
        actions: grant?.actions || [],
      };
    });
  }, [
    props.taskTypes,
    props.grants,
    props.users,
    props.branches,
    props.usersLoad,
    props.grantsLoad,
    props.branchesLoad,
  ]);

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Pekerjaan & penugasan</h3>
        <p className="ep-settings-card-note">
          Peran kerja (produksi) berbeda dari peran sistem (menu aplikasi). Mengubah
          izin pekerjaan tidak mengubah penanggung jawab, dan sebaliknya. Grant kosong
          tidak dipulihkan otomatis.
        </p>
        <div className="ep-settings-subtabs" role="tablist" aria-label="Bagian pekerjaan">
          {(
            [
              ["status", "Status konfigurasi"],
              ["types", "Jenis pekerjaan"],
              ["assign", "Penanggung jawab"],
              ["grants", "Izin pekerjaan"],
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
      </section>

      {section === "status" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Status tiap jenis pekerjaan</h3>
          <div className="ep-settings-table-wrap">
            <table className="ep-settings-data-table">
              <thead>
                <tr>
                  <th>Nama pekerjaan</th>
                  <th className="is-role">Penanggung jawab</th>
                  <th className="is-role">Peran yang diizinkan</th>
                  <th>Aksi yang diizinkan</th>
                  <th className="is-status">Status</th>
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
                        {row.task.assigned_role
                          ? roleWorkLabel(row.task.assigned_role)
                          : "—"}
                      </td>
                      <td className="is-role">
                        {row.allowedRoles.length
                          ? row.allowedRoles.map((r) => roleWorkLabel(r)).join(", ")
                          : "—"}
                      </td>
                      <td>{row.actions.length ? row.actions.join(", ") : "—"}</td>
                      <td className="is-status">
                        <span
                          className={`ep-settings-status-pill is-${
                            row.status === "SIAP" ? "ok" : "warn"
                          }`}
                          title={
                            row.status === "BELUM_TERVERIFIKASI"
                              ? "Data muat gagal atau cakupan cabang belum terbukti"
                              : undefined
                          }
                        >
                          {jobConfigStatusLabel(row.status)}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
          <div className="ep-settings-actions" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="ep-btn"
              onClick={() => setSection("grants")}
            >
              Perbaiki izin
            </button>
            <button
              type="button"
              className="ep-btn"
              onClick={() => props.onOpenLeaf("ops-grants")}
            >
              Buka halaman izin terpisah
            </button>
          </div>
        </section>
      ) : null}

      {section === "types" ? (
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

      {section === "assign" ? (
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

      {section === "grants" ? (
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
    </>
  );
}
