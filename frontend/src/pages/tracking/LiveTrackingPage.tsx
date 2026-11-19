import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  MILESTONE_STATE_LABEL,
  fetchPublicTracking,
  formatQty,
  formatTs,
  type MilestoneState,
  type TrackingMilestone,
  type TrackingPayload,
} from "../../api/tracking";
import "../../styles/tracking.css";

type TabId = "ringkasan" | "timeline" | "dokumen" | "bantuan";

function badgeClass(state: MilestoneState | string): string {
  if (state === "completed" || state === "Selesai") return "is-ok";
  if (state === "in_progress" || state === "Dalam Proses" || state === "Berjalan")
    return "is-run";
  if (state === "attention_required" || state === "Perlu Perhatian") return "is-attn";
  if (state === "not_applicable") return "is-na";
  return "is-wait";
}

function ProgressBar({ ratio, label }: { ratio: number | null; label: string }) {
  const pct = ratio === null ? null : Math.round(Math.min(1, Math.max(0, ratio)) * 100);
  return (
    <div className="elt-progress-block">
      <div className="elt-progress-labels">
        <span>{label}</span>
        <span>{pct === null ? "Belum tersedia" : `${pct}%`}</span>
      </div>
      <div className="elt-bar" role="progressbar" aria-valuenow={pct ?? 0} aria-valuemin={0} aria-valuemax={100}>
        <i style={{ width: `${pct ?? 0}%`, opacity: pct === null ? 0.25 : 1 }} />
      </div>
    </div>
  );
}

function MilestoneRow({
  m,
  open,
  onToggle,
}: {
  m: TrackingMilestone;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <li className={`elt-step is-${m.state}`}>
      <span className="elt-node" aria-hidden>
        {m.state === "completed" ? "✓" : m.seq}
      </span>
      <div className="elt-step-body">
        <div className="elt-step-head">
          <button type="button" onClick={onToggle} aria-expanded={open}>
            <div className="elt-step-title">{m.label}</div>
            <div className="elt-step-time">{formatTs(m.timestamp)}</div>
          </button>
          <span className={`elt-badge ${badgeClass(m.state)}`}>
            {MILESTONE_STATE_LABEL[m.state]}
          </span>
        </div>
        {open ? <p className="elt-step-detail">{m.detail}</p> : null}
      </div>
    </li>
  );
}

function ensureTrackingPwaMeta() {
  const head = document.head;
  let manifest = head.querySelector('link[rel="manifest"][data-elt]') as HTMLLinkElement | null;
  if (!manifest) {
    manifest = document.createElement("link");
    manifest.rel = "manifest";
    manifest.href = "/tracking/manifest.webmanifest";
    manifest.dataset.elt = "1";
    head.appendChild(manifest);
  }
  let theme = head.querySelector('meta[name="theme-color"][data-elt]') as HTMLMetaElement | null;
  if (!theme) {
    theme = document.createElement("meta");
    theme.name = "theme-color";
    theme.content = "#1a4d3e";
    theme.dataset.elt = "1";
    head.appendChild(theme);
  }
  if ("serviceWorker" in navigator) {
    void navigator.serviceWorker
      .register("/tracking/sw.js", { scope: "/t/" })
      .catch(() => {
        /* offline fallback opsional — jangan blok UI */
      });
  }
}

