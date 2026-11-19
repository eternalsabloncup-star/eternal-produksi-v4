import type { DeliverySjRow } from "../demo/deliverySjDemo";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

type Props = {
  row: DeliverySjRow;
};

/** Detail item line SJ — tanpa mengulang header SJ. */
export function SjLineItemsDetail({ row }: Props) {
  const items = row.items || [];

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
              <th>Outlet</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr>
                <td colSpan={5}>
                  <div className="ep-empty">Tidak ada item pada SJ ini (DEMO).</div>
                </td>
              </tr>
            ) : (
              items.map((it) => (
                <tr key={`${row.sjNo}-${it.code}`}>
                  <td>{it.code}</td>
                  <td>{it.name}</td>
                  <td>
                    <b>{formatQty(it.qty)}</b>
                  </td>
                  <td>{it.unit}</td>
                  <td>{it.outlet}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
