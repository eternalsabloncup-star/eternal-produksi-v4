import { useEffect, useRef, useState } from "react";
import {
  GlobalWorkerOptions,
  getDocument,
  type PDFDocumentProxy,
} from "pdfjs-dist/build/pdf.mjs";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.mjs?url";
import { publicDesignEvidenceUrl } from "../api/scheduling";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

type Props = {
  open: boolean;
  token: string;
  title: string;
  filename?: string | null;
  contentType?: string | null;
  designVersion?: number | null;
  onClose: () => void;
};

function isPdf(contentType: string | null | undefined, filename: string | null | undefined) {
  const mime = (contentType || "").split(";")[0].trim().toLowerCase();
  if (mime === "application/pdf") return true;
  return (filename || "").toLowerCase().endsWith(".pdf");
}

/**
 * In-app preview bukti desain publik (PDF.js / img) — tanpa tab baru.
 * Blob di-fetch lewat endpoint token-scoped; URL internal tidak di-expose ke user.
 */
export function PublicDesignEvidencePreview({
  open,
  token,
  title,
  filename,
  contentType,
  designVersion,
  onClose,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<PDFDocumentProxy | null>(null);
  const blobUrlRef = useRef<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [scale, setScale] = useState(1.1);
  const pdfMode = isPdf(contentType, filename);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setImageUrl(null);
    setPage(1);
    setPageCount(0);
    setScale(1.1);
    docRef.current = null;

    void (async () => {
      try {
        const res = await fetch(publicDesignEvidenceUrl(token), {
          method: "GET",
          credentials: "omit",
        });
        if (cancelled) return;
        if (!res.ok) {
          setError(
            res.status === 403
              ? "Tipe file tidak didukung atau akses ditolak."
              : res.status === 404
                ? "File desain tidak tersedia."
                : `Gagal memuat preview (HTTP ${res.status}).`,
          );
          setLoading(false);
          return;
        }
        const blob = await res.blob();
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        blobUrlRef.current = url;

        if (pdfMode) {
          const doc = await getDocument(url).promise;
          if (cancelled) {
            void doc.destroy();
            return;
          }
          docRef.current = doc;
          setPageCount(doc.numPages);
          setLoading(false);
        } else {
          setImageUrl(url);
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setError("Gagal memuat preview desain.");
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      const d = docRef.current;
      docRef.current = null;
      if (d) void d.destroy();
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [open, token, pdfMode]);

  useEffect(() => {
    if (!open || !pdfMode || !docRef.current || !canvasRef.current || pageCount < 1) {
      return;
    }
    let cancelled = false;
    void (async () => {
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
  }, [open, pdfMode, page, scale, pageCount]);

  async function fitToWidth() {
    if (!docRef.current || !frameRef.current) return;
    try {
      const pdfPage = await docRef.current.getPage(page);
      const base = pdfPage.getViewport({ scale: 1 });
      const avail = Math.max(120, frameRef.current.clientWidth - 24);
      const next = Math.min(2.4, Math.max(0.6, avail / base.width));
      setScale(Number(next.toFixed(2)));
    } catch {
      /* keep current scale */
    }
  }

  if (!open) return null;

  const versionLabel =
    designVersion != null ? ` · versi ${designVersion}` : "";

  return (
    <div
      className="ep-modalshade ep-modalshade-center ep-public-preview-shade"
      data-testid="public-design-preview"
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
            <h2>{title}</h2>
            <div className="ep-modal-submeta">
              {filename || "—"}
              {versionLabel}
            </div>
          </div>
          <button type="button" className="ep-btn sm" onClick={onClose}>
            Tutup
          </button>
        </div>
        {pdfMode ? (
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
            <button
              type="button"
              className="ep-btn sm"
              data-testid="design-preview-fit"
              disabled={!pageCount}
              onClick={() => void fitToWidth()}
            >
              Muat lebar
            </button>
          </div>
        ) : null}
        <div className="ep-modal-body">
          {loading ? <p className="ep-notice">Memuat preview…</p> : null}
          {error ? (
            <p className="ep-error" role="alert">
              {error}
            </p>
          ) : null}
          {!loading && !error ? (
            <div className="ep-public-preview-frame" ref={frameRef}>
              {pdfMode ? (
                <div className="ep-pdf-canvas-wrap">
                  <canvas ref={canvasRef} className="ep-pdf-canvas" />
                </div>
              ) : imageUrl ? (
                <img src={imageUrl} alt={filename || title} />
              ) : null}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
