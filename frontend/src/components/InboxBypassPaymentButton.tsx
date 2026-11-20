import {
  useCallback,
  useEffect,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import {
  bypassPaymentGate,
  fetchPaymentState,
  WorkflowApiError,
} from "../api/workflow";
import { useAuth } from "../auth";
import type { PoRow } from "../demo/types";
import { ConfirmationSuccessOverlay } from "./ConfirmationSuccessOverlay";
import { InboxBypassPaymentModal } from "./InboxBypassPaymentModal";

type Feedback = {
  tone: "success" | "error";
  title: string;
  message: string;
};

type Props = {
  row: PoRow;
  onUpdated?: (next: PoRow) => void;
};

type Phase = "idle" | "loading" | "success" | "error";

function isBypassed(row: PoRow): boolean {
  return Boolean(
    row.paymentGate?.bypass && row.paymentGate?.state === "open",
  );
}

function isPaymentOpenNormal(row: PoRow): boolean {
  return (
    row.paymentGate?.state === "open" && !Boolean(row.paymentGate?.bypass)
  );
}

function safeErrorMessage(err: unknown): string {
  if (err instanceof WorkflowApiError) {
    const msg = err.message.trim();
    if (err.status === 403) {
      return msg || "Akses ditolak: tidak memiliki izin Bypass Payment.";
    }
    if (err.status === 409) {
      return msg || "Gate payment bentrok — refresh status terlebih dahulu.";
    }
    if (err.status === 422) {
      return msg || "Alasan bypass tidak valid.";
    }
    if (err.status === 404) {
      return msg || "PO tidak ditemukan.";
    }
    return msg || "Bypass Payment gagal.";
  }
  if (err instanceof Error && err.message.trim()) return err.message.trim();
  return "Bypass Payment gagal. Silakan coba lagi.";
}

/**
 * Aksi baris Inbox: Bypass Payment via endpoint backend existing.
 * Hanya mode API + capability + PO accepted.
 */
export function InboxBypassPaymentButton({ row, onUpdated }: Props) {
  const { capabilities } = useAuth();
  const canBypass = Boolean(capabilities.payment_bypass);
  const accepted = row.acceptanceStatus === "accepted";
  const apiPo = row.origin === "accurate" && Boolean(row.accurateId);

  const [modalOpen, setModalOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [localGate, setLocalGate] = useState(row.paymentGate);

  useEffect(() => {
    setLocalGate(row.paymentGate);
    setPhase("idle");
  }, [row.accurateId, row.paymentGate?.state, row.paymentGate?.bypass]);

  const gate = localGate ?? row.paymentGate;
  const bypassed = Boolean(gate?.bypass && gate?.state === "open");
  const openNormal = gate?.state === "open" && !Boolean(gate?.bypass);

  const closeFeedback = useCallback(() => setFeedback(null), []);

  function applyGateToRow(
    base: PoRow,
    next: { state?: string; bypass?: boolean; reason?: string | null },
  ): PoRow {
    const paymentGate = {
      state: next.state || "open",
      bypass: Boolean(next.bypass),
      reason: next.reason ?? null,
    };
    setLocalGate(paymentGate);
    return { ...base, paymentGate };
  }

  async function reconcileGateBeforeRetry(): Promise<PoRow> {
    const id = row.accurateId;
    if (!id) return row;
    try {
      const state = await fetchPaymentState(id);
      const payment = state.gates?.payment;
      const detail =
        payment?.detail && typeof payment.detail === "object"
          ? (payment.detail as { bypass?: boolean })
          : {};
      const next = applyGateToRow(row, {
        state: payment?.state,
        bypass: Boolean(detail.bypass),
        reason: payment?.reason ?? null,
      });
      onUpdated?.(next);
      return next;
    } catch {
      return row;
    }
  }

  async function onConfirm(reason: string) {
    if (!row.accurateId || phase === "loading") return;
    setPhase("loading");
    setFeedback(null);
    try {
      const res = await bypassPaymentGate(row.accurateId, reason);
      const payment = res.payment;
      const detail =
        payment?.detail && typeof payment.detail === "object"
          ? (payment.detail as { bypass?: boolean })
          : { bypass: true };
      const next = applyGateToRow(row, {
        state: payment?.state || "open",
        bypass: detail.bypass !== false,
        reason: payment?.reason ?? `Bypass Payment: ${reason}`,
      });
      onUpdated?.(next);
      setPhase("success");
      setModalOpen(false);
      setFeedback({
        tone: "success",
        title: res.duplicate ? "Sudah di-bypass" : "Bypass berhasil",
        message: res.duplicate
          ? `PO ${row.poNo || row.id} · Payment Bypassed (idempotent).`
          : `PO ${row.poNo || row.id} · Payment Bypassed.`,
      });
    } catch (err) {
      setPhase("error");
      // Jaringan/ambiguous: reconcile dulu — jangan auto-retry mutation.
      if (err instanceof WorkflowApiError && err.status === 0) {
        const reconciled = await reconcileGateBeforeRetry();
        if (isBypassed(reconciled)) {
          setModalOpen(false);
          setFeedback({
            tone: "success",
            title: "Status tersinkron",
            message: `PO ${row.poNo || row.id} · Payment Bypassed.`,
          });
          setPhase("success");
          return;
        }
      }
      setFeedback({
        tone: "error",
        title: "Gagal",
        message: safeErrorMessage(err),
      });
    }
  }

  function onOpen(e: MouseEvent) {
    e.stopPropagation();
    if (!canBypass || !accepted || !apiPo || bypassed || openNormal) return;
    setPhase("idle");
    setFeedback(null);
    setModalOpen(true);
  }

  let cell: ReactNode;
  if (!apiPo || !canBypass) {
    cell = (
      <span className="ep-cell-center" style={{ fontSize: 11, color: "#6a8578" }}>
        —
      </span>
    );
  } else if (!accepted) {
    cell = (
      <span className="ep-cell-center" style={{ fontSize: 11, color: "#6a8578" }}>
        —
      </span>
    );
  } else if (bypassed) {
    cell = (
      <span className="ep-cell-center">
        <span
          className="ep-payment-bypassed"
          data-testid="inbox-payment-bypassed"
          title={gate?.reason || "Payment Bypassed"}
        >
          Payment Bypassed
        </span>
      </span>
    );
  } else if (openNormal) {
    cell = (
      <span className="ep-cell-center">
        <span
          className="ep-payment-open"
          data-testid="inbox-payment-open"
          title="Gate payment sudah terbuka"
        >
          Gate Terbuka
        </span>
      </span>
    );
  } else {
    cell = (
      <span className="ep-cell-center">
        <button
          type="button"
          className={`ep-btn${phase === "loading" ? " is-busy" : ""}`}
          style={{ padding: "4px 8px", fontSize: 11 }}
          disabled={phase === "loading"}
          aria-busy={phase === "loading"}
          data-testid="inbox-bypass-payment-btn"
          data-po-id={row.accurateId || ""}
          data-po-no={row.poNo || row.id}
          title="Bypass Payment Gate (izin Master)"
          onClick={onOpen}
        >
          Bypass Payment
        </button>
      </span>
    );
  }

  return (
    <>
      {cell}
      <InboxBypassPaymentModal
        open={modalOpen}
        row={row}
        busy={phase === "loading"}
        canConfirm={canBypass && accepted && apiPo && !bypassed && !openNormal}
        onClose={() => {
          if (phase === "loading") return;
          setModalOpen(false);
        }}
        onConfirm={(reason) => void onConfirm(reason)}
      />
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

export const __test__ = {
  isBypassed,
  isPaymentOpenNormal,
  safeErrorMessage,
};
