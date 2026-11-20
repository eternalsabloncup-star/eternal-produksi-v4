import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { listTrackingLinks } from "../api/tracking";
import type { PoRow } from "../demo/types";
import {
  createOrCopyLiveTrackingLink,
  hasActiveTrackingLink,
  LiveTrackingCopyError,
  recoverAndCopyLiveTrackingLink,
} from "../lib/liveTrackingCopy";
import { resolveTrackingPoKey } from "../lib/trackingPoKey";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";

type Feedback = {
  tone: "success" | "error";
  title: string;
  message: string;
};

type Props = {
  row: PoRow;
};

type Status = "loading" | "absent" | "created";

/**
 * Belum ada → Buat & Salin Link.
 * Sudah aktif → Salin Link (reveal URL, tanpa regenerate).
 * Legacy hash-only → penjelasan + pemulihan terkendali (persetujuan eksplisit).
 * Clipboard gagal (mobile async) → field URL selectable manual, tanpa regenerate.
 */
export function InboxLiveTrackingButton({ row }: Props) {
  const poKey = resolveTrackingPoKey(row);
  const [status, setStatus] = useState<Status>(poKey ? "loading" : "absent");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [manualUrl, setManualUrl] = useState<string | null>(null);
  const manualRef = useRef<HTMLInputElement | null>(null);

  const closeFeedback = useCallback(() => setFeedback(null), []);

  const showManualFallback = useCallback((url: string, message: string) => {
    setManualUrl(url);
    setFeedback({
      tone: "error",
      title: "Salin manual",
      message,
    });
    queueMicrotask(() => {
      const el = manualRef.current;
      if (!el) return;
      el.focus();
      el.select();
    });
  }, []);

  const refreshStatus = useCallback(async () => {
    if (!poKey) {
      setStatus("absent");
      return;
    }
    try {
      const items = await listTrackingLinks(poKey);
      setStatus(hasActiveTrackingLink(items) ? "created" : "absent");
    } catch {
      // Keep actionable button; click will surface permission/API errors.
      setStatus("absent");
    }
  }, [poKey]);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  async function onClick(e: MouseEvent) {
    e.stopPropagation();
    if (!poKey || busy || status === "loading") return;
    setBusy(true);
    setFeedback(null);
    setManualUrl(null);
    try {
      await createOrCopyLiveTrackingLink(poKey);
      setStatus("created");
      setFeedback({
        tone: "success",
        title: "Link disalin",
        message: `PO ${row.poNo || row.id} · siap dibagikan.`,
      });
    } catch (err) {
      if (
        err instanceof LiveTrackingCopyError &&
        err.code === "legacy_unrecoverable"
      ) {
        setStatus("created");
        const expectedLinkId = err.linkId || "";
        const confirmed = window.confirm(
          `${err.message}\n\nPemulihan akan mencabut link lama dan membuat link baru. Lanjutkan?`,
        );
        if (!confirmed) {
          setFeedback({
            tone: "error",
            title: "Dibatalkan",
            message: "Pemulihan link tidak dijalankan. Link lama tetap aktif.",
          });
          return;
        }
        try {
          await recoverAndCopyLiveTrackingLink(poKey, {
            confirmed: true,
            expectedLinkId,
          });
          setStatus("created");
          setFeedback({
            tone: "success",
            title: "Link disalin",
            message: `PO ${row.poNo || row.id} · link baru siap dibagikan.`,
          });
        } catch (recoverErr) {
          if (
            recoverErr instanceof LiveTrackingCopyError &&
            recoverErr.code === "clipboard" &&
            recoverErr.absoluteUrl
          ) {
            setStatus("created");
            showManualFallback(recoverErr.absoluteUrl, recoverErr.message);
          } else {
            setFeedback({
              tone: "error",
              title: "Gagal",
              message:
                recoverErr instanceof Error
                  ? recoverErr.message
                  : "Gagal memulihkan link.",
            });
          }
        }
        return;
      }
      if (err instanceof LiveTrackingCopyError) {
        if (err.linkCreated || err.code === "already_created") {
          setStatus("created");
        }
        if (err.code === "clipboard" && err.absoluteUrl) {
          showManualFallback(err.absoluteUrl, err.message);
          return;
        }
        // Clipboard fail tanpa URL → error only; never success.
        setFeedback({
          tone: "error",
          title: "Gagal",
          message: err.message,
        });
      } else {
        setFeedback({
          tone: "error",
          title: "Gagal",
          message: err instanceof Error ? err.message : "Gagal Live Tracking.",
        });
      }
    } finally {
      setBusy(false);
    }
  }

  const label =
    busy || status === "loading"
      ? "Memproses…"
      : status === "created"
        ? "Salin Link"
        : "Buat & Salin Link";

  let cell: ReactNode;
  if (!poKey) {
    cell = (
      <span className="ep-cell-center" style={{ fontSize: 11, color: "#6a8578" }}>
        —
      </span>
    );
  } else {
    cell = (
      <span className="ep-cell-center" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <button
          type="button"
          className={`ep-btn${busy ? " is-busy" : ""}`}
          style={{ padding: "4px 8px", fontSize: 11 }}
          disabled={busy || status === "loading"}
          aria-busy={busy}
          title={
            busy || status === "loading"
              ? "Memproses Live Tracking…"
              : status === "created"
                ? `Salin Link Live Tracking · ${poKey}`
                : `Buat & Salin Link Live Tracking · ${poKey}`
          }
          data-testid="inbox-live-tracking-btn"
          data-po-key={poKey}
          data-po-no={row.poNo || row.id}
          data-status={status}
          onClick={(ev) => void onClick(ev)}
        >
          {label}
        </button>
        {manualUrl ? (
          <input
            ref={manualRef}
            type="text"
            readOnly
            value={manualUrl}
            data-testid="inbox-live-tracking-manual-url"
            aria-label="URL Live Tracking untuk salin manual"
            onFocus={(ev) => ev.currentTarget.select()}
            onClick={(ev) => {
              ev.stopPropagation();
              ev.currentTarget.select();
            }}
            style={{
              width: "100%",
              maxWidth: 220,
              fontSize: 10,
              padding: "4px 6px",
              border: "1px solid #9bb5a8",
              borderRadius: 4,
              background: "#f4faf7",
            }}
          />
        ) : null}
      </span>
    );
  }

  return (
    <>
      {cell}
      <ConfirmationSuccessOverlay
        open={Boolean(feedback)}
        global
        tone={feedback?.tone || "success"}
        title={feedback?.title || ""}
        message={feedback?.message || ""}
        autoCloseMs={feedback?.tone === "success" ? 1800 : 2600}
        onClose={closeFeedback}
      />
    </>
  );
}
