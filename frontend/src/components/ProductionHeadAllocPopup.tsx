import { useEffect, useMemo, useState } from "react";
import {
  DEMO_OPERATORS,
  DEMO_SCREEN_OPTIONS,
  listAllocationsForItem,
  listPrintItemsForPo,
  remainingQtyForItem,
  saveDemoAllocation,
  subscribeProductionAlloc,
  type DemoPrintItem,
  type DemoScreenOption,
  type ProductionAllocRow,
} from "../demo/productionHeadAlloc";
import { formatDateDisplay } from "../lib/formatDate";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";
import { PdfViewerModal } from "./PdfViewerModal";

type Props = {
  row: ProductionAllocRow | null;
  open: boolean;
  onClose: () => void;
};

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

/** Popup pembagian operator + item Cetak + screen + tanggal (DEMO). */
export function ProductionHeadAllocPopup({ row, open, onClose }: Props) {
  const [tick, setTick] = useState(0);
  const [itemId, setItemId] = useState("");
  const [operatorId, setOperatorId] = useState("");
  const [qtyText, setQtyText] = useState("");
  const [screen, setScreen] = useState<DemoScreenOption | "">("");
  const [workDate, setWorkDate] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [successOpen, setSuccessOpen] = useState(false);
  const [pdfOpen, setPdfOpen] = useState(false);

  useEffect(() => subscribeProductionAlloc(() => setTick((t) => t + 1)), []);

  const poItems = useMemo(() => {
    if (!row) return [] as DemoPrintItem[];
    return listPrintItemsForPo(row.poNo);
  }, [row]);

  const selectedItem = useMemo(() => {
    return poItems.find((i) => i.id === itemId) ?? null;
  }, [poItems, itemId]);

  const itemAllocs = useMemo(() => {
    void tick;
    if (!selectedItem) return [];
    return listAllocationsForItem(selectedItem.id);
  }, [selectedItem, tick]);

  const scheduled = useMemo(() => {
    if (!selectedItem) return 0;
    return itemAllocs.reduce((s, a) => s + a.qty, 0);
  }, [selectedItem, itemAllocs]);

  const remaining = useMemo(() => {
    void tick;
    if (!selectedItem) return 0;
    return remainingQtyForItem(selectedItem.id);
  }, [selectedItem, tick]);

  useEffect(() => {
    if (!open || !row) return;
    setItemId(row.id);
    setOperatorId("");
    setQtyText("");
    setScreen("");
    setWorkDate("");
    setError(null);
    setSuccessOpen(false);
    setPdfOpen(false);
  }, [open, row?.id]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (successOpen || pdfOpen) return;
      onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, successOpen, pdfOpen]);

  if (!open || !row) return null;

  function onSave() {
    if (!itemId) {
      setError("Pilih item line proses Cetak.");
      return;
    }
    if (!operatorId) {
      setError("Pilih operator terlebih dahulu.");
      return;
    }
    if (!screen) {
      setError("Pilih ukuran screen (Kecil/Besar).");
      return;
    }
    if (!workDate) {
      setError("Isi tanggal pengerjaan.");
      return;
    }
    const qty = Number(qtyText);
    if (!Number.isFinite(qty) || !Number.isInteger(qty)) {
      setError("Qty harus bilangan bulat positif.");
      return;
    }
    const result = saveDemoAllocation({
      itemId,
      operatorId,
      qty,
      screen,
      workDate,
    });
    if (!result.ok) {
      setError(result.error);
      return;
    }
    setError(null);
    setQtyText("");
    setOperatorId("");
    setScreen("");
    setSuccessOpen(true);
  }

  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="ep-modal ep-alloc-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Pembagian Qty Operator"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            <div className="ep-modal-kicker">Simulasi · DEMO</div>
            <h2>Pembagian Qty Operator</h2>
            <div className="ep-sub">
              {row.poNo}
              <span className="ep-flow-meta-sep" aria-hidden>
                ·
              </span>
              {row.customer}
            </div>
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>

        <div className="ep-modal-body">
          <label className="ep-alloc-field">
            <span className="lbl">Nama Operator</span>
            <select
              value={operatorId}
              onChange={(e) => {
                setOperatorId(e.target.value);
                setError(null);
              }}
              aria-label="Pilih operator"
            >
              <option value="">Pilih operator…</option>
              {DEMO_OPERATORS.map((op) => (
                <option key={op.id} value={op.id}>
                  {op.name}
                </option>
              ))}
            </select>
          </label>

          <label className="ep-alloc-field">
            <span className="lbl">Item Line (proses Cetak)</span>
            <select
              value={itemId}
              onChange={(e) => {
                setItemId(e.target.value);
                setError(null);
              }}
              aria-label="Pilih item line"
            >
              <option value="">Pilih item…</option>
              {poItems.map((it) => (
                <option key={it.id} value={it.id}>
                  {it.itemName} · Qty {formatQty(it.qtyPo)}
                </option>
              ))}
            </select>
          </label>

          <div className="ep-alloc-meta">
            <div>
              <div className="lbl">Qty Item PO</div>
              <strong>
                {selectedItem ? formatQty(selectedItem.qtyPo) : "—"}
              </strong>
            </div>
            <div>
              <div className="lbl">Sudah Dijadwalkan</div>
              <strong>{selectedItem ? formatQty(scheduled) : "—"}</strong>
            </div>
            <div>
              <div className="lbl">Sisa Qty</div>
              <strong>{selectedItem ? formatQty(remaining) : "—"}</strong>
            </div>
            <div>
              <div className="lbl">Proses</div>
              <strong>{selectedItem?.process ?? "—"}</strong>
            </div>
          </div>

          <label className="ep-alloc-field">
            <span className="lbl">Qty Dijadwalkan</span>
            <input
              type="number"
              min={1}
              step={1}
              inputMode="numeric"
              value={qtyText}
              onChange={(e) => {
                setQtyText(e.target.value);
                setError(null);
              }}
              placeholder="Masukkan qty…"
              aria-label="Qty dijadwalkan"
            />
          </label>

          <label className="ep-alloc-field">
            <span className="lbl">Screen</span>
            <select
              value={screen}
              onChange={(e) => {
                setScreen(e.target.value as DemoScreenOption | "");
                setError(null);
              }}
              aria-label="Pilih screen"
            >
              <option value="">Pilih screen…</option>
              {DEMO_SCREEN_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>

          <label className="ep-alloc-field">
            <span className="lbl">Tanggal Pengerjaan</span>
            <input
              type="date"
              value={workDate}
              onChange={(e) => {
                setWorkDate(e.target.value);
                setError(null);
              }}
              aria-label="Tanggal pengerjaan"
            />
            {workDate ? (
              <span className="ep-alloc-date-hint">
                Tampil: {formatDateDisplay(workDate)}
              </span>
            ) : null}
          </label>

          <div className="ep-alloc-design">
            <div>
              <div className="lbl">Tanggal Design Approval</div>
              <strong>
                {selectedItem?.designApprovalDate
                  ? formatDateDisplay(selectedItem.designApprovalDate)
                  : "—"}
              </strong>
            </div>
            <button
              type="button"
              className="ep-btn sm"
              onClick={() => setPdfOpen(true)}
            >
              Buka Design
            </button>
          </div>

          {itemAllocs.length > 0 ? (
            <div className="ep-alloc-list">
              <div className="lbl">Alokasi yang sudah dibuat</div>
              <ul>
                {itemAllocs.map((a) => (
                  <li key={a.id}>
                    <span>{a.operatorName}</span>
                    <span>{formatQty(a.qty)}</span>
                    <span>{a.screen}</span>
                    <span>{formatDateDisplay(a.workDate)}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {error ? (
            <div className="ep-alloc-error" role="alert">
              {error}
            </div>
          ) : null}
        </div>

        <div className="ep-modal-foot">
          <span className="ep-po-confirm-hint">Aksi DEMO · tidak permanen</span>
          <div className="ep-modal-foot-actions">
            <button type="button" className="ep-btn" onClick={onClose}>
              Batal
            </button>
            <button type="button" className="ep-btn primary" onClick={onSave}>
              Simpan Alokasi (Demo)
            </button>
          </div>
        </div>

        <ConfirmationSuccessOverlay
          open={successOpen}
          title="Alokasi Operator Berhasil"
          message="Pembagian operator tersimpan pada simulasi frontend"
          demo
          onClose={() => setSuccessOpen(false)}
        />
      </div>

      <PdfViewerModal
        open={pdfOpen}
        title={`Design · ${row.poNo}`}
        subtitle={selectedItem?.itemName ?? row.itemName}
        src={selectedItem?.pdfUrl ?? null}
        onClose={() => setPdfOpen(false)}
      />
    </div>
  );
}
