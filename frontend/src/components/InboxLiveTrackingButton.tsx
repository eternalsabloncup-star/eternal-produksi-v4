import {
  useCallback,
  useEffect,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { listTrackingLinks } from "../api/tracking";
import type { PoRow } from "../demo/types";
import {
  generateAndCopyLiveTrackingLink,
  hasActiveTrackingLink,
  LiveTrackingCopyError,
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
 * Belum dibuat → generate + copy sekali.
 * Sudah dibuat (backend active) → status saja, tanpa mutasi.
 */
export function InboxLiveTrackingButton({ row }: Props) {
  const poKey = resolveTrackingPoKey(row);
  const [status, setStatus] = useState<Status>(poKey ? "loading" : "absent");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  const closeFeedback = useCallback(() => setFeedback(null), []);

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
    if (!poKey || busy || status === "created" || status === "loading") return;
    setBusy(true);
    setFeedback(null);
    try {
      await generateAndCopyLiveTrackingLink(poKey);
      setStatus("created");
      setFeedback({
        tone: "success",
        title: "Link disalin",
        message: `PO ${row.poNo || row.id} · siap dibagikan.`,
      });
    } catch (err) {
      if (err instanceof LiveTrackingCopyError) {
        if (err.linkCreated || err.code === "already_created") {
          setStatus("created");
        }
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

  let cell: ReactNode;
  if (!poKey) {
    cell = (
      <span className="ep-cell-center" style={{ fontSize: 11, color: "#6a8578" }}>
        —
      </span>
    );
  } else if (status === "created") {
    cell = (
      <span className="ep-cell-center">
        <span
          className="ep-live-tracking-done"
          data-testid="inbox-live-tracking-done"
          data-po-key={poKey}
          data-po-no={row.poNo || row.id}
          title={`Live Tracking sudah dibuat · ${poKey}`}
        >
          Live Tracking Sudah Dibuat
        </span>
      </span>
    );
  } else {
    cell = (
      <span className="ep-cell-center">
        <button
          type="button"
          className={`ep-btn${busy ? " is-busy" : ""}`}
          style={{ padding: "4px 8px", fontSize: 11 }}
          disabled={busy || status === "loading"}
          aria-busy={busy}
          title={
            busy
              ? "Memproses Live Tracking…"
              : status === "loading"
                ? "Memeriksa status…"
                : `Live Tracking · ${poKey}`
          }
          data-testid="inbox-live-tracking-btn"
          data-po-key={poKey}
          data-po-no={row.poNo || row.id}
          onClick={(ev) => void onClick(ev)}
        >
          {busy ? "Memproses…" : status === "loading" ? "…" : "Live Tracking"}
        </button>
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
