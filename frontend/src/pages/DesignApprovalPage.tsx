import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  fetchPublicApproval,
  submitPublicApproval,
  type PublicApprovalContext,
} from "../api/scheduling";
import { ConfirmationSuccessOverlay } from "../components/ConfirmationSuccessOverlay";

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
  const [hasSignature, setHasSignature] = useState(false);
  const [feedback, setFeedback] = useState<{
    tone: "success" | "error";
    title: string;
    message: string;
  } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const drawing = useRef(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchPublicApproval(token);
      setContext(data);
      if (data.already_approved) setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Link tidak valid.");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  function pos(e: React.PointerEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const rect = canvas.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function startDraw(e: React.PointerEvent<HTMLCanvasElement>) {
    const ctx = canvasRef.current?.getContext("2d");
    if (!ctx) return;
    drawing.current = true;
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

  function endDraw() {
    drawing.current = false;
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

  if (loading) {
    return (
      <div className="page center">
        <p className="muted">Memuat approval desain…</p>
      </div>
    );
  }

  if (!context) {
    return (
      <div className="page center">
        <h3>Link approval tidak valid</h3>
        <p className="muted">{error || "Link sudah kedaluwarsa atau dicabut."}</p>
      </div>
    );
  }

  return (
    <div className="page" data-testid="design-approval-page">
      <h2>Approval Desain</h2>
      <p className="muted">
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

      {done ? (
        <p className="ep-notice" data-testid="approval-done">
          Terima kasih. Approval desain sudah tercatat.
        </p>
      ) : (
        <div className="ep-approval-form">
          <label htmlFor="signer">Nama penanda tangan</label>
          <input
            id="signer"
            value={signerName}
            onChange={(e) => setSignerName(e.target.value)}
          />

          <p className="ep-sectionlabel">Tanda tangan</p>
          <canvas
            ref={canvasRef}
            width={420}
            height={160}
            className="ep-signature"
            data-testid="signature-canvas"
            onPointerDown={startDraw}
            onPointerMove={moveDraw}
            onPointerUp={endDraw}
            onPointerLeave={endDraw}
          />
          <div className="ep-filterbar">
            <button type="button" className="ep-btn" onClick={clearSignature}>
              Hapus tanda tangan
            </button>
            <button
              type="button"
              className="ep-btn primary"
              disabled={submitting}
              onClick={() => void onApprove()}
            >
              {submitting ? "Mengirim…" : "Konfirmasi Approval"}
            </button>
          </div>
          {error ? <p className="ep-error">{error}</p> : null}
        </div>
      )}

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
