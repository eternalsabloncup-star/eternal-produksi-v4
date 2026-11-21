import { useState } from "react";
import type { DeliveryNoteItem } from "../api/delivery";
import { display, formatQtyByUnit, qtyByUnit, qtyText } from "../lib/deliverySj";

type Props = {
  items: DeliveryNoteItem[];
};

const MOBILE_PREVIEW_COUNT = 3;

function hasExtraDetail(item: DeliveryNoteItem): boolean {
  return Boolean(
    (item.warehouse_name || "").trim() || (item.detail_notes || "").trim(),
  );
}

/** Rincian item line Surat Jalan — tabel desktop; daftar compact di mobile. */
export function SjApiLineItems({ items }: Props) {
  const lines = items || [];
  const totalByUnit = formatQtyByUnit(qtyByUnit(lines));
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const needsCollapse = lines.length > MOBILE_PREVIEW_COUNT;
  const visibleMobile = showAll || !needsCollapse
    ? lines
    : lines.slice(0, MOBILE_PREVIEW_COUNT);

  function toggleExpand(sjLineId: string) {
    setExpanded((prev) => ({ ...prev, [sjLineId]: !prev[sjLineId] }));
  }

  return (
    <div className="ep-po-detail" data-testid="sj-line-items">
      <div className="ep-sj-items-desktop ep-po-detail-scroll">
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

      <div className="ep-sj-items-mobile" data-testid="sj-line-items-mobile">
        {lines.length === 0 ? (
          <div className="ep-empty">Surat Jalan ini belum memiliki item.</div>
        ) : (
          <ul className="ep-sj-item-list">
            {visibleMobile.map((item, index) => {
              const open = Boolean(expanded[item.sj_line_id]);
              const warehouse = (item.warehouse_name || "").trim();
              const notes = (item.detail_notes || "").trim();
              const lineMeta = [
                item.accurate_line_id
                  ? `Line ${item.accurate_line_id}`
                  : null,
                (item.item_code || "").trim() || null,
              ]
                .filter(Boolean)
                .join(" · ");
              return (
                <li key={item.sj_line_id} className="ep-sj-item">
                  <div className="ep-sj-item-row">
                    <div className="ep-sj-item-main">
                      <span className="ep-sj-item-idx" aria-hidden>
                        {index + 1}.
                      </span>
                      <div className="ep-sj-item-copy">
                        <div className="ep-sj-item-name">
                          {display(item.item_name)}
                        </div>
                        {lineMeta ? (
                          <div className="ep-sj-item-meta">{lineMeta}</div>
                        ) : null}
                      </div>
                    </div>
                    <div className="ep-sj-item-qty">
                      <span className="ep-sj-item-qty-num">
                        {qtyText(item.qty)}
                      </span>{" "}
                      <span className="ep-sj-item-qty-unit">
                        {display(item.unit)}
                      </span>
                    </div>
                  </div>
                  {hasExtraDetail(item) ? (
                    <button
                      type="button"
                      className="ep-sj-item-detail-toggle"
                      aria-expanded={open}
                      onClick={() => toggleExpand(item.sj_line_id)}
                    >
                      {open ? "Sembunyikan Detail" : "Detail"}
                    </button>
                  ) : null}
                  {open ? (
                    <div className="ep-sj-item-extra">
                      {warehouse ? (
                        <div className="ep-sj-item-extra-row">
                          <span className="lbl">Gudang/Outlet</span>
                          <strong>{warehouse}</strong>
                        </div>
                      ) : null}
                      {notes ? (
                        <div className="ep-sj-item-extra-row">
                          <span className="lbl">Catatan Item</span>
                          <strong>{notes}</strong>
                        </div>
                      ) : null}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
        {needsCollapse ? (
          <button
            type="button"
            className="ep-btn sm ep-sj-items-toggle"
            data-testid="sj-items-show-all"
            onClick={() => setShowAll((v) => !v)}
          >
            {showAll ? "Tampilkan Lebih Sedikit" : "Lihat Semua Barang"}
          </button>
        ) : null}
      </div>

      <div className="ep-sj-items-total" data-testid="sj-items-total">
        <div className="ep-sj-items-total-label">Total Surat Jalan</div>
        <div className="ep-sj-items-total-value">{totalByUnit}</div>
      </div>
      <p className="ep-po-confirm-hint ep-sj-items-desktop-hint">
        {lines.length} baris barang · Total {totalByUnit}
      </p>
    </div>
  );
}
