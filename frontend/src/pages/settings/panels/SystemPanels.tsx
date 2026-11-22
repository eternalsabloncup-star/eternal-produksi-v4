import { SETTINGS_NAV_ITEMS, type SettingsTabId } from "../settingsNav";
import { workflowStatusLabel } from "../settingsLabels";
import type { AuditItem } from "../../../api/settings";
import { NavIcon } from "../../../app/icons";

type OverviewProps = {
  counts: {
    users: number;
    roles: number;
    taskTypes: number;
    branches: number;
    machines: number;
    shifts: number;
  };
  onNavigate: (id: SettingsTabId) => void;
};

export function OverviewPanel({ counts, onNavigate }: OverviewProps) {
  return (
    <>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Ringkasan data</h3>
        <p className="ep-settings-card-note">
          Peran baru tidak mendapat hak apa pun sampai Anda mencentang dan menyimpan.
          Hak sensitif wajib dikonfirmasi. Perubahan hanya tersimpan setelah tombol Simpan.
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
                <td className="is-status">{counts.users}</td>
              </tr>
              <tr>
                <td>Peran sistem</td>
                <td className="is-status">{counts.roles}</td>
              </tr>
              <tr>
                <td>Jenis pekerjaan</td>
                <td className="is-status">{counts.taskTypes}</td>
              </tr>
              <tr>
                <td>Cabang</td>
                <td className="is-status">{counts.branches}</td>
              </tr>
              <tr>
                <td>Mesin</td>
                <td className="is-status">{counts.machines}</td>
              </tr>
              <tr>
                <td>Jam kerja</td>
                <td className="is-status">{counts.shifts}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section className="ep-settings-card">
        <h3 className="ep-settings-card-title">Pintasan menu</h3>
        <div className="ep-settings-shortcuts">
          {SETTINGS_NAV_ITEMS.filter((item) => item.id !== "overview").map((item) => (
            <button
              key={item.id}
              type="button"
              className="ep-settings-shortcut"
              onClick={() => onNavigate(item.id)}
            >
              <NavIcon name={item.icon} size={15} />
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      </section>
    </>
  );
}

type AccurateProps = {
  accurate: {
    configured: Record<string, boolean>;
    sj_live_sync_enabled: boolean;
    note: string;
  } | null;
  onGoBranches: () => void;
};

export function AccuratePanel({ accurate, onGoBranches }: AccurateProps) {
  return (
    <section className="ep-settings-card">
      <h3 className="ep-settings-card-title">Status Accurate Online</h3>
      <p className="ep-settings-card-note">
        Hanya status koneksi. Kata sandi dan token tidak ditampilkan. Tautan cabang
        diatur di menu Cabang.
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
                    Tidak dapat memuat status, atau Anda tidak punya hak mengaksesnya.
                  </div>
                </td>
              </tr>
            ) : (
              <>
                <tr>
                  <td>Alamat dasar (Base URL)</td>
                  <td className="is-status">
                    {accurate.configured.base_url ? "Terkonfigurasi" : "Belum"}
                  </td>
                  <td>—</td>
                </tr>
                <tr>
                  <td>Token API</td>
                  <td className="is-status">
                    {accurate.configured.api_token ? "Terkonfigurasi" : "Belum"}
                  </td>
                  <td>Nilai rahasia disembunyikan</td>
                </tr>
                <tr>
                  <td>Tanda tangan (Signature)</td>
                  <td className="is-status">
                    {accurate.configured.signature ? "Terkonfigurasi" : "Belum"}
                  </td>
                  <td>Nilai rahasia disembunyikan</td>
                </tr>
                <tr>
                  <td>ID database</td>
                  <td className="is-status">
                    {accurate.configured.db_id ? "Terkonfigurasi" : "Belum"}
                  </td>
                  <td>—</td>
                </tr>
                <tr>
                  <td>Sinkron Surat Jalan langsung</td>
                  <td className="is-status">
                    <span
                      className={`ep-settings-pill${accurate.sj_live_sync_enabled ? "" : " is-off"}`}
                    >
                      {accurate.sj_live_sync_enabled ? "Aktif" : "Nonaktif"}
                    </span>
                  </td>
                  <td>{accurate.note}</td>
                </tr>
              </>
            )}
          </tbody>
        </table>
      </div>
      <div className="ep-settings-actions is-split">
        <button type="button" className="ep-btn" onClick={onGoBranches}>
          Kelola tautan cabang Accurate
        </button>
        <button
          type="button"
          className="ep-btn"
          disabled
          title="Sinkron produksi tidak tersedia dari halaman ini"
        >
          Sinkron produksi (tidak tersedia)
        </button>
      </div>
    </section>
  );
}

type WorkflowProps = {
  policies: Array<{ id: string; label: string; status: string; note: string }>;
};

export function WorkflowPanel({ policies }: WorkflowProps) {
  return (
    <section className="ep-settings-card">
      <h3 className="ep-settings-card-title">Aturan alur kerja</h3>
      <p className="ep-settings-card-note">
        Hanya tampilan — pengaturan alur tidak diubah dari halaman ini.
      </p>
      <div className="ep-settings-table-wrap">
        <table className="ep-settings-data-table">
          <thead>
            <tr>
              <th>Aturan</th>
              <th className="is-status">Status</th>
              <th>Catatan</th>
            </tr>
          </thead>
          <tbody>
            {policies.length === 0 ? (
              <tr>
                <td colSpan={3}>
                  <div className="ep-settings-empty">Tidak ada aturan.</div>
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
                      {workflowStatusLabel(p.status)}
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
  );
}

type AuditProps = {
  audits: AuditItem[];
};

function auditActionLabel(action: string): string {
  const map: Record<string, string> = {
    "settings.user.create": "Tambah pengguna",
    "settings.user.update": "Ubah pengguna",
    "settings.user.activate": "Aktifkan pengguna",
    "settings.user.deactivate": "Nonaktifkan pengguna",
    "settings.user.reset_password": "Reset kata sandi",
    "settings.session.revoke": "Cabut sesi login",
    "settings.role.create": "Tambah peran",
    "settings.role.duplicate": "Gandakan peran",
    "settings.role.update": "Ubah peran",
    "settings.role.deactivate": "Nonaktifkan peran",
    "settings.role.permissions.save": "Simpan hak akses menu",
    "settings.task_type.create": "Tambah jenis pekerjaan",
    "settings.task_type.deactivate": "Nonaktifkan jenis pekerjaan",
    "settings.branch.upsert": "Simpan cabang",
    "settings.branch.activate": "Aktifkan cabang",
    "settings.branch.deactivate": "Nonaktifkan cabang",
    "settings.branch.accurate_mapping": "Tautan cabang Accurate",
    "settings.machine.upsert": "Simpan mesin",
    "settings.shift.upsert": "Simpan jam kerja",
    "settings.product.screen_size.set": "Simpan ukuran screen",
    "master.role_actions.set": "Simpan izin pekerjaan",
  };
  return map[action] || action;
}

export function AuditPanel({ audits }: AuditProps) {
  return (
    <section className="ep-settings-card">
      <h3 className="ep-settings-card-title">Catatan perubahan</h3>
      <p className="ep-settings-card-note">
        Hanya menambah entri baru · perubahan di Pengaturan.
      </p>
      <div className="ep-settings-table-wrap">
        <table className="ep-settings-data-table">
          <thead>
            <tr>
              <th style={{ width: 160 }}>Waktu</th>
              <th>Kegiatan</th>
              <th style={{ width: 180 }}>Dilakukan oleh</th>
              <th style={{ width: 180 }}>Dikenakan pada</th>
            </tr>
          </thead>
          <tbody>
            {audits.length === 0 ? (
              <tr>
                <td colSpan={4}>
                  <div className="ep-settings-empty">
                    Belum ada catatan, atau Anda tidak punya hak melihatnya.
                  </div>
                </td>
              </tr>
            ) : (
              audits.map((a, idx) => (
                <tr key={`${a.action}-${idx}`}>
                  <td>{a.created_at ? String(a.created_at) : "—"}</td>
                  <td title={a.action}>{auditActionLabel(a.action)}</td>
                  <td>{a.actor_email || "—"}</td>
                  <td>{a.target_email || "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
