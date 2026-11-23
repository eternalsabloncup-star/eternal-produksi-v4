import { useState } from "react";
import {
  fetchInternalTracking,
  formatTs,
  generateTrackingLink,
  regenerateTrackingLink,
  revokeTrackingLink,
  type GenerateLinkResponse,
  type TrackingLinkRow,
  type TrackingPayload,
} from "../../api/tracking";
import { ConfirmationSuccessOverlay } from "../../components/ConfirmationSuccessOverlay";
import "../../styles/tracking.css";

type Feedback = {
  tone: "success" | "error";
  title: string;
  message: string;
};

/** Route kompatibilitas /app/tracking — akses utama via kolom Inbox PO. */
export function TrackingAdminPage() {
  const [poKey, setPoKey] = useState("");
  const [payload, setPayload] = useState<TrackingPayload | null>(null);
  const [links, setLinks] = useState<TrackingLinkRow[]>([]);
  const [lastGenerated, setLastGenerated] = useState<GenerateLinkResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const accurateId = payload?.order.accurate_po_id || poKey.trim();

  function showOk(title: string, message: string) {
    setFeedback({ tone: "success", title, message });
  }

  function showFail(message: string) {
    setError(message);
    setFeedback({ tone: "error", title: "Gagal", message });
  }

  async function load(id: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      const data = await fetchInternalTracking(id);
      setPayload(data);
      setLinks(data.links || []);
    } catch (err) {
      showFail(err instanceof Error ? err.message : "Gagal memuat");
      setPayload(null);
      setLinks([]);
    } finally {
      setBusy(false);
    }
  }

  async function onGenerate() {
    if (!accurateId || busy) return;
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      const gen = await generateTrackingLink(accurateId);
      setLastGenerated(gen);
      showOk("Link dibuat", "Link Live Tracking baru siap. Salin sebelum menutup halaman.");
      try {
        await loadQuiet(accurateId);
      } catch {
        /* generate sudah sukses — refresh list gagal tidak membatalkan link */
      }
    } catch (err) {
      showFail(err instanceof Error ? err.message : "Gagal generate");
    } finally {
      setBusy(false);
    }
  }

  async function onRegenerate() {
    if (!accurateId || busy) return;
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      const gen = await regenerateTrackingLink(accurateId);
      setLastGenerated(gen);
      showOk("Link diganti", "Link aktif sebelumnya dicabut. Salin link baru sekarang.");
      try {
        await loadQuiet(accurateId);
      } catch {
        /* regenerate sudah sukses — refresh list gagal tidak membatalkan link */
      }
    } catch (err) {
      showFail(err instanceof Error ? err.message : "Gagal regenerate");
    } finally {
      setBusy(false);
    }
  }

  /** Reload tanpa menggeser busy flag induk (sudah dipegang caller). */
  async function loadQuiet(id: string) {
    const data = await fetchInternalTracking(id);
    setPayload(data);
    setLinks(data.links || []);
  }

  async function onRevoke(linkId: string) {
    if (busy) return;
    setBusy(true);
    setError(null);
    setFeedback(null);
    try {
      await revokeTrackingLink(linkId, "manual revoke");
      showOk("Link dicabut", "Link tidak lagi dapat dibuka pelanggan.");
      if (accurateId) {
        try {
          await loadQuiet(accurateId);
        } catch {
          /* revoke sudah sukses */
        }
      }
    } catch (err) {
      showFail(err instanceof Error ? err.message : "Gagal revoke");
    } finally {
      setBusy(false);
    }
  }

  async function copyPath(path: string) {
    if (busy) return;
    const absolute = `${window.location.origin}${path}`;
    try {
      await navigator.clipboard.writeText(absolute);
      setCopied(absolute);
      showOk("Disalin", "URL Live Tracking ada di clipboard.");
      window.setTimeout(() => setCopied(null), 2000);
    } catch {
      setCopied(absolute);
      showFail("Clipboard gagal — salin URL secara manual dari kartu link.");
    }
  }

  return (
    <div className="elt-admin" data-testid="tracking-admin-page">
      <h1>Live Tracking</h1>
      <p className="muted" style={{ marginTop: 0, color: "var(--elt-muted)" }}>
        Halaman kompatibilitas. Manajemen utama ada di kolom{" "}
        <strong>Live Tracking</strong> pada Inbox PO. Token acak; slug hanya
        label URL.
      </p>

      <div className="elt-admin-toolbar">
        <input
          value={poKey}
          onChange={(e) => setPoKey(e.target.value)}
          placeholder="Accurate PO ID atau nomor PO"
          aria-label="PO ID"
          disabled={busy}
        />
        <button
          type="button"
          disabled={busy || !poKey.trim()}
          aria-busy={busy}
          onClick={() => void load(poKey.trim())}
        >
          {busy ? "Memuat…" : "Lihat"}
        </button>
        <button
          type="button"
          disabled={busy || !accurateId}
          aria-busy={busy}
          onClick={() => void onGenerate()}
        >
          Generate
        </button>
        <button
          type="button"
          disabled={busy || !accurateId}
          aria-busy={busy}
          onClick={() => void onRegenerate()}
        >
          Regenerate
        </button>
      </div>

      {error ? (
        <p style={{ color: "var(--elt-danger)" }} role="alert">
          {error}
        </p>
      ) : null}
      {copied ? (
        <p style={{ color: "var(--elt-ok)" }} role="status">
          Disalin: {copied}
        </p>
      ) : null}

      {lastGenerated ? (
        <div className="elt-link-card">
          <strong>Link baru (tampil sekali)</strong>
          <p style={{ wordBreak: "break-all", margin: "6px 0" }}>
            {window.location.origin}
            {lastGenerated.path}
          </p>
          <div className="elt-link-actions">
            <button
              type="button"
              className="is-ghost"
              disabled={busy}
              onClick={() => void copyPath(lastGenerated.path)}
            >
              Salin Link
            </button>
          </div>
        </div>
      ) : null}

      {payload ? (
        <>
          <div className="elt-summary" style={{ marginBottom: 16 }}>
            <div className="elt-stat is-wide">
              <span>Pesanan</span>
              <strong>
                {payload.order.po_no} — {payload.order.customer}
              </strong>
            </div>
            <div className="elt-stat">
              <span>Status</span>
              <strong>{payload.order.status_label}</strong>
            </div>
            <div className="elt-stat">
              <span>Milestone</span>
              <strong>{payload.progress.milestone.label}</strong>
            </div>
          </div>

          <h2 className="elt-section-title">Link aktif / riwayat</h2>
          {links.length === 0 ? (
            <p className="elt-empty">Belum ada link untuk PO ini.</p>
          ) : (
            links.map((link) => (
              <article key={link.id} className="elt-link-card">
                <div>
                  <strong>{link.status.toUpperCase()}</strong> · dibuat{" "}
                  {formatTs(link.created_at)}
                  {link.expires_at ? ` · expired ${formatTs(link.expires_at)}` : ""}
                </div>
                <div style={{ fontSize: "0.85rem", color: "var(--elt-muted)" }}>
                  /t/{link.customer_slug}/{link.po_slug}/••••
                  {link.access_count ? ` · diakses ${link.access_count}×` : ""}
                </div>
                <div className="elt-link-actions">
                  {link.status === "active" ? (
                    <button
                      type="button"
                      className="is-danger"
                      disabled={busy}
                      aria-busy={busy}
                      onClick={() => void onRevoke(link.id)}
                    >
                      Revoke
                    </button>
                  ) : null}
                </div>
              </article>
            ))
          )}

          {Array.isArray(payload.internal?.gaps) &&
          (payload.internal!.gaps as string[]).length > 0 ? (
            <>
              <h2 className="elt-section-title">Gap sumber event</h2>
              <ul>
                {(payload.internal!.gaps as string[]).map((g) => (
                  <li key={g} style={{ color: "var(--elt-muted)", fontSize: "0.85rem" }}>
                    {g}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}

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
