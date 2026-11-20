import { useCallback, useEffect, useState } from "react";
import {
  bypassPaymentGate,
  fetchPaymentState,
  ingestPaymentReceipts,
  type PaymentStateResponse,
  WorkflowApiError,
} from "../api/workflow";
import { useAuth } from "../auth";

type Props = {
  accuratePoId: string;
  poNo?: string;
  onChanged?: () => void;
};

function gateLabel(state: string | undefined): string {
  if (state === "open") return "Terbuka";
  if (state === "review") return "Perlu Review";
  if (state === "closed") return "Tertutup";
  return state || "—";
}

/** Panel Payment API: ingest receipt snapshot + bypass berizin. */
export function PaymentWorkflowPanel({ accuratePoId, poNo, onChanged }: Props) {
  const { capabilities } = useAuth();
  // Canonical: operations.full OR explicit payment-bypass (backend capabilities).
  const canBypass = Boolean(capabilities.payment_bypass);
  const [state, setState] = useState<PaymentStateResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [receiptId, setReceiptId] = useState("");
  const [invoiceNo, setInvoiceNo] = useState("");
  const [amount, setAmount] = useState("");
  const [receiptDate, setReceiptDate] = useState("");
  const [bypassReason, setBypassReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setState(await fetchPaymentState(accuratePoId));
    } catch (err) {
      setState(null);
      setError(
        err instanceof WorkflowApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Gagal memuat status pembayaran.",
      );
    } finally {
      setLoading(false);
    }
  }, [accuratePoId]);

  useEffect(() => {
    void load();
    setNotice(null);
  }, [load]);

  async function onIngest() {
    const id = receiptId.trim();
    const value = Number(amount);
    if (!id) {
      setError("ID penerimaan Accurate wajib diisi.");
      return;
    }
    if (!Number.isFinite(value)) {
      setError("Nominal penerimaan tidak valid.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await ingestPaymentReceipts(accuratePoId, [
        {
          id,
          invoice_no: invoiceNo.trim() || null,
          amount: value,
          receipt_date: receiptDate.trim() || null,
        },
      ]);
      setNotice(
        `Penerimaan tersimpan · gate payment: ${gateLabel(res.gates?.payment?.state)}.`,
      );
      setReceiptId("");
      setInvoiceNo("");
      setAmount("");
      setReceiptDate("");
      await load();
      onChanged?.();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Gagal menyimpan penerimaan.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function onBypass() {
    const reason = bypassReason.trim();
    if (reason.length < 3) {
      setError("Alasan bypass wajib diisi (minimal 3 karakter).");
      return;
    }
    if (reason.length > 500) {
      setError("Alasan bypass maksimal 500 karakter.");
      return;
    }
    const confirmed = window.confirm(
      "Bypass Payment akan membuka gate pembayaran dan tercatat di audit. Lanjutkan?",
    );
    if (!confirmed) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const gates = await bypassPaymentGate(accuratePoId, reason);
      const label = gateLabel(
        gates?.payment && typeof gates.payment === "object"
          ? (gates.payment as { state?: string }).state
          : undefined,
      );
      setNotice(
        gates.duplicate
          ? `Sudah di-bypass · gate: ${label}.`
          : `Bypass disimpan · gate: ${label}.`,
      );
      setBypassReason("");
      await load();
      onChanged?.();
    } catch (err) {
      if (err instanceof WorkflowApiError && err.status === 403) {
        setError("Akses ditolak: tidak memiliki izin Bypass Payment (Master).");
      } else if (err instanceof WorkflowApiError && err.status === 409) {
        setError(err.message || "Gate payment sudah terbuka — bypass ditolak.");
        await load();
      } else {
        setError(err instanceof Error ? err.message : "Bypass ditolak.");
      }
    } finally {
      setBusy(false);
    }
  }

  const paymentGate = state?.gates?.payment;
  const isBypass = Boolean(
    paymentGate?.detail &&
      typeof paymentGate.detail === "object" &&
      (paymentGate.detail as { bypass?: boolean }).bypass,
  );

  return (
    <div className="ep-workflow-panel" data-testid="payment-workflow-panel">
      <p className="ep-sectionlabel">Payment · {poNo || accuratePoId}</p>

      {loading ? <p>Memuat status pembayaran…</p> : null}
      {error ? (
        <p className="ep-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? <p className="ep-notice">{notice}</p> : null}

      {!loading && state ? (
        <>
          <dl className="ep-kv">
            <dt>Gate Payment</dt>
            <dd>
              {gateLabel(paymentGate?.state)}
              {isBypass ? " · bypass" : ""}
            </dd>
            <dt>Alasan / detail</dt>
            <dd>{paymentGate?.reason || state.reconciliation?.reason || "—"}</dd>
            <dt>Rekonsiliasi</dt>
            <dd>
              {state.reconciliation?.decision || "—"}
              {state.reconciliation?.total_amount != null
                ? ` · total ${state.reconciliation.total_amount.toLocaleString("id-ID")}`
                : ""}
            </dd>
          </dl>

          {state.receipts.length > 0 ? (
            <div className="ep-tblscroll">
              <table className="ep-table">
                <thead>
                  <tr>
                    <th>ID Penerimaan</th>
                    <th>Faktur</th>
                    <th>Nominal</th>
                    <th>Tanggal</th>
                  </tr>
                </thead>
                <tbody>
                  {state.receipts.map((r) => (
                    <tr key={r.accurate_receipt_id}>
                      <td>{r.accurate_receipt_id}</td>
                      <td>{r.invoice_no || "—"}</td>
                      <td>
                        {r.amount != null
                          ? Number(r.amount).toLocaleString("id-ID")
                          : "—"}
                      </td>
                      <td>{r.receipt_date || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="ep-empty">Belum ada snapshot penerimaan untuk PO ini.</p>
          )}

          <p className="ep-sectionlabel">
            Catat penerimaan Accurate (snapshot manual)
          </p>
          <p className="ep-po-confirm-hint" data-testid="payment-manual-notice">
            Bukan sinkronisasi live Accurate. Petugas mengisi snapshot penerimaan
            secara manual; gate dan audit diputuskan backend.
          </p>
          <div className="ep-filterbar">
            <input
              aria-label="ID penerimaan Accurate"
              placeholder="ID penerimaan"
              value={receiptId}
              onChange={(e) => setReceiptId(e.target.value)}
            />
            <input
              aria-label="Nomor faktur"
              placeholder="No. faktur"
              value={invoiceNo}
              onChange={(e) => setInvoiceNo(e.target.value)}
            />
            <input
              aria-label="Nominal"
              type="number"
              placeholder="Nominal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
            <input
              aria-label="Tanggal penerimaan"
              placeholder="Tanggal (opsional)"
              value={receiptDate}
              onChange={(e) => setReceiptDate(e.target.value)}
            />
            <button
              type="button"
              className="ep-btn primary"
              disabled={busy}
              onClick={() => void onIngest()}
            >
              {busy ? "Menyimpan…" : "Simpan Penerimaan"}
            </button>
          </div>

          {canBypass ? (
            <>
              <p className="ep-sectionlabel">Bypass Payment (izin Master)</p>
              <div className="ep-filterbar">
                <input
                  aria-label="Alasan bypass"
                  placeholder="Alasan bypass wajib"
                  value={bypassReason}
                  onChange={(e) => setBypassReason(e.target.value)}
                />
                <button
                  type="button"
                  className="ep-btn"
                  disabled={busy || paymentGate?.state === "open"}
                  title={
                    paymentGate?.state === "open"
                      ? "Gate sudah terbuka"
                      : "Membuka gate dengan audit"
                  }
                  onClick={() => void onBypass()}
                >
                  Bypass Gate
                </button>
                <button
                  type="button"
                  className="ep-btn"
                  disabled={busy}
                  onClick={() => void load()}
                >
                  Muat ulang
                </button>
              </div>
            </>
          ) : (
            <div className="ep-filterbar">
              <button type="button" className="ep-btn" disabled={busy} onClick={() => void load()}>
                Muat ulang
              </button>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
