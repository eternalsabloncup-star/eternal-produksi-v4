import { useEffect, useRef, useState } from "react";
import {
  GlobalWorkerOptions,
  getDocument,
  type PDFDocumentProxy,
} from "pdfjs-dist/build/pdf.mjs";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";

/** Worker PDF.js — path eksplisit agar resolusi Vite stabil. */
GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

type Props = {
  open: boolean;
  title: string;
  subtitle?: string;
  /** URL lokal DEMO (public/). Kosong = empty state. */
  src: string | null;
  onClose: () => void;
};

/** Viewer PDF internal (PDF.js) — tanpa tab baru / iframe browser. */
export function PdfViewerModal({ open, title, subtitle, src, onClose }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1.15);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    docRef.current = null;
    setPage(1);
    setPageCount(0);
    setError(null);

    if (!src) {
      setLoading(false);
      return;
    }

    setLoading(true);
    (async () => {
      try {
        const task = getDocument(src);
        const doc = await task.promise;
        if (cancelled) {
          void doc.destroy();
          return;
        }
        docRef.current = doc;
        setPageCount(doc.numPages);
        setLoading(false);
      } catch {
        if (!cancelled) {
          setError("Gagal memuat PDF DEMO.");
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      const d = docRef.current;
      docRef.current = null;
      if (d) void d.destroy();
    };
  }, [open, src]);

  useEffect(() => {
    if (!open || !docRef.current || !canvasRef.current || pageCount < 1) return;
    let cancelled = false;
    (async () => {
      try {
        const pdfPage = await docRef.current!.getPage(page);
        if (cancelled) return;
        const viewport = pdfPage.getViewport({ scale });
        const canvas = canvasRef.current!;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        await pdfPage.render({ canvasContext: ctx, viewport }).promise;
      } catch {
        if (!cancelled) setError("Gagal merender halaman PDF.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, page, scale, pageCount, src]);

  if (!open) return null;

  return (
    <div
      className="ep-modalshade ep-modalshade-center ep-pdf-shade"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="ep-modal ep-pdf-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head ep-pdf-head">
          <div>
            <div className="ep-modal-kicker">Preview PDF · DEMO</div>
            <h2>{title}</h2>
            {subtitle ? <div className="ep-sub">{subtitle}</div> : null}
          </div>
          <button type="button" className="ep-x" onClick={onClose} aria-label="Tutup">
            ×
          </button>
        </div>

        <div className="ep-pdf-toolbar">
          <button
            type="button"
            className="ep-btn sm"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            ← Halaman
          </button>
          <span className="ep-pdf-page">
            {pageCount ? `${page} / ${pageCount}` : "—"}
          </span>
          <button
            type="button"
            className="ep-btn sm"
            disabled={!pageCount || page >= pageCount}
            onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
          >
            Halaman →
          </button>
          <span className="ep-pdf-sep" aria-hidden />
          <button
            type="button"
            className="ep-btn sm"
            onClick={() => setScale((s) => Math.max(0.6, Number((s - 0.15).toFixed(2))))}
          >
            Zoom −
          </button>
          <button
            type="button"
            className="ep-btn sm"
            onClick={() => setScale((s) => Math.min(2.4, Number((s + 0.15).toFixed(2))))}
          >
            Zoom +
          </button>
          <button type="button" className="ep-btn sm" onClick={onClose}>
            Tutup
          </button>
        </div>

        <div className="ep-modal-body ep-pdf-body">
          {!src ? (
            <div className="ep-empty">Dokumen PDF belum tersedia untuk baris ini (DEMO).</div>
          ) : loading ? (
            <div className="ep-empty">Memuat PDF…</div>
          ) : error ? (
            <div className="ep-empty">{error}</div>
          ) : (
            <div className="ep-pdf-canvas-wrap">
              <canvas ref={canvasRef} className="ep-pdf-canvas" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
