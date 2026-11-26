import { useMemo, useState, type FormEvent } from "react";
import { DetailDrawer } from "../../../components/DetailDrawer";
import type { OperationalRole } from "../../../api/operational";
import type { SettingsBranch, SettingsRole, SettingsUser } from "../../../api/settings";
import {
  activeBranchOptions,
  formatUserBranches,
  toggleBranchId,
} from "../../settingsUsersUi";
import { buildEffectivePermsView } from "../effectivePermsSummary";
import { SettingsKelolaMenu } from "../SettingsKelolaMenu";
import { roleWorkLabel } from "../settingsLabels";
import type { PolicyGrant } from "../settingsDraftUtils";

export type UserFormState = {
  email: string;
  display_name: string;
  password: string;
  role: string;
  operational_role: string;
  branch_ids: string[];
};

type Props = {
  users: SettingsUser[];
  roles: SettingsRole[];
  rolesOps: OperationalRole[];
  branches: SettingsBranch[];
  newUser: UserFormState;
  setNewUser: (v: UserFormState | ((p: UserFormState) => UserFormState)) => void;
  editingUser: SettingsUser | null;
  editUserForm: Omit<UserFormState, "email" | "password">;
  setEditUserForm: (
    v:
      | Omit<UserFormState, "email" | "password">
      | ((p: Omit<UserFormState, "email" | "password">) => Omit<
          UserFormState,
          "email" | "password"
        >),
  ) => void;
  setEditingUser: (u: SettingsUser | null) => void;
  savingCreate: boolean;
  savingEditUser: boolean;
  onCreate: (e: FormEvent) => void;
  onSaveEdit: (e: FormEvent) => void;
  onDeactivate: (u: SettingsUser) => void;
  onActivate: (u: SettingsUser) => void;
  onResetPassword: (u: SettingsUser) => void;
  onManageSessions: (u: SettingsUser) => void;
  /** Optional: open Hak Akses Menu for the selected system role. */
  onOpenModulePerms?: (roleKey: string) => void;
  /** Policy grants per role — dipakai preview izin efektif (non-revocable). */
  policyGrants?: Record<string, PolicyGrant[] | undefined>;
};

