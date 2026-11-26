import type { SettingsBranch } from "../../../api/settings";

type Machine = { code: string; label: string; branch_code?: string };
type Shift = { code: string; label: string; start_time: string; end_time: string };

type Props = {
  branches: SettingsBranch[];
  machines: Machine[];
  shifts: Shift[];
};

/** Ringkasan cabang → mesin → shift. Capacity tetap INFORMATIONAL. */
export function OrgReadinessBanner({ branches, machines, shifts }: Props) {
  const activeBranches = branches.filter((b) => b.is_active);

  return (
    <section className="ep-settings-card">
      <h3 className="ep-settings-card-title">Kesiapan cabang & produksi</h3>
      <p className="ep-settings-card-note">
        Capacity Advisory bersifat informasi saja — tidak memblokir penjadwalan.
        Data mesin/shift tidak digandakan atau dibuat otomatis.
      </p>
      <div className="ep-settings-table-wrap">
        <table className="ep-settings-data-table is-compact">
          <thead>
            <tr>
              <th>Cabang</th>
              <th>Mesin</th>
              <th>Shift tersedia</th>
              <th className="is-status">Status kapasitas</th>
            </tr>
          </thead>
          <tbody>
            {activeBranches.length === 0 ? (
              <tr>
                <td colSpan={4}>
                  <div className="ep-settings-empty">Belum ada cabang aktif.</div>
                </td>
              </tr>
            ) : (
              activeBranches.map((b) => {
                const branchMachines = machines.filter(
                  (m) =>
                    !m.branch_code ||
                    m.branch_code.toUpperCase() === b.code.toUpperCase(),
                );
                const capacityReady =
                  branchMachines.length > 0 && shifts.length > 0;
                return (
                  <tr key={b.code}>
                    <td>
                      {b.name} ({b.code})
                    </td>
                    <td>
                      {branchMachines.length === 0
                        ? "Belum ada"
                        : branchMachines.map((m) => m.label || m.code).join(", ")}
                    </td>
                    <td>
                      {shifts.length === 0
                        ? "Belum ada"
                        : shifts.map((s) => s.label || s.code).join(", ")}
                    </td>
                    <td className="is-status">
                      {capacityReady
                        ? "Data ada (advisory)"
                        : "Belum lengkap — advisory INSUFFICIENT_DATA"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
