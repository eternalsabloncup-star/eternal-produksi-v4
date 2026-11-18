import type { PoLineItem, PoRow } from "../demo/types";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

type Props = {
  row: PoRow;
};

/** Hanya tabel item line — tanpa mengulang header PO. */
export function PoLineItemsDetail({ row }: Props) {
  const items: PoLineItem[] = row.items || [];

  return (
    <div className="ep-po-detail">
      <div className="ep-po-detail-scroll">
        <table className="ep-tbl ep-tbl-sheet ep-tbl-detail">
          <thead>
            <tr>
              <th>Kode Barang</th>
              <th>Nama Barang</th>
              <th>Kts.</th>
              <th>Satuan</th>
              <th>Proses</th>
              <th>Warna</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td colSpan={6}>
                  <div className="ep-empty">
                    Tidak ada item pada PO ini
                    {row.origin === "demo" ? " (DEMO)" : ""}.
                  </div>
                </td>
              </tr>
            ) : (
              items.map((it) => (
                <tr key={it.lineId || `${row.sourcePoId}:${it.code}`}>
                  <td>{it.code}</td>
                  <td>{it.name}</td>
                  <td>
                    <b>{formatQty(it.qty)}</b>
                  </td>
                  <td>{it.unit}</td>
                  <td>{it.processKind ?? it.process}</td>
                  <td>{it.color}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