export function UsersPanel({
  users,
  roles,
  rolesOps,
  branches,
  newUser,
  setNewUser,
  editingUser,
  editUserForm,
  setEditUserForm,
  setEditingUser,
  savingCreate,
  savingEditUser,
  onCreate,
  onSaveEdit,
  onDeactivate,
  onActivate,
  onResetPassword,
  onManageSessions,
  onOpenModulePerms,
  policyGrants = {},
}: Props) {
  const [q, setQ] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "inactive">("all");
  const [showCreate, setShowCreate] = useState(false);

  const assignableBranches = useMemo(() => activeBranchOptions(branches), [branches]);

  const editRoleDoc = useMemo(
    () => roles.find((r) => r.role_key === editUserForm.role),
    [roles, editUserForm.role],
  );
  const permView = useMemo(
    () =>
      buildEffectivePermsView({
        editingUser,
        selectedRoleKey: editUserForm.role,
        roles,
        policyGrants,
      }),
    [editingUser, editUserForm.role, roles, policyGrants],
  );

  const editBranchOptions = useMemo(() => {
    const selected = new Set((editUserForm.branch_ids || []).map((c) => c.toUpperCase()));
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

  const filtered = useMemo(() => {
    return users.filter((u) => {
      if (roleFilter && u.role !== roleFilter) return false;
      if (statusFilter === "active" && !u.is_active) return false;
      if (statusFilter === "inactive" && u.is_active) return false;
      const needle = q.trim().toLowerCase();
      if (!needle) return true;
      return (
        u.email.toLowerCase().includes(needle) ||
        u.display_name.toLowerCase().includes(needle)
      );
    });
  }, [users, q, roleFilter, statusFilter]);

  function kelolaItemsFor(u: SettingsUser) {
    return [
      {
        id: "edit",
        label: "Edit",
        onSelect: () => {
          setEditingUser(u);
          setEditUserForm({
            display_name: u.display_name,
            role: u.role,
            operational_role: u.operational_role || "",
            branch_ids: [...(u.branch_ids || [])],
          });
        },
      },
      u.is_active
        ? {
            id: "deactivate",
            label: "Nonaktifkan",
            tone: "danger" as const,
            onSelect: () => onDeactivate(u),
          }
        : {
            id: "activate",
            label: "Aktifkan kembali",
            onSelect: () => onActivate(u),
          },
      {
        id: "reset-password",
        label: "Reset kata sandi",
        onSelect: () => onResetPassword(u),
      },
      {
        id: "sessions",
        label: "Sesi login",
        onSelect: () => onManageSessions(u),
      },
    ];
  }

  function userRoleSystemLabel(u: SettingsUser) {
    return roles.find((r) => r.role_key === u.role)?.label || u.role;
  }

  function userRoleWorkLabel(u: SettingsUser) {
    return u.operational_role ? roleWorkLabel(u.operational_role) : "—";
  }

  function branchChecks(
    ids: string[],
    options: Array<{ code: string; name: string; is_active: boolean }>,
    onToggle: (code: string, checked: boolean) => void,
    allowInactiveSelected = false,
  ) {
    if (options.length === 0) {
      return (
        <p className="ep-settings-hint">
          Belum ada cabang aktif. Tambahkan cabang di menu Cabang terlebih dahulu.
        </p>
      );
    }
    return (
      <div className="ep-settings-check-grid">
        {options.map((b) => {
          const checked = ids.map((x) => x.toUpperCase()).includes(b.code.toUpperCase());
          const canAssign = b.is_active || (allowInactiveSelected && checked);
          return (
            <label key={b.code} className="ep-settings-check">
              <input
                type="checkbox"
                checked={checked}
                disabled={!b.is_active && !checked}
                onChange={(e) => {
                  if (!canAssign && e.target.checked) return;
                  onToggle(b.code, e.target.checked);
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
    );
  }

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Alur sederhana</h3>
        <p className="ep-settings-card-note">
          Pilih pengguna → pilih peran sistem → pilih cabang → lihat akses efektif →
          simpan. Permission baru tidak diberikan otomatis (default deny).
        </p>
        <div className="ep-settings-glossary">
          <div>
            <strong>Peran sistem</strong>
            <p>Hak membuka dan mengelola fitur aplikasi (menu, pengaturan, laporan).</p>
          </div>
          <div>
            <strong>Peran kerja</strong>
            <p>
              Jenis pekerjaan produksi yang dapat ditangani (designer, operator, helper,
              dll). Tidak digabung dengan peran sistem di database.
            </p>
          </div>
        </div>
      </section>

      <section className="ep-settings-card">
        <div className="ep-settings-card-head">
          <h3 className="ep-settings-card-title">Daftar pengguna</h3>
          <button type="button" className="ep-btn primary" onClick={() => setShowCreate(true)}>
            Tambah pengguna
          </button>
        </div>
        <div className="ep-settings-toolbar">
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Cari</span>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Email atau nama…"
              aria-label="Cari pengguna"
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Peran sistem</span>
            <select
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value)}
              aria-label="Filter peran sistem"
            >
              <option value="">Semua</option>
              {roles.map((r) => (
                <option key={r.role_key} value={r.role_key}>
                  {r.label}
                </option>
              ))}
            </select>
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Status</span>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)}
              aria-label="Filter status pengguna"
            >
              <option value="all">Semua</option>
              <option value="active">Aktif</option>
              <option value="inactive">Nonaktif</option>
            </select>
          </label>
        </div>
        <div className="ep-settings-table-wrap ep-settings-users-desktop">
          <table className="ep-settings-data-table is-users">
            <thead>
              <tr>
                <th className="is-email">Email</th>
                <th className="is-name">Nama</th>
                <th className="is-role">Peran sistem</th>
                <th className="is-role">Peran kerja</th>
                <th className="is-branch">Cabang</th>
                <th className="is-status">Aktif</th>
                <th className="is-actions">Aksi</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={7}>
                    <div className="ep-settings-empty">
                      {users.length === 0
                        ? "Belum ada pengguna, atau Anda tidak punya hak mengelola pengguna."
                        : "Tidak ada pengguna yang cocok dengan filter."}
                    </div>
                  </td>
                </tr>
              ) : (
                filtered.map((u) => (
                  <tr key={u.id} data-user-id={u.id}>
                    <td className="is-email" title={u.email}>
                      {u.email}
                    </td>
                    <td className="is-name">{u.display_name}</td>
                    <td className="is-role">{userRoleSystemLabel(u)}</td>
                    <td className="is-role">{userRoleWorkLabel(u)}</td>
                    <td
                      className="is-branch"
                      title={formatUserBranches(u.branch_ids, branches)}
                    >
                      {formatUserBranches(u.branch_ids, branches)}
                    </td>
                    <td className="is-status">
                      <span className={`ep-settings-pill${u.is_active ? "" : " is-off"}`}>
                        {u.is_active ? "Ya" : "Tidak"}
                      </span>
                    </td>
                    <td className="is-actions">
                      <SettingsKelolaMenu
                        ariaLabel={`Kelola pengguna ${u.display_name || u.email}`}
                        items={kelolaItemsFor(u)}
                      />
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <ul
          className="ep-settings-users-mobile"
          aria-label="Daftar pengguna"
          data-testid="settings-users-mobile"
        >
          {filtered.length === 0 ? (
            <li className="ep-settings-empty">
              {users.length === 0
                ? "Belum ada pengguna, atau Anda tidak punya hak mengelola pengguna."
                : "Tidak ada pengguna yang cocok dengan filter."}
            </li>
          ) : (
            filtered.map((u) => {
              const branchText = formatUserBranches(u.branch_ids, branches);
              return (
                <li
                  key={u.id}
                  className="ep-settings-user-card"
                  data-user-id={u.id}
                  data-testid={`settings-user-card-${u.id}`}
                >
                  <div className="ep-settings-user-card-top">
                    <div className="ep-settings-user-card-identity">
                      <p className="ep-settings-user-card-name">{u.display_name}</p>
                      <p className="ep-settings-user-card-email" title={u.email}>
                        {u.email}
                      </p>
                    </div>
                    <div className="ep-settings-user-card-actions">
                      <span className={`ep-settings-pill${u.is_active ? "" : " is-off"}`}>
                        {u.is_active ? "Aktif" : "Nonaktif"}
                      </span>
                      <SettingsKelolaMenu
                        ariaLabel={`Kelola pengguna ${u.display_name || u.email}`}
                        items={kelolaItemsFor(u)}
                      />
                    </div>
                  </div>
                  <dl className="ep-settings-user-card-meta">
                    <div>
                      <dt>Peran sistem</dt>
                      <dd>{userRoleSystemLabel(u)}</dd>
                    </div>
                    <div>
                      <dt>Peran kerja</dt>
                      <dd>{userRoleWorkLabel(u)}</dd>
                    </div>
                    <div className="ep-settings-user-card-branches">
                      <dt>Cabang</dt>
                      <dd title={branchText}>{branchText || "—"}</dd>
                    </div>
                  </dl>
                </li>
              );
            })
          )}
        </ul>
      </section>

      <DetailDrawer
        open={showCreate}
        title="Tambah pengguna"
        subtitle="Akun baru untuk masuk ke Eternal V4"
        onClose={() => setShowCreate(false)}
        footer={
          <div className="ep-settings-sticky-save-actions">
            <button type="button" className="ep-btn" onClick={() => setShowCreate(false)}>
              Tutup
            </button>
            <button
              type="submit"
              form="settings-create-user"
              className={`ep-btn primary${savingCreate ? " is-busy" : ""}`}
              disabled={savingCreate}
            >
              {savingCreate ? "Menyimpan…" : "Tambah pengguna"}
            </button>
          </div>
        }
      >
        <form id="settings-create-user" className="ep-settings-form-grid" onSubmit={onCreate}>
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
              onChange={(e) => setNewUser({ ...newUser, display_name: e.target.value })}
              required
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Kata sandi (minimal 12 karakter)</span>
            <input
              type="password"
              value={newUser.password}
              onChange={(e) => setNewUser({ ...newUser, password: e.target.value })}
              required
              minLength={12}
              autoComplete="new-password"
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Peran sistem</span>
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
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Peran kerja</span>
            <select
              aria-label="Peran kerja"
              value={newUser.operational_role}
              onChange={(e) =>
                setNewUser({ ...newUser, operational_role: e.target.value })
              }
            >
              <option value="">— Tidak ada —</option>
              {rolesOps.map((r) => (
                <option key={r} value={r}>
                  {roleWorkLabel(r)}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="ep-settings-field ep-settings-field-span">
            <legend className="ep-settings-field-label">Cabang</legend>
            {branchChecks(newUser.branch_ids, assignableBranches, (code, checked) =>
              setNewUser({
                ...newUser,
                branch_ids: toggleBranchId(newUser.branch_ids, code, checked),
              }),
            )}
          </fieldset>
        </form>
      </DetailDrawer>

      <DetailDrawer
        open={Boolean(editingUser)}
        title={editingUser ? `Edit pengguna` : "Edit pengguna"}
        subtitle={editingUser?.email}
        onClose={() => setEditingUser(null)}
        footer={
          <div className="ep-settings-sticky-save-actions">
            <button
              type="button"
              className="ep-btn"
              disabled={savingEditUser}
              onClick={() => setEditingUser(null)}
            >
              Batalkan
            </button>
            <button
              type="submit"
              form="settings-edit-user"
              className={`ep-btn primary${savingEditUser ? " is-busy" : ""}`}
              disabled={savingEditUser}
            >
              {savingEditUser ? "Menyimpan…" : "Simpan perubahan"}
            </button>
          </div>
        }
      >
        {editingUser ? (
          <form id="settings-edit-user" className="ep-settings-form-grid" onSubmit={onSaveEdit}>
            <label className="ep-settings-field">
              <span className="ep-settings-field-label">Nama</span>
              <input
                value={editUserForm.display_name}
                onChange={(e) =>
                  setEditUserForm({ ...editUserForm, display_name: e.target.value })
                }
                required
              />
            </label>
            <label className="ep-settings-field">
              <span className="ep-settings-field-label">Peran sistem</span>
              <select
                value={editUserForm.role}
                onChange={(e) => setEditUserForm({ ...editUserForm, role: e.target.value })}
                disabled={editingUser.role === "super_admin"}
              >
                {roles.map((r) => (
                  <option key={r.role_key} value={r.role_key}>
                    {r.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="ep-settings-field">
              <span className="ep-settings-field-label">Peran kerja</span>
              <select
                aria-label="Peran kerja"
                value={editUserForm.operational_role}
                onChange={(e) =>
                  setEditUserForm({
                    ...editUserForm,
                    operational_role: e.target.value,
                  })
                }
              >
                <option value="">— Tidak ada —</option>
                {rolesOps.map((r) => (
                  <option key={r} value={r}>
                    {roleWorkLabel(r)}
                  </option>
                ))}
              </select>
            </label>
            <fieldset className="ep-settings-field ep-settings-field-span">
              <legend className="ep-settings-field-label">Cabang</legend>
              {branchChecks(
                editUserForm.branch_ids,
                editBranchOptions,
                (code, checked) =>
                  setEditUserForm({
                    ...editUserForm,
                    branch_ids: toggleBranchId(editUserForm.branch_ids, code, checked),
                  }),
                true,
              )}
            </fieldset>
            <div className="ep-settings-field ep-settings-field-span">
              <span className="ep-settings-field-label">Ringkasan izin efektif</span>
              <p className="ep-settings-card-note">
                Preview mengikuti peran sistem yang dipilih
                {editRoleDoc ? ` (“${editRoleDoc.label}”)` : ""}. Snapshot lama tidak
                dipakai sebagai izin peran baru. Policy grants non-revocable tetap
                dihitung. Tidak menambah izin baru otomatis.
              </p>
              {permView.roleChanged ? (
                <div className="ep-settings-perm-split">
                  <div>
                    <strong className="ep-settings-field-label">Izin saat ini (tersimpan)</strong>
                    <p className="ep-settings-card-note">
                      Snapshot pengguna untuk peran “{editingUser?.role || "—"}” — bukan
                      preview peran baru.
                    </p>
                    {(editingUser?.effective_module_permissions || []).length === 0 ? (
                      <p className="ep-settings-empty">Tidak ada snapshot tersimpan.</p>
                    ) : (
                      <ul className="ep-settings-effective-list">
                        {(editingUser?.effective_module_permissions || []).map((p) => (
                          <li key={`cur-${p}`}>
                            <code>{p}</code>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div>
                    <strong className="ep-settings-field-label">
                      Preview setelah disimpan
                    </strong>
                    <p className="ep-settings-card-note">
                      Katalog peran “{permView.selectedRoleLabel}” + policy.
                    </p>
                    {permView.previewAfterSave.length === 0 ? (
                      <p className="ep-settings-empty">Belum ada hak akses menu.</p>
                    ) : (
                      <ul className="ep-settings-effective-list">
                        {permView.previewAfterSave.map((p) => (
                          <li key={`prev-${p}`}>
                            <code>{p}</code>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              ) : permView.previewAfterSave.length === 0 ? (
                <p className="ep-settings-empty">Belum ada hak akses menu.</p>
              ) : (
                <ul className="ep-settings-effective-list">
                  {permView.previewAfterSave.map((p) => (
                    <li key={p}>
                      <code>{p}</code>
                    </li>
                  ))}
                </ul>
              )}
              {onOpenModulePerms && editUserForm.role ? (
                <button
                  type="button"
                  className="ep-btn"
                  onClick={() => onOpenModulePerms(editUserForm.role)}
                >
                  Kelola hak akses menu peran ini
                </button>
              ) : null}
            </div>
          </form>
        ) : null}
      </DetailDrawer>
    </>
  );
}
