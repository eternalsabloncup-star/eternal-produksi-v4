import { useState, type FormEvent } from "react";
import type { SettingsRole } from "../../../api/settings";
import { SettingsKelolaMenu } from "../SettingsKelolaMenu";
import { roleKindLabel } from "../settingsLabels";

type Props = {
  roles: SettingsRole[];
  newRole: { role_key: string; label: string };
  setNewRole: (v: { role_key: string; label: string }) => void;
  savingCreate: boolean;
  onCreate: (e: FormEvent) => void;
  onDuplicate: (role: SettingsRole) => void;
  onDeactivate: (role: SettingsRole) => void;
  onRename: (role: SettingsRole) => void;
};

export function RolesCatalogPanel({
  roles,
  newRole,
  setNewRole,
  savingCreate,
  onCreate,
  onDuplicate,
  onDeactivate,
  onRename,
}: Props) {
  const [q, setQ] = useState("");
  const filtered = roles.filter((r) => {
    const needle = q.trim().toLowerCase();
    if (!needle) return true;
    return (
      r.label.toLowerCase().includes(needle) ||
      r.role_key.toLowerCase().includes(needle)
    );
  });

  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Tambah peran sistem</h3>
        <p className="ep-settings-card-note">
          Peran baru tidak mendapat hak akses apa pun sampai diatur di Hak Akses Menu.
        </p>
        <form className="ep-settings-form-grid" onSubmit={onCreate}>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Kode peran</span>
            <input
              value={newRole.role_key}
              onChange={(e) => setNewRole({ ...newRole, role_key: e.target.value })}
              required
              placeholder="contoh: supervisor-produksi"
            />
          </label>
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Nama tampilan</span>
            <input
              value={newRole.label}
              onChange={(e) => setNewRole({ ...newRole, label: e.target.value })}
              required
            />
          </label>
          <div className="ep-settings-actions">
            <button
              type="submit"
              className={`ep-btn primary${savingCreate ? " is-busy" : ""}`}
              disabled={savingCreate}
            >
              {savingCreate ? "Menyimpan…" : "Tambah peran"}
            </button>
          </div>
        </form>
      </section>

      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Daftar peran sistem</h3>
        <div className="ep-settings-toolbar">
          <label className="ep-settings-field">
            <span className="ep-settings-field-label">Cari</span>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Nama atau kode…"
              aria-label="Cari peran sistem"
            />
          </label>
        </div>
        <div className="ep-settings-table-wrap">
          <table className="ep-settings-data-table is-compact">
            <thead>
              <tr>
                <th>Nama</th>
                <th>Kode</th>
                <th className="is-status">Jenis</th>
                <th className="is-status">Versi</th>
                <th className="is-status">Aktif</th>
                <th className="is-actions">Aksi</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={6}>
                    <div className="ep-settings-empty">
                      {roles.length === 0
                        ? "Belum ada peran, atau Anda tidak punya hak mengelola peran."
                        : "Tidak ada peran yang cocok."}
                    </div>
                  </td>
                </tr>
              ) : (
                filtered.map((r) => (
                  <tr key={r.role_key}>
                    <td>{r.label}</td>
                    <td>
                      <code className="ep-settings-code">{r.role_key}</code>
                    </td>
                    <td className="is-status" title={r.kind}>
                      {roleKindLabel(r.kind)}
                    </td>
                    <td className="is-status">v{r.version}</td>
                    <td className="is-status">
                      <span className={`ep-settings-pill${r.is_active ? "" : " is-off"}`}>
                        {r.is_active ? "Ya" : "Tidak"}
                      </span>
                    </td>
                    <td className="is-actions">
                      <SettingsKelolaMenu
                        ariaLabel={`Kelola peran ${r.label}`}
                        items={[
                          {
                            id: "rename",
                            label: "Ubah nama",
                            disabled: r.role_key === "super_admin",
                            onSelect: () => onRename(r),
                          },
                          {
                            id: "duplicate",
                            label: "Gandakan",
                            onSelect: () => onDuplicate(r),
                          },
                          {
                            id: "deactivate",
                            label: "Nonaktifkan",
                            tone: "danger",
                            disabled:
                              !r.is_active ||
                              r.role_key === "super_admin" ||
                              r.kind === "system",
                            onSelect: () => onDeactivate(r),
                          },
                        ]}
                      />
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
