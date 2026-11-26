import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  fetchPublicTracking,
  formatQty,
  formatTs,
  openPublicInvoicePdf,
  resolvePublicDesignApproval,
  type TrackingDocumentCategory,
  type TrackingPayload,
} from "../../api/tracking";
import { NavIcon, type NavIconName } from "../../app/icons";
import { DesignApprovalReminderModal } from "./DesignApprovalReminderModal";
import {
  dismissDesignApproval,
  shouldShowDesignApprovalPopup,
} from "./designApprovalPopup";
import { SnakeProgress } from "./SnakeProgress";
import {
  OWNER_DOC_SLOTS,
  buildSnakeStages,
  timelineMilestoneLabel,
  type OwnerDocSlot,
} from "./snakeMapping";
import {
  formatEstimasiLabel,
  formatItemCountLabel,
  milestoneStateLabel,
} from "./liveTrackingView";
import "../../styles/tracking.css";

const DOC_ICONS: Record<string, NavIconName> = {
  design: "pen-tool",
  payment: "wallet",
  invoice: "file-text",
  sj: "clipboard-list",
  ship: "truck",
};

type Panel = "main" | "detail" | "timeline" | "help" | "doc";

type PdfUiState = { id: string; status: "loading" | "error"; message?: string };

function ensureTrackingPwaMeta() {
  const head = document.head;
  if (!head.querySelector('link[rel="manifest"][data-elt]')) {
    const manifest = document.createElement("link");
    manifest.rel = "manifest";
    manifest.href = "/tracking/manifest.webmanifest";
    manifest.dataset.elt = "1";
    head.appendChild(manifest);
  }
  if (!head.querySelector('meta[name="theme-color"][data-elt]')) {
    const theme = document.createElement("meta");
    theme.name = "theme-color";
    theme.content = "#1a4d3e";
    theme.dataset.elt = "1";
    head.appendChild(theme);
  }
  if ("serviceWorker" in navigator) {
    void navigator.serviceWorker.register("/tracking/sw.js", { scope: "/t/" }).catch(() => {});
  }
}

function statusBadgeClass(label: string): string {
  const t = label.toLowerCase();
  if (t.includes("selesai")) return "is-ok";
  if (t.includes("perhatian")) return "is-attn";
  if (t.includes("menunggu") || t.includes("order masuk")) return "is-wait";
  return "is-run";
}

function resolveDocSlot(docs: TrackingDocumentCategory[], slot: OwnerDocSlot) {
  if (slot.apiCategories.length === 0) {
    return {
      status: "waiting" as const,
      reason: "Belum tersedia",
      items: [] as Array<Record<string, unknown>>,
      canView: false,
    };
  }
  const hit = docs.find((d) => slot.apiCategories.includes(d.category));
  if (!hit) {
    return {
      status: "waiting" as const,
      reason: "Belum tersedia",
      items: [],
      canView: false,
    };
  }
  const canView =
    hit.status === "available" &&
    hit.items.some((it) => it.preview_allowed === true);
  return {
    status: hit.status,
    reason: hit.reason || hit.note || null,
    items: hit.items,
    canView,
  };
}

function docStatusText(status: string): string {
  if (status === "available") return "Tersedia";
  if (status === "locked") return "Terkunci";
  return "Belum tersedia";
}