export function LiveTrackingPage() {
  const { customerSlug = "", poSlug = "", token = "" } = useParams();
  const [tab, setTab] = useState<TabId>("ringkasan");
  const [data, setData] = useState<TrackingPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [openIds, setOpenIds] = useState<Set<string>>(() => new Set());
  const [shareNote, setShareNote] = useState<string | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    ensureTrackingPwaMeta();
    document.title = "Eternal Live Tracking";
    return () => {
      mounted.current = false;
    };
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
        if (!isPoll) {
          const active = payload.progress.active_milestone?.id;
          setOpenIds(new Set(active ? [active] : []));
        }
        const interval = payload.polling?.interval_ms || 30000;
        timer = window.setTimeout(() => void load(true), interval);
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
      setShareNote("Link disalin");
      window.setTimeout(() => setShareNote(null), 2000);
    } catch {
      setShareNote("Tidak dapat membagikan");
      window.setTimeout(() => setShareNote(null), 2000);
    }
  }

  if (loading) {
    return (
      <div className="elt-root" aria-busy="true">
        <header className="elt-header">
          <div className="elt-brand">
            <div className="elt-brand-name">Eternal Packaging</div>
            <div className="elt-brand-sub">Live Tracking</div>
          </div>
        </header>
        <section className="elt-section">
          <div className="elt-skel" />
          <div className="elt-skel" />
          <div className="elt-skel" />
        </section>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="elt-root">
        <div className="elt-error">
          <h1>Link tidak valid</h1>
          <p>{error || "Pelacakan tidak ditemukan, kedaluwarsa, atau sudah dicabut."}</p>
        </div>
      </div>
    );
  }

  const produced = data.progress.production.qty_produced;
  const qc = data.progress.production.qty_qc_passed;
  const shipped = data.progress.shipping.qty_shipped;
  const remaining = data.progress.shipping.qty_remaining;
  const qtyPo = data.progress.production.qty_po;

  return (
    <div className="elt-root">
      <header className="elt-header">
        <div className="elt-brand-row">
          <div className="elt-brand">
            <div className="elt-brand-name">Eternal Packaging</div>
            <div className="elt-brand-sub">{data.order.customer}</div>
          </div>
          <button type="button" className="elt-share" onClick={() => void onShare()}>
            {shareNote || "Bagikan"}
          </button>
        </div>
        <div className="elt-meta">
          <div className="elt-po">{data.order.po_no}</div>
          <div className="elt-status-row">
            <span className={`elt-badge ${badgeClass(data.order.status_label)}`}>
              {data.order.status_label}
            </span>
            <span className={`elt-updated${stale ? " elt-stale" : ""}`}>
              {stale
                ? "Koneksi bermasalah — menampilkan data terakhir"
                : `Diperbarui ${formatTs(data.order.last_updated_at || data.generated_at)}`}
            </span>
          </div>
        </div>
      </header>

      {tab === "ringkasan" ? (
        <section className="elt-section" aria-label="Ringkasan pesanan">
          <h2 className="elt-section-title">Ringkasan</h2>
          <div className="elt-summary">
            <div className="elt-stat is-wide">
              <span>Pelanggan</span>
              <strong>{data.order.customer}</strong>
            </div>
            <div className="elt-stat">
              <span>Tanggal masuk</span>
              <strong>{data.order.po_date || "—"}</strong>
            </div>
            <div className="elt-stat">
              <span>Total Qty PO</span>
              <strong>{formatQty(qtyPo)}</strong>
            </div>
            <div className="elt-stat">
              <span>Qty diproduksi</span>
              <strong>{formatQty(produced)}</strong>
            </div>
            <div className="elt-stat">
              <span>Qty lolos QC</span>
              <strong>{formatQty(qc)}</strong>
            </div>
            <div className="elt-stat">
              <span>Qty dikirim</span>
              <strong>{formatQty(shipped)}</strong>
            </div>
            <div className="elt-stat">
              <span>Qty belum dikirim</span>
              <strong>{formatQty(remaining)}</strong>
            </div>
            {data.order.eta_ship ? (
              <div className="elt-stat is-wide">
                <span>Estimasi kirim (sumber PO)</span>
                <strong>{data.order.eta_ship}</strong>
              </div>
            ) : null}
          </div>

          <div style={{ marginTop: 16 }}>
            <ProgressBar
              ratio={data.progress.milestone.ratio}
              label={`Milestone ${data.progress.milestone.label}`}
            />
            <ProgressBar ratio={data.progress.production.ratio} label="Progres produksi (Qty)" />
            <ProgressBar ratio={data.progress.shipping.ratio} label="Progres pengiriman (Qty)" />
          </div>
        </section>
      ) : null}

      {tab === "timeline" ? (
        <section className="elt-section" aria-label="Timeline 15 tahap">
          <h2 className="elt-section-title">Timeline</h2>
          <ol className="elt-timeline">
            {data.milestones.map((m) => (
              <MilestoneRow
                key={m.id}
                m={m}
                open={openIds.has(m.id)}
                onToggle={() =>
                  setOpenIds((prev) => {
                    const next = new Set(prev);
                    if (next.has(m.id)) next.delete(m.id);
                    else next.add(m.id);
                    return next;
                  })
                }
              />
            ))}
          </ol>
        </section>
      ) : null}

      {tab === "dokumen" ? (
        <section className="elt-section" aria-label="Dokumen">
          <h2 className="elt-section-title">Dokumen</h2>
          <div className="elt-docs">
            {data.documents.map((doc) => (
              <article key={doc.category} className="elt-doc">
                <div className="elt-doc-head">
                  <h3>{doc.label}</h3>
                  <span className={`elt-badge ${badgeClass(doc.status === "available" ? "completed" : doc.status === "locked" ? "attention_required" : "pending")}`}>
                    {doc.status === "available"
                      ? "Tersedia"
                      : doc.status === "locked"
                        ? "Terkunci"
                        : "Menunggu"}
                  </span>
                </div>
                {doc.reason ? <p className="elt-step-detail">{doc.reason}</p> : null}
                {doc.note ? <p className="elt-step-detail">{doc.note}</p> : null}
                {doc.items.length > 0 ? (
                  <ul className="elt-doc-list">
                    {doc.items.map((item, idx) => (
                      <li key={String(item.id || idx)}>
                        <span>{String(item.number || item.id || "Dokumen")}</span>
                        <span>{String(item.date || item.confirmed_at || "")}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {tab === "bantuan" ? (
        <section className="elt-section" aria-label="Bantuan">
          <div className="elt-help">
            <h2>{data.help.title}</h2>
            <p>{data.help.hours}</p>
            <p>
              Email:{" "}
              <a href={`mailto:${data.help.email}?subject=PO%20${encodeURIComponent(data.order.po_no)}`}>
                {data.help.email}
              </a>
            </p>
            <p>{data.help.note}</p>
          </div>
        </section>
      ) : null}

      <nav className="elt-nav" aria-label="Navigasi pelacakan">
        {(
          [
            ["ringkasan", "Ringkasan"],
            ["timeline", "Timeline"],
            ["dokumen", "Dokumen"],
            ["bantuan", "Bantuan"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={tab === id ? "is-active" : undefined}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
    </div>
  );
}
