import { useEffect, useState } from "react";
import { acceptPo, PoAcceptError } from "../api/poAcceptance";
import { useAuth } from "../auth";
import type { PoRow } from "../demo/types";
import { formatDateDisplay } from "../lib/formatDate";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";

function formatQty(n: number): string {
  return n.toLocaleString("id-ID");
}

function canAcceptPoRole(role: string | undefined, operationalRole: string | null | undefined): boolean {
  if (role === "super_admin" || role === "admin") return true;
  return operationalRole === "production-head";
}

type Props = {
  row: PoRow | null;
  open: boolean;
  onClose: () => void;
  /** Dipanggil setelah backend mengonfirmasi penerimaan (bukan sebelum API). */
  onAccepted?: (row: PoRow) => void;
};

type AcceptPhase = "idle" | "loading" | "success" | "error";

/** Popup rincian PO Inbox + gate Terima PO (backend). */
export function InboxPoDetailModal({ row, open, onClose, onAccepted }: Props) {
  const { user } = useAuth();
  const [phase, setPhase] = useState<AcceptPhase>("idle");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successOpen, setSuccessOpen] = useState(false);
  const [localRow, setLocalRow] = useState<PoRow | null>(row);

  useEffect(() => {
    setLocalRow(row);
    setPhase("idle");
    setErrorMsg(null);
    setSuccessOpen(false);
  }, [open, row?.id, row?.accurateId, row?.acceptanceStatus]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (successOpen) return;
      onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, successOpen]);

  if (!open || !localRow) return null;

  const accepted = localRow.acceptanceStatus === "accepted";
  const mayAccept = canAcceptPoRole(user?.role, user?.operational_role);
  const showAccept =
    localRow.origin === "accurate" && Boolean(localRow.accurateId) && mayAccept;
  const acceptDisabled =
    phase === "loading" || phase === "success" || accepted || !mayAccept;

  const orderStatus = localRow.urgent ? "Urgent" : "Normal";
  const fields: [string, string][] = [
    ["Nomor PO", localRow.poNo || localRow.id],
    ["Tanggal PO", formatDateDisplay(localRow.poDate)],
    ["Pelanggan", localRow.customer || "—"],
    ["Nama Penerima", localRow.recipient || "—"],
    ["PIC Alamat", localRow.addressPicName || "—"],
    ["Alamat", localRow.address || "—"],
    ["Syarat Pembayaran", localRow.paymentTerms || "—"],
    ["User Pembuat", localRow.userName || "—"],
    ["User Cetak", localRow.printUserName || "—"],
    ["ID Pembuat", localRow.createdById || "—"],
    ["Ekspedisi", localRow.expedition || "—"],
    ["Estimasi Pengiriman", formatDateDisplay(localRow.etaShip)],
    ["Zona Pengiriman", localRow.shipZone || "—"],
    ["Penjual", localRow.seller || "—"],
    ["Prioritas", localRow.priorityLabel || "—"],
    ["Status Pesanan", orderStatus],
    [
      "Status Inbox",
      accepted ? "PO Diterima" : localRow.status || "PO Baru",
    ],
    ["Buffer/Non-Buffer", localRow.bufferLabel || "—"],
    ["Keterangan", localRow.notes || "—"],
    ["Jumlah Item", String(localRow.itemCount ?? localRow.items?.length ?? 0)],
    ["Total Kuantitas", localRow.totalQtyDisplay || "—"],
    ["Asal Data", localRow.origin === "accurate" ? "Accurate" : "DEMO"],
  ];

  const items = localRow.items || [];

  async function onTerimaPo() {
    if (acceptDisabled || !localRow?.accurateId) return;
    setPhase("loading");
    setErrorMsg(null);
    try {
      const res = await acceptPo(localRow.accurateId);
      const next: PoRow = {
        ...localRow,
        acceptanceStatus: res.acceptance.status || "accepted",
        acceptedBy: res.acceptance.accepted_by ?? user?.email ?? null,
        acceptedAt: res.acceptance.accepted_at
          ? String(res.acceptance.accepted_at)
          : null,
        status: res.acceptance.inbox_status || "PO Diterima",
      };
      setLocalRow(next);
      setPhase("success");
      setSuccessOpen(true);
      onAccepted?.(next);
    } catch (err) {
      setPhase("error");
      setErrorMsg(
        err instanceof PoAcceptError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Gagal menerima PO.",
      );
    }
  }

  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="ep-modal"
        role="dialog"
        aria-modal="true"
        aria-label={
          accepted ? "PO Berhasil Diterima" : `Rincian ${localRow.id}`
        }
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            <div className="ep-modal-kicker">
              Rincian PO · {localRow.origin === "accurate" ? "Accurate" : "DEMO"}
            </div>
            <h2>{localRow.id}</h2>
            <div className="ep-sub">
              {localRow.customer}
              <span className="ep-flow-meta-sep" aria-hidden>
                ·
              </span>
              <span
                className={`ep-pill ep-pill-status ${localRow.urgent ? "order-urgent" : "order-normal"}`}
              >
                {orderStatus}
              </span>
              {accepted ? (
                <>
                  <span className="ep-flow-meta-sep" aria-hidden>
                    ·
                  </span>
                  <span className="ep-pill ep-pill-status order-normal">
                    PO Diterima
                  </span>
                </>
              ) : null}
            </div>
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>

        <div className="ep-modal-body">
          <div className="ep-po-info-grid">
            {fields.map(([label, value]) => (
              <div key={label} className="ep-po-info-cell">
                <div className="lbl">{label}</div>
                <strong>{value}</strong>
              </div>
            ))}
          </div>

          <div className="ep-sectionlabel">Item line</div>
          <div className="ep-po-detail-scroll ep-po-modal-items">
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
                      <div className="ep-empty">Tidak ada item.</div>
                    </td>
                  </tr>
                ) : (
                  items.map((it) => (
                    <tr key={it.lineId || `${localRow.sourcePoId}:${it.code}`}>
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

        <div className="ep-modal-foot">
          <span className="ep-po-confirm-hint">
            {localRow.origin === "accurate"
              ? accepted
                ? "PO sudah diterima — workflow Eternal aktif"
                : "Accurate read-only · Terima PO mengaktifkan workflow Eternal"
              : "Sumber DEMO — Terima PO hanya untuk PO Accurate"}
          </span>
          <div className="ep-modal-foot-actions">
            <button type="button" className="ep-btn" onClick={onClose}>
              Tutup
            </button>
            {showAccept ? (
              <button
                type="button"
                className={`ep-btn primary${phase === "loading" ? " is-busy" : ""}`}
                disabled={acceptDisabled}
                aria-busy={phase === "loading"}
                onClick={() => void onTerimaPo()}
              >
                {phase === "loading" ? (
                  <>
                    <span className="ep-btn-spinner" aria-hidden />
                    Memproses...
                  </>
                ) : accepted || phase === "success" ? (
                  "PO Diterima"
                ) : (
                  "Terima PO"
                )}
              </button>
            ) : null}
          </div>
          {phase === "error" && errorMsg ? (
            <p className="ep-error" role="alert">
              {errorMsg}
            </p>
          ) : null}
        </div>

        <ConfirmationSuccessOverlay
          open={successOpen}
          title="PO berhasil diterima"
          message="Workflow produksi Eternal diaktifkan sesuai respons server."
          onClose={() => setSuccessOpen(false)}
        />
      </div>
    </div>
  );
}
