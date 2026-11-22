import { useMemo, useState } from "react";
import type { SettingsRole } from "../../../api/settings";
import { modulePermLabel, roleKindLabel } from "../settingsLabels";
import { modulePermsAreDirty } from "../settingsDraftUtils";
import { SettingsStickySaveBar } from "../SettingsStickySaveBar";

type PolicyMap = Record<
  string,
  Array<{ permission: string; source: string; revocable: boolean; note?: string }>
>;

type Props = {
  roles: SettingsRole[];
  selectedRole: string;
  onSelectRole: (roleKey: string) => void | Promise<void>;
  modulePerms: string[];
  sensitivePerms: string[];
  policyGrants: PolicyMap;
  draftPerms: string[];
  setDraftPerms: (next: string[] | ((p: string[]) => string[])) => void;
  permReason: string;
  setPermReason: (v: string) => void;
  confirmSensitive: boolean;
  setConfirmSensitive: (v: boolean) => void;
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

export function ModulePermsPanel({
  roles,
  selectedRole,
  onSelectRole,
  modulePerms,
  sensitivePerms,
  policyGrants,
  draftPerms,
  setDraftPerms,
  permReason,
  setPermReason,
  confirmSensitive,
  setConfirmSensitive,
  saving,
  onSave,
  onDiscard,
  askConfirm,
}: Props) {
  const [query, setQuery] = useState("");
  const [showTechIds, setShowTechIds] = useState(false);
  const selectedRoleDoc = roles.find((r) => r.role_key === selectedRole);
  const dirty = selectedRoleDoc
    ? modulePermsAreDirty(
        draftPerms,
        selectedRoleDoc.module_permissions,
        policyGrants[selectedRole],
      )
    : false;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return modulePerms;
    return modulePerms.filter((p) => {
      const label = modulePermLabel(p).toLowerCase();
      return label.includes(q) || p.toLowerCase().includes(q);
    });
  }, [modulePerms, query]);

  async function handleDiscard() {
    if (!dirty) return;
    const res = await askConfirm({
      title: "Batalkan perubahan?",
      message: "Perubahan hak akses menu yang belum disimpan akan dibuang.",
      confirmLabel: "Buang perubahan",
    });
    if (!res.ok) return;
    onDiscard();
  }

  return (
    <section className="ep-settings-card ep-settings-card-flush">
      <h3 className="ep-settings-card-title">Hak akses menu</h3>
      <p className="ep-settings-card-note">
        Peran baru tidak mendapat hak apa pun sampai Anda mencentang dan menyimpan.
        Hak sensitif wajib dikonfirmasi beserta alasan.
      </p>

      <div className="ep-settings-toolbar">
        <label className="ep-settings-field">
          <span className="ep-settings-field-label">Peran sistem yang diatur</span>
          <select
            value={selectedRole}
            onChange={(e) => void onSelectRole(e.target.value)}
            aria-label="Pilih peran sistem"
          >
            {roles.map((r) => (
              <option key={r.role_key} value={r.role_key}>
                {r.label} ({roleKindLabel(r.kind)}) · v{r.version}
              </option>
            ))}
          </select>
        </label>
        <label className="ep-settings-field">
          <span className="ep-settings-field-label">Cari hak akses</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Contoh: kelola pengguna…"
            aria-label="Cari hak akses menu"
          />
        </label>
        <label className="ep-settings-check-row ep-settings-tech-toggle">
          <input
            type="checkbox"
            checked={showTechIds}
            onChange={(e) => setShowTechIds(e.target.checked)}
          />
          Tampilkan ID teknis
        </label>
      </div>

      <div className="ep-settings-table-wrap">
        <table className="ep-settings-data-table is-compact">
          <thead>
            <tr>
              <th>Hak akses</th>
              <th className="is-check">Aktif di draft</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={2}>
                  <div className="ep-settings-empty">
                    {modulePerms.length === 0
                      ? "Katalog hak akses kosong atau gagal dimuat."
                      : "Tidak ada hak akses yang cocok dengan pencarian."}
                  </div>
                </td>
              </tr>
            ) : (
              filtered.map((p) => {
                const policyHit = (policyGrants[selectedRole] || []).find(
                  (g) => g.permission === p && !g.revocable,
                );
                return (
                  <tr key={p}>
                    <td title={showTechIds ? undefined : p}>
                      <div className="ep-settings-perm-label">{modulePermLabel(p)}</div>
                      {showTechIds ? (
                        <div className="ep-settings-perm-id" title={p}>
                          {p}
                        </div>
                      ) : null}
                      {sensitivePerms.includes(p) ? (
                        <span className="ep-settings-pill is-warn">
                          Sensitif — berisiko tinggi
                        </span>
                      ) : null}
                      {policyHit ? (
                        <span
                          className="ep-settings-pill"
                          title={policyHit.note || policyHit.source}
                          data-testid="policy-grant-badge"
                        >
                          Dari kebijakan (tidak bisa dicabut)
                        </span>
                      ) : null}
                    </td>
                    <td className="is-check">
                      <input
                        type="checkbox"
                        checked={draftPerms.includes(p) || Boolean(policyHit)}
                        disabled={
                          selectedRole === "super_admin" || Boolean(policyHit) || saving
                        }
                        aria-label={modulePermLabel(p)}
                        title={
                          policyHit
                            ? policyHit.note ||
                              "Berlaku dari kebijakan sistem — tidak dapat dicabut di sini"
                            : undefined
                        }
                        onChange={(e) => {
                          if (policyHit) return;
                          setDraftPerms((prev) =>
                            e.target.checked
                              ? Array.from(new Set([...prev, p]))
                              : prev.filter((x) => x !== p),
                          );
                        }}
                      />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="ep-settings-form-grid" style={{ marginTop: 12 }}>
        <label className="ep-settings-field" style={{ gridColumn: "1 / -1" }}>
          <span className="ep-settings-field-label">
            Alasan (wajib jika menambah hak sensitif)
          </span>
          <input
            value={permReason}
            onChange={(e) => setPermReason(e.target.value)}
            disabled={saving}
          />
        </label>
        <label className="ep-settings-check-row">
          <input
            type="checkbox"
            checked={confirmSensitive}
            disabled={saving}
            onChange={(e) => setConfirmSensitive(e.target.checked)}
          />
          Saya paham dan mengonfirmasi perubahan hak sensitif
        </label>
      </div>

      <SettingsStickySaveBar
        dirtyCount={dirty ? 1 : 0}
        dirtyLabel="Ada perubahan hak akses menu belum disimpan"
        saving={saving}
        saveLabel="Simpan Hak Akses Menu"
        disabled={!selectedRoleDoc || selectedRole === "super_admin"}
        onSave={onSave}
        onDiscard={() => void handleDiscard()}
        statusId="module-perms-save-status"
      />
    </section>
  );
}
