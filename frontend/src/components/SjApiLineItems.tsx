import type { DeliveryNoteItem } from "../api/delivery";
import { display, formatQtyByUnit, qtyByUnit, qtyText } from "../lib/deliverySj";

type Props = {
  items: DeliveryNoteItem[];
};

/** Rincian item line Surat Jalan — identitas baris dipertahankan. */
export function SjApiLineItems({ items }: Props) {
  const lines = items || [];

  return (
    <div className="ep-po-detail" data-testid="sj-line-items">
      <div className="ep-po-detail-scroll">
        <table className="ep-tbl ep-tbl-sheet ep-tbl-detail">
          <thead>
            <tr>
              <th>Baris SJ</th>
              <th>Item Line PO</th>
              <th>Kode Barang</th>
              <th>Nama Barang</th>
              <th>Kts.</th>
              <th>Satuan</th>
              <th>Gudang/Outlet</th>
              <th>Catatan Item</th>
            </tr>
          </thead>
          <tbody>
            {lines.length === 0 ? (
              <tr>
                <td colSpan={8}>
                  <div className="ep-empty">Surat Jalan ini belum memiliki item.</div>
                </td>
              </tr>
            ) : (
              lines.map((item) => (
                <tr key={item.sj_line_id}>
                  <td>{display(item.sj_line_id)}</td>
                  <td>{display(item.accurate_line_id)}</td>
                  <td>{display(item.item_code)}</td>
                  <td>{display(item.item_name)}</td>
                  <td>
                    <b>{qtyText(item.qty)}</b>
                  </td>
                  <td>{display(item.unit)}</td>
                  <td>{display(item.warehouse_name)}</td>
                  <td>{display(item.detail_notes)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <p className="ep-po-confirm-hint">
        {lines.length} baris barang · Total {formatQtyByUnit(qtyByUnit(lines))}
      </p>
    </div>
  );
}