export function LiveTrackingPage() {
  const { customerSlug = "", poSlug = "", token = "" } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState<TrackingPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [panel, setPanel] = useState<Panel>("main");
  const [docKey, setDocKey] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [dapOpen, setDapOpen] = useState(false);
  const [dapResolving, setDapResolving] = useState(false);
  const [dapError, setDapError] = useState<string | null>(null);
  const [dapDismissTick, setDapDismissTick] = useState(0);
  const [pdfUi, setPdfUi] = useState<PdfUiState | null>(null);

  useEffect(() => {
    ensureTrackingPwaMeta();
    document.title = "Live Tracking Pesanan — Eternal Packaging";
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let hasData = false;

    async function load(isPoll: boolean) {
      try {
        const payload = await fetchPublicTracking(customerSlug, poSlug, token);
        if (cancelled) return;
        hasData = true;
        setData(payload);
        setError(null);
        setStale(false);
        setLoading(false);
        timer = window.setTimeout(
          () => void load(true),
          payload.polling?.interval_ms || 30000,
        );
      } catch (err) {
        if (cancelled) return;
        if (isPoll && hasData) {
          setStale(true);
          timer = window.setTimeout(() => void load(true), 30000);
        } else {
          setError(err instanceof Error ? err.message : "Gagal memuat pelacakan.");
          setLoading(false);
        }
      }
    }

    void load(false);
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [customerSlug, poSlug, token]);

  const snake = useMemo(
    () => (data ? buildSnakeStages(data.milestones) : []),
    [data],
  );

  const dapEligible = useMemo(() => {
    void dapDismissTick;
    return shouldShowDesignApprovalPopup(data?.design_approval, poSlug);
  }, [data?.design_approval, poSlug, dapDismissTick]);

  useEffect(() => {
    if (!data) {
      setDapOpen(false);
      return;
    }
    if (dapEligible) {
      setDapOpen(true);
      setDapError(null);
    } else {
      setDapOpen(false);
    }
  }, [data, dapEligible]);

  function dismissDap() {
    const version = data?.design_approval?.design_version;
    if (version != null) dismissDesignApproval(poSlug, version);
    setDapOpen(false);
    setDapError(null);
    setDapDismissTick((n) => n + 1);
  }

  async function onApprovalDesign() {
    setDapResolving(true);
    setDapError(null);
    try {
      const resolved = await resolvePublicDesignApproval(customerSlug, poSlug, token);
      navigate(resolved.path);
    } catch (err) {
      setDapError(
        err instanceof Error ? err.message : "Link Approval Design tidak tersedia.",
      );
    } finally {
      setDapResolving(false);
    }
  }

  function flash(msg: string) {
    setToast(msg);
    window.setTimeout(() => setToast(null), 1800);
  }

  async function copyPo() {
    if (!data?.order.po_no) return;
    try {
      await navigator.clipboard.writeText(data.order.po_no);
      flash("Nomor PO disalin");
    } catch {
      flash("Gagal menyalin");
    }
  }

  async function onShare() {
    const url = window.location.href;
    try {
      if (navigator.share) {
        await navigator.share({
          title: "Eternal Live Tracking",
          text: `Lacak pesanan ${data?.order.po_no || ""}`,
          url,
        });
        return;
      }
      await navigator.clipboard.writeText(url);
      flash("Link disalin");
    } catch {
      flash("Tidak dapat membagikan");
    }
  }

  if (loading) {
    return (
      <div className="elt-root" aria-busy="true">
        <header className="elt-topbar">
          <div className="elt-topbar-inner">
            <span className="elt-logo" aria-hidden />
            <span className="elt-topbar-brand">ETERNAL PACKAGING</span>
          </div>
          <h1 className="elt-page-title">Live Tracking Pesanan</h1>
        </header>
        <div className="elt-body">
          <div className="elt-skel" />
          <div className="elt-skel" />
          <div className="elt-skel" />
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="elt-root">
        <header className="elt-topbar">
          <div className="elt-topbar-inner">
            <span className="elt-logo" aria-hidden />
            <span className="elt-topbar-brand">ETERNAL PACKAGING</span>
          </div>
          <h1 className="elt-page-title">Live Tracking Pesanan</h1>
        </header>
        <div className="elt-body">
          <div className="elt-error">
            <h2>Link tidak valid</h2>
            <p>{error || "Pelacakan tidak ditemukan, kedaluwarsa, atau sudah dicabut."}</p>
          </div>
        </div>
      </div>
    );
  }

  const items = data.order.summary.items || [];
  const itemCount = data.order.summary.item_count ?? items.length;
  const itemCountLabel = formatItemCountLabel(itemCount);
  const estimasi = formatEstimasiLabel(
    data.order.eta_ship,
    data.order.summary.eta_ship,
  );

  const activeDoc = docKey
    ? OWNER_DOC_SLOTS.find((s) => s.key === docKey) ?? null
    : null;
  const activeDocResolved = activeDoc
    ? resolveDocSlot(data.documents, activeDoc)
    : null;

  return (
    <div className="elt-root">
      <header className="elt-topbar">
        <div className="elt-topbar-inner">
          <span className="elt-logo" aria-hidden>
            S
          </span>
          <span className="elt-topbar-brand">ETERNAL PACKAGING</span>
          <button
            type="button"
            className="elt-share-quiet"
            onClick={() => void onShare()}
            aria-label="Bagikan link pelacakan"
          >
            Bagikan
          </button>
        </div>
        <h1 className="elt-page-title">Live Tracking Pesanan</h1>
      </header>

      <div className="elt-body">
        {stale ? (
          <p className="elt-stale" role="status">
            Koneksi bermasalah — menampilkan data terakhir.
          </p>
        ) : null}
        {toast ? (
          <p className="elt-toast" role="status">
            {toast}
          </p>
        ) : null}

        <section className="elt-card" aria-label="Informasi PO">
          <div className="elt-po-head">
            <div className="elt-po-customer">
              <div className="elt-kicker">PELANGGAN / OUTLET</div>
              <h2 className="elt-customer-name">{data.order.customer || "—"}</h2>
            </div>
            <span className={`elt-status-pill ${statusBadgeClass(data.order.status_label)}`}>
              {data.order.status_label}
            </span>
          </div>

          <div className="elt-po-number-block">
            <div className="elt-kicker">NO. PO</div>
            <div className="elt-po-row">
              <strong className="elt-po-no">{data.order.po_no}</strong>
              <button
                type="button"
                className="elt-icon-btn"
                onClick={() => void copyPo()}
                aria-label="Salin nomor PO"
                title="Salin nomor PO"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
                  <rect x="5" y="5" width="8" height="9" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
                  <rect x="3" y="2" width="8" height="9" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
                </svg>
              </button>
            </div>
          </div>

          <hr className="elt-divider" />

          <button
            type="button"
            className="elt-meta-row is-action"
            onClick={() => setPanel("detail")}
            aria-label={`${itemCountLabel}, buka Detail Pesanan`}
          >
            <span className="elt-meta-icon" aria-hidden>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
                <path d="M3.3 7 12 12l8.7-5" />
                <path d="M12 22V12" />
              </svg>
            </span>
            <span className="elt-meta-main">{itemCountLabel}</span>
            <span className="elt-meta-chevron" aria-hidden>
              ›
            </span>
          </button>

          <hr className="elt-divider" />

          <div className="elt-meta-row" aria-label="Estimasi selesai">
            <span className="elt-meta-icon" aria-hidden>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <rect x="3" y="5" width="18" height="16" rx="2" />
                <path d="M3 10h18" />
                <path d="M8 3v4" />
                <path d="M16 3v4" />
              </svg>
            </span>
            <span className="elt-meta-label">Estimasi Selesai</span>
            <strong className="elt-meta-value">{estimasi}</strong>
          </div>
        </section>

        <section className="elt-card" aria-label="Progress pesanan">
          <div className="elt-card-head">
            <h2 className="elt-card-title">Progress Pesanan</h2>
            <button
              type="button"
              className="elt-text-link is-accent"
              onClick={() => setPanel("timeline")}
            >
              Lihat Timeline
            </button>
          </div>
          <SnakeProgress stages={snake} />
        </section>

        <section className="elt-card" aria-label="Dokumen pesanan">
          <h2 className="elt-card-title">Dokumen Pesanan</h2>
          <div className="elt-doc-grid">
            {OWNER_DOC_SLOTS.map((slot) => {
              const resolved = resolveDocSlot(data.documents, slot);
              return (
                <article key={slot.key} className="elt-doc-tile">
                  <div className="elt-doc-icon" aria-hidden>
                    <NavIcon name={DOC_ICONS[slot.key] ?? "file-text"} size={18} strokeWidth={2} />
                  </div>
                  <h3>{slot.title}</h3>
                  <p className={`elt-doc-status is-${resolved.status}`}>
                    ○ {docStatusText(resolved.status)}
                  </p>
                  <button
                    type="button"
                    className="elt-text-link"
                    disabled={!resolved.canView}
                    onClick={() => {
                      if (!resolved.canView) return;
                      setDocKey(slot.key);
                      setPanel("doc");
                    }}
                  >
                    Lihat
                  </button>
                </article>
              );
            })}
          </div>
        </section>

        <div className="elt-actions" role="group" aria-label="Aksi">
          <button type="button" onClick={() => setPanel("detail")}>
            Detail Pesanan
          </button>
          <button type="button" onClick={() => setPanel("timeline")}>
            Lihat Timeline
          </button>
          <button type="button" onClick={() => setPanel("help")}>
            Bantuan
          </button>
        </div>

        <p className="elt-copyright">© Eternal Packaging</p>
        <p className="elt-updated-foot">
          Diperbarui {formatTs(data.order.last_updated_at || data.generated_at)}
        </p>
      </div>

      <DesignApprovalReminderModal
        open={dapOpen && panel === "main"}
        poNo={data.order.po_no}
        resolving={dapResolving}
        error={dapError}
        onClose={dismissDap}
        onLater={dismissDap}
        onApprove={() => void onApprovalDesign()}
      />

      {panel !== "main" ? (
        <div className="elt-panel" role="dialog" aria-modal="true">
          <div className="elt-panel-bar">
            <button type="button" className="elt-text-link" onClick={() => setPanel("main")}>
              ← Kembali
            </button>
            <strong>
              {panel === "detail"
                ? "Detail Pesanan"
                : panel === "timeline"
                  ? "Timeline"
                  : panel === "help"
                    ? "Bantuan"
                    : activeDoc?.title || "Dokumen"}
            </strong>
            <span />
          </div>
          <div className="elt-panel-body">
            {panel === "detail" ? (
              <>
                <dl className="elt-dl">
                  <div>
                    <dt>Tanggal masuk</dt>
                    <dd>{data.order.po_date || "—"}</dd>
                  </div>
                  <div>
                    <dt>Total Qty PO</dt>
                    <dd>{formatQty(data.progress.production.qty_po)}</dd>
                  </div>
                  <div>
                    <dt>Qty diproduksi</dt>
                    <dd>{formatQty(data.progress.production.qty_produced)}</dd>
                  </div>
                  <div>
                    <dt>Qty lolos QC</dt>
                    <dd>{formatQty(data.progress.production.qty_qc_passed)}</dd>
                  </div>
                  <div>
                    <dt>Qty dikirim</dt>
                    <dd>{formatQty(data.progress.shipping.qty_shipped)}</dd>
                  </div>
                  <div>
                    <dt>Qty belum dikirim</dt>
                    <dd>{formatQty(data.progress.shipping.qty_remaining)}</dd>
                  </div>
                  <div>
                    <dt>Status progres</dt>
                    <dd>{data.progress.milestone.label}</dd>
                  </div>
                </dl>
                <h3 className="elt-subhead">Item</h3>
                <ul className="elt-item-list">
                  {items.map((it) => (
                    <li key={`${it.item_code}-${it.item_name}`}>
                      <strong>{it.item_name}</strong>
                      <span>
                        {formatQty(it.qty)} {it.unit}
                        {it.color ? ` · ${it.color}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}

            {panel === "timeline" ? (
              <ol className="elt-event-list">
                {data.milestones.map((m) => (
                  <li key={m.id} className={`is-${m.state}`}>
                    <div className="elt-event-head">
                      <strong>
                        {m.seq}. {timelineMilestoneLabel(m.id, m.label)}
                      </strong>
                      <span>{milestoneStateLabel(m.state)}</span>
                    </div>
                    <p>{m.detail}</p>
                    <time>{formatTs(m.timestamp)}</time>
                  </li>
                ))}
              </ol>
            ) : null}

            {panel === "help" ? (
              <div className="elt-help-block">
                <h2>{data.help.title}</h2>
                <p>{data.help.hours}</p>
                <p>
                  Email:{" "}
                  <a
                    href={`mailto:${data.help.email}?subject=PO%20${encodeURIComponent(data.order.po_no)}`}
                  >
                    {data.help.email}
                  </a>
                </p>
                <p>{data.help.note}</p>
              </div>
            ) : null}

            {panel === "doc" && activeDoc && activeDocResolved ? (
              <>
                {!activeDocResolved.canView ? (
                  <p className="elt-note">
                    {activeDocResolved.reason || "Dokumen tidak tersedia atau akses ditolak."}
                  </p>
                ) : (
                  <ul className="elt-item-list">
                    {activeDocResolved.items
                      .filter((it) => it.preview_allowed === true)
                      .map((it, idx) => {
                        const invId = String(it.id || "");
                        const showPdf =
                          docKey === "invoice" &&
                          it.file_available === true &&
                          it.download_allowed === true &&
                          Boolean(invId);
                        const pdfBusy = pdfUi?.id === invId && pdfUi.status === "loading";
                        const pdfErr =
                          pdfUi?.id === invId && pdfUi.status === "error"
                            ? pdfUi.message || "PDF tidak tersedia."
                            : null;
                        return (
                          <li key={String(it.id || idx)}>
                            <strong>{String(it.number || it.id || "Dokumen")}</strong>
                            <span>
                              {[
                                it.date || it.confirmed_at || "",
                                it.status ? `status: ${String(it.status)}` : "",
                                it.qty != null ? `qty: ${String(it.qty)}` : "",
                                it.amount != null ? `nominal: ${String(it.amount)}` : "",
                                it.invoice_no ? `faktur: ${String(it.invoice_no)}` : "",
                                it.recipient_name
                                  ? `penerima: ${String(it.recipient_name)}`
                                  : "",
                                it.has_photo === true ? "foto ✓" : "",
                                it.has_signature === true ? "ttd ✓" : "",
                                it.note ? String(it.note) : "",
                              ]
                                .filter(Boolean)
                                .map(String)
                                .join(" · ")}
                            </span>
                            {showPdf ? (
                              <div className="elt-doc-pdf-actions">
                                <button
                                  type="button"
                                  className="elt-text-link"
                                  disabled={pdfBusy}
                                  onClick={() => {
                                    setPdfUi({ id: invId, status: "loading" });
                                    void openPublicInvoicePdf(
                                      customerSlug,
                                      poSlug,
                                      token,
                                      invId,
                                    )
                                      .then(() => setPdfUi(null))
                                      .catch((err: unknown) => {
                                        const msg =
                                          err instanceof Error
                                            ? err.message
                                            : "Dokumen PDF sementara tidak tersedia.";
                                        setPdfUi({
                                          id: invId,
                                          status: "error",
                                          message: msg,
                                        });
                                      });
                                  }}
                                >
                                  {pdfBusy ? "Memuat PDF…" : "Lihat Faktur PDF"}
                                </button>
                                {pdfErr ? <p className="elt-note">{pdfErr}</p> : null}
                              </div>
                            ) : null}
                          </li>
                        );
                      })}
                  </ul>
                )}
                {activeDocResolved.items.some((it) => it.preview_allowed === false) ? (
                  <p className="elt-note">
                    Sebagian dokumen terkait ditahan (multi-SO / belum diizinkan).
                  </p>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
