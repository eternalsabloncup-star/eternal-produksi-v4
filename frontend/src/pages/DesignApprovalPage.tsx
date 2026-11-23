import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  fetchPublicApproval,
  submitPublicApproval,
  submitPublicRejection,
  type PublicApprovalContext,
} from "../api/scheduling";
import { ConfirmationSuccessOverlay } from "../components/ConfirmationSuccessOverlay";
import { PublicDesignEvidencePreview } from "../components/PublicDesignEvidencePreview";
import {
  SIGNATURE_CANVAS_HEIGHT,
  canvasPointerPos,
  syncSignatureCanvasSize,
} from "../components/signatureCanvas";
import "./publicDesignApproval.css";

function canvasToBase64Png(canvas: HTMLCanvasElement): string {
  const dataUrl = canvas.toDataURL("image/png");
  return dataUrl.split(",")[1] || "";
}

/** Halaman approval desain untuk customer — tanpa sesi internal V4. */
export function DesignApprovalPage() {
  const { token = "" } = useParams();
  const [context, setContext] = useState<PublicApprovalContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [signerName, setSignerName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [rejected, setRejected] = useState(false);
  const [rejectReasonSaved, setRejectReasonSaved] = useState<string | null>(null);
  const [hasSignature, setHasSignature] = useState(false);
  const [showReject, setShowReject] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [feedback, setFeedback] = useState<{
    tone: "success" | "error";
    title: string;
    message: string;
  } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const signatureWrapRef = useRef<HTMLDivElement | null>(null);
  const drawing = useRef(false);

  const syncCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = signatureWrapRef.current;
    if (!canvas || !wrap) return;
    syncSignatureCanvasSize(canvas, wrap.clientWidth, SIGNATURE_CANVAS_HEIGHT);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPublicApproval(token);
      setContext(data);
      if (data.already_approved || data.decision === "approved") {
        setDone(true);
        setRejected(false);
      } else if (data.decision === "rejected") {
        setRejected(true);
        setDone(false);
        setRejectReasonSaved(data.reject_reason || null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Link tidak valid.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  useLayoutEffect(() => {
    if (done || rejected || loading || !context) return;
    syncCanvas();
    const wrap = signatureWrapRef.current;
    if (!wrap || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => syncCanvas());
    ro.observe(wrap);
    return () => ro.disconnect();
  }, [done, rejected, loading, context, syncCanvas]);

  function pos(e: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    return canvasPointerPos(canvas, e.clientX, e.clientY);
  }

  function startDraw(e: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    drawing.current = true;
    canvas.setPointerCapture(e.pointerId);
    const { x, y } = pos(e);
    ctx.beginPath();
    ctx.moveTo(x, y);
  }

  function moveDraw(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawing.current) return;
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    const { x, y } = pos(e);
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.strokeStyle = "#17341f";
    ctx.lineTo(x, y);
    ctx.stroke();
    setHasSignature(true);
  }

  function endDraw(e?: React.PointerEvent<HTMLCanvasElement>) {
    drawing.current = false;
    const canvas = canvasRef.current;
    if (canvas && e && canvas.hasPointerCapture(e.pointerId)) {
      canvas.releasePointerCapture(e.pointerId);
    }
  }

  function clearSignature() {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    setHasSignature(false);
  }

  async function onApprove() {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (!signerName.trim()) {
      setError("Nama penanda tangan wajib diisi.");
      return;
    }
    if (!hasSignature) {
      setError("Tanda tangan digital wajib diisi.");
      return;
    }
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    setFeedback(null);
    try {
      await submitPublicApproval(token, signerName.trim(), canvasToBase64Png(canvas));
      setDone(true);
      setRejected(false);
      setFeedback({
        tone: "success",
        title: "Approval tersimpan",
        message: "Terima kasih. Approval desain sudah tercatat.",
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Approval gagal.";
      setError(message);
      setFeedback({ tone: "error", title: "Gagal", message });
    } finally {
      setSubmitting(false);
    }
  }

  async function onReject() {
    const reason = rejectReason.trim();
    if (reason.length < 3) {
      setError("Alasan penolakan wajib diisi (minimal 3 karakter).");
      return;
    }
    if (
      !window.confirm(
        "Tolak desain ini? Keputusan ini tidak dapat dibatalkan untuk versi ini. Desainer perlu mengunggah revisi.",
      )
    ) {
      return;
    }
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    setFeedback(null);
    try {
      const res = await submitPublicRejection(token, reason);
      setRejected(true);
      setDone(false);
      setShowReject(false);
      setRejectReasonSaved(res.reject_reason || reason);
      setFeedback({
        tone: "success",
        title: res.duplicate ? "Sudah ditolak" : "Desain ditolak",
        message: "Revisi diperlukan. Tim desain akan mengunggah versi baru untuk approval ulang.",
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Penolakan gagal.";
      setError(message);
      setFeedback({ tone: "error", title: "Gagal", message });
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="page center ep-public-approval">
        <p className="muted">Memuat approval desain…</p>
      </div>
    );
  }

  if (!context) {
    return (
      <div className="page center ep-public-approval">
        <h3>Link approval tidak valid</h3>
        <p className="muted">{error || "Link sudah kedaluwarsa atau dicabut."}</p>
      </div>
    );
  }

  const canDecide = !done && !rejected;

  return (
    <div className="page ep-public-approval" data-testid="design-approval-page">
      <h2>Approval Desain</h2>
      <p className="muted ep-public-meta">
        {context.po_no} · {context.customer} · desain versi {context.design_version}
      </p>

      <table className="ep-table">
        <thead>
          <tr>
            <th>Nama Barang</th>
            <th>Kode</th>
            <th>Qty</th>
            <th>Warna</th>
          </tr>
        </thead>
        <tbody>
          {context.items.map((i) => (
            <tr key={`${i.item_code}-${i.item_name}`}>
              <td>{i.item_name}</td>
              <td>{i.item_code}</td>
              <td>
                {i.qty} {i.unit}
              </td>
              <td>{i.color}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="ep-public-file" data-testid="design-file-meta">
        <strong>File desain</strong>
        <span className="ep-public-meta">
          {context.design_filename || "Nama file tidak tersedia"} · versi{" "}
          {context.design_version}
          {context.design_content_type ? ` · ${context.design_content_type}` : ""}
        </span>
        <div className="ep-public-actions">
          <button
            type="button"
            className="ep-btn"
            data-testid="design-preview-open"
            disabled={!context.preview_supported}
            title={
              context.preview_supported
                ? "Preview di dalam aplikasi"
                : "Preview tidak tersedia untuk tipe file ini"
            }
            onClick={() => setPreviewOpen(true)}
          >
            Preview Desain
          </button>
        </div>
      </div>

      {done ? (
        <p className="ep-notice" data-testid="approval-done">
          Terima kasih. Approval desain sudah tercatat.
        </p>
      ) : null}

      {rejected ? (
        <p className="ep-notice is-rejected" data-testid="approval-rejected">
          Desain ditolak — Revisi Diperlukan.
          {rejectReasonSaved ? ` Alasan: ${rejectReasonSaved}` : ""}
        </p>
      ) : null}

      {canDecide ? (
        <div className="ep-approval-form">
          <label htmlFor="signer">Nama penanda tangan</label>
          <input
            id="signer"
            value={signerName}
            onChange={(e) => setSignerName(e.target.value)}
            disabled={submitting}
          />

          <div
            className="ep-signature-wrap"
            ref={signatureWrapRef}
            data-testid="approval-signature-wrap"
          >
            <p className="ep-signature-label" id="approval-signature-label">
              Tanda tangan
            </p>
            <canvas
              ref={canvasRef}
              width={420}
              height={SIGNATURE_CANVAS_HEIGHT}
              className="ep-signature"
              data-testid="signature-canvas"
              aria-labelledby="approval-signature-label"
              onPointerDown={startDraw}
              onPointerMove={moveDraw}
              onPointerUp={endDraw}
              onPointerCancel={endDraw}
              onPointerLeave={endDraw}
            />
          </div>
          <div className="ep-filterbar ep-public-actions">
            <button
              type="button"
              className="ep-btn"
              disabled={submitting}
              onClick={clearSignature}
            >
              Hapus tanda tangan
            </button>
            <button
              type="button"
              className={`ep-btn primary${submitting ? " is-busy" : ""}`}
              disabled={submitting}
              aria-busy={submitting}
              onClick={() => void onApprove()}
            >
              {submitting ? "Mengirim…" : "Konfirmasi Approval"}
            </button>
            <button
              type="button"
              className="ep-btn is-danger-outline"
              data-testid="design-reject-open"
              disabled={submitting}
              onClick={() => {
                setShowReject(true);
                setError(null);
              }}
            >
              Tolak Desain
            </button>
          </div>

          {showReject ? (
            <div className="ep-reject-panel" data-testid="design-reject-panel">
              <label htmlFor="reject-reason">Alasan penolakan (wajib)</label>
              <textarea
                id="reject-reason"
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                disabled={submitting}
                maxLength={500}
                placeholder="Contoh: warna tidak sesuai brief"
              />
              <div className="ep-public-actions">
                <button
                  type="button"
                  className={`ep-btn is-danger-outline${submitting ? " is-busy" : ""}`}
                  data-testid="design-reject-submit"
                  disabled={submitting}
                  aria-busy={submitting}
                  onClick={() => void onReject()}
                >
                  {submitting ? "Mengirim…" : "Kirim Penolakan"}
                </button>
                <button
                  type="button"
                  className="ep-btn"
                  disabled={submitting}
                  onClick={() => setShowReject(false)}
                >
                  Batal
                </button>
              </div>
            </div>
          ) : null}

          {error ? (
            <p className="ep-error" role="alert">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}

      <PublicDesignEvidencePreview
        open={previewOpen}
        token={token}
        title="Preview desain"
        filename={context.design_filename}
        contentType={context.design_content_type}
        onClose={() => setPreviewOpen(false)}
      />

      <ConfirmationSuccessOverlay
        open={Boolean(feedback)}
        global
        tone={feedback?.tone || "success"}
        title={feedback?.title || ""}
        message={feedback?.message || ""}
        autoCloseMs={feedback?.tone === "success" ? 1800 : 2600}
        onClose={() => setFeedback(null)}
      />
    </div>
  );
}
