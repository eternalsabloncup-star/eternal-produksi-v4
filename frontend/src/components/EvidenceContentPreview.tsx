import { useEffect, useState } from "react";

type Props = {
  title: string;
  contentUrl: string;
  filename?: string | null;
  onClose: () => void;
};

/**
 * Preview read-only bukti (image/PDF) via URL sesi yang di-scope backend.
 * Tidak membentuk path GridFS spekulatif.
 */
export function EvidenceContentPreview({
  title,
  contentUrl,
  filename,
  onClose,
}: Props) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const isPdf = (filename || contentUrl).toLowerCase().includes(".pdf");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setOk(false);
    void (async () => {
      try {
        const res = await fetch(contentUrl, {
          credentials: "include",
          method: "GET",
          headers: { Range: "bytes=0-0" },
        });
        if (cancelled) return;
        if (!res.ok && res.status !== 206) {
          setError(
            res.status === 403
              ? "Tidak berizin melihat bukti ini."
              : res.status === 404
                ? "Bukti tidak ditemukan atau sudah dihapus."
                : `Gagal memuat bukti (HTTP ${res.status}).`,
          );
          return;
        }
        setOk(true);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Gagal memuat bukti.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [contentUrl]);

  return (
    <div
      className="ep-modalshade ep-modalshade-center ep-evidence-preview-shade"
      data-testid="evidence-content-preview"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="ep-modal ep-evidence-preview-modal" role="dialog" aria-modal="true">
        <header className="ep-modal-head">
          <div>
            <h2>{title}</h2>
            <div className="ep-modal-submeta">{filename || "—"}</div>
          </div>
          <button type="button" className="ep-btn sm" onClick={onClose}>
            Tutup
          </button>
        </header>
        <div className="ep-modal-body">
          {loading ? <p className="ep-notice">Memuat bukti…</p> : null}
          {error ? (
            <p className="ep-error" role="alert">
              {error}
            </p>
          ) : null}
          {!loading && ok ? (
            <div className="ep-evidence-preview-frame">
              {isPdf ? (
                <iframe
                  title={title}
                  src={contentUrl}
                  className="ep-sched-design-embed"
                />
              ) : (
                <img
                  src={contentUrl}
                  alt={filename || title}
                  className="ep-sched-design-img"
                />
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
