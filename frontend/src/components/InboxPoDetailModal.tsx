import { useEffect, useState } from "react";
import {
  acceptPo,
  fetchPoAcceptanceStatus,
  PoAcceptError,
} from "../api/poAcceptance";
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

export type TerimaPoFeedback = {
  tone: "success" | "error";
  title: string;
  message: string;
};

/** Copy overlay sukses — hanya setelah acceptPo resolve. */
export const TERIMA_PO_SUCCESS_FEEDBACK: TerimaPoFeedback = {
  tone: "success",
  title: "PO berhasil diterima",
  message: "PO berhasil diterima.",
};

/** Timeout/network uncertain — bukan sukses; minta refresh status. */
export const TERIMA_PO_UNCERTAIN_FEEDBACK: TerimaPoFeedback = {
  tone: "error",
  title: "Status penerimaan belum dapat dipastikan",
  message:
    "Status penerimaan belum dapat dipastikan. Muat ulang status sebelum mencoba lagi — permintaan tidak dikirim ulang otomatis.",
};

/** Pesan error aman untuk overlay Terima PO (tanpa traceback/internal). */
export function terimaPoFeedbackMessage(err: unknown): string {
  if (err instanceof PoAcceptError) {
    if (err.uncertain) {
      return TERIMA_PO_UNCERTAIN_FEEDBACK.message;
    }
    const msg = err.message.trim();
    return msg || "Gagal menerima PO. Silakan coba lagi.";
  }
  if (err instanceof Error) {
    const msg = err.message.trim();
    if (msg) return msg;
  }
  return "Gagal menerima PO. Silakan coba lagi.";
}

export function terimaPoErrorFeedback(err: unknown): TerimaPoFeedback {
  if (err instanceof PoAcceptError && err.uncertain) {
    return { ...TERIMA_PO_UNCERTAIN_FEEDBACK };
  }
  return {
    tone: "error",
    title: "Gagal menerima PO",
    message: terimaPoFeedbackMessage(err),
  };
}

export function notifyPoAccepted(accuratePoId: string): void {
  window.dispatchEvent(
    new CustomEvent("eternal:po-accepted", {
      detail: { accuratePoId },
    }),
  );
}

/** Sama dengan Live Tracking: success 1800ms, error 2600ms. */
export function terimaPoOverlayAutoCloseMs(tone: "success" | "error"): number {
  return tone === "success" ? 1800 : 2600;
}

export function isTerimaPoAcceptDisabled(
  phase: AcceptPhase,
  accepted: boolean,
  mayAccept: boolean,
): boolean {
  return phase === "loading" || phase === "success" || accepted || !mayAccept;
}

/** Popup rincian PO Inbox + gate Terima PO (backend). */
export function InboxPoDetailModal({ row, open, onClose, onAccepted }: Props) {
  const { user } = useAuth();
  const [phase, setPhase] = useState<AcceptPhase>("idle");
  const [feedback, setFeedback] = useState<TerimaPoFeedback | null>(null);
  const [localRow, setLocalRow] = useState<PoRow | null>(row);

  useEffect(() => {
    setLocalRow(row);
    setPhase("idle");
    setFeedback(null);
  }, [open, row?.id, row?.accurateId, row?.acceptanceStatus]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (feedback) return;
      onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, feedback]);

  if (!open || !localRow) return null;

  const accepted = localRow.acceptanceStatus === "accepted";
  const mayAccept = canAcceptPoRole(user?.role, user?.operational_role);
  const showAccept =
    localRow.origin === "accurate" && Boolean(localRow.accurateId) && mayAccept;
  const acceptDisabled = isTerimaPoAcceptDisabled(phase, accepted, mayAccept);

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

  function applyAccepted(
    acceptance: {
      status?: string;
      accepted_by?: string | null;
      accepted_at?: string | null;
      inbox_status?: string;
    },
  ) {
    if (!localRow) return;
    const next: PoRow = {
      ...localRow,
      acceptanceStatus: acceptance.status || "accepted",
      acceptedBy: acceptance.accepted_by ?? user?.email ?? null,
      acceptedAt: acceptance.accepted_at
        ? String(acceptance.accepted_at)
        : null,
      status: acceptance.inbox_status || "PO Diterima",
    };
    setLocalRow(next);
    setPhase("success");
    setFeedback(TERIMA_PO_SUCCESS_FEEDBACK);
    onAccepted?.(next);
    if (next.accurateId) notifyPoAccepted(next.accurateId);
  }

  async function refreshAcceptanceStatus() {
    if (!localRow?.accurateId || phase === "loading") return;
    setPhase("loading");
    setFeedback(null);
    const status = await fetchPoAcceptanceStatus(localRow.accurateId);
    if (status?.status === "accepted") {
      applyAccepted(status);
      return;
    }
    setPhase("idle");
    setFeedback({
      ...TERIMA_PO_UNCERTAIN_FEEDBACK,
      message:
        status == null
          ? "Status penerimaan belum dapat dipastikan. Coba muat ulang status lagi."
          : "PO belum diterima di server. Anda dapat mencoba Terima PO lagi.",
    });
  }

  async function onTerimaPo() {
    if (acceptDisabled || !localRow?.accurateId) return;
    setPhase("loading");
    setFeedback(null);
    try {
      const res = await acceptPo(localRow.accurateId);
      applyAccepted(res.acceptance);
    } catch (err) {
      // Timeout/network: jangan auto-retry accept; reconcile GET status.
      if (err instanceof PoAcceptError && err.uncertain) {
        const status = await fetchPoAcceptanceStatus(localRow.accurateId);
        if (status?.status === "accepted") {
          applyAccepted(status);
          return;
        }
        setPhase("error");
        setFeedback(TERIMA_PO_UNCERTAIN_FEEDBACK);
        return;
      }
      setPhase("error");
      // Status PO tidak diubah; onAccepted tidak dipanggil.
      setFeedback(terimaPoErrorFeedback(err));
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
            {showAccept &&
            phase === "error" &&
            feedback?.title === TERIMA_PO_UNCERTAIN_FEEDBACK.title ? (
              <button
                type="button"
                className="ep-btn"
                onClick={() => void refreshAcceptanceStatus()}
              >
                Muat ulang status
              </button>
            ) : null}
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
        </div>

        <ConfirmationSuccessOverlay
          open={Boolean(feedback)}
          global
          tone={feedback?.tone || "success"}
          title={feedback?.title || ""}
          message={feedback?.message || ""}
          autoCloseMs={
            feedback ? terimaPoOverlayAutoCloseMs(feedback.tone) : 0
          }
          onClose={() => setFeedback(null)}
        />
      </div>
    </div>
  );
}
