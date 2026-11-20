import { getCsrfToken } from "../api";

export type PoAcceptance = {
  status: "pending" | "accepted" | string;
  accepted_by?: string | null;
  accepted_at?: string | null;
  inbox_status?: string;
};

export type AcceptPoResponse = {
  accurate_po_id: string;
  duplicate: boolean;
  acceptance: PoAcceptance;
  workflow: {
    created_task_keys?: string[];
    skipped?: string[];
  };
  po: Record<string, unknown>;
};

export class PoAcceptError extends Error {
  readonly status: number;
  /** true bila request timeout/putus — jangan auto-retry accept. */
  readonly uncertain: boolean;

  constructor(
    message: string,
    status = 0,
    opts?: { uncertain?: boolean },
  ) {
    super(message);
    this.name = "PoAcceptError";
    this.status = status;
    this.uncertain = Boolean(opts?.uncertain);
  }
}

/** Timeout Terima PO — tidak auto-retry; lanjut reconcile status. */
export const ACCEPT_PO_TIMEOUT_MS = 25_000;

async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: string };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // ignore
  }
  return `Gagal menerima PO (HTTP ${res.status}).`;
}

function isAbortError(err: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      err instanceof DOMException &&
      err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

export async function acceptPo(
  accuratePoId: string,
  opts?: { timeoutMs?: number; signal?: AbortSignal },
): Promise<AcceptPoResponse> {
  const csrf = getCsrfToken();
  const timeoutMs = opts?.timeoutMs ?? ACCEPT_PO_TIMEOUT_MS;
  const controller = new AbortController();
  const onParentAbort = () => controller.abort();
  if (opts?.signal) {
    if (opts.signal.aborted) controller.abort();
    else opts.signal.addEventListener("abort", onParentAbort, { once: true });
  }
  const timer =
    timeoutMs > 0
      ? window.setTimeout(() => controller.abort(), timeoutMs)
      : 0;

  try {
    let res: Response;
    try {
      res = await fetch(
        `/api/po/${encodeURIComponent(accuratePoId)}/accept`,
        {
          method: "POST",
          credentials: "include",
          signal: controller.signal,
          headers: {
            "Content-Type": "application/json",
            ...(csrf ? { "X-CSRF-Token": csrf } : {}),
          },
        },
      );
    } catch (err) {
      if (isAbortError(err)) {
        throw new PoAcceptError(
          "Status penerimaan belum dapat dipastikan",
          0,
          { uncertain: true },
        );
      }
      throw new PoAcceptError(
        err instanceof Error ? err.message : "Koneksi terputus.",
        0,
        { uncertain: true },
      );
    }
    if (!res.ok) throw new PoAcceptError(await readError(res), res.status);
    return (await res.json()) as AcceptPoResponse;
  } finally {
    if (timer) window.clearTimeout(timer);
    opts?.signal?.removeEventListener("abort", onParentAbort);
  }
}

/** Baca status acceptance otoritatif (GET PO) — tanpa mutasi. */
export async function fetchPoAcceptanceStatus(
  accuratePoId: string,
): Promise<PoAcceptance | null> {
  let res: Response;
  try {
    res = await fetch(`/api/po/${encodeURIComponent(accuratePoId)}`, {
      credentials: "include",
    });
  } catch {
    return null;
  }
  if (!res.ok) return null;
  try {
    const doc = (await res.json()) as {
      acceptance?: PoAcceptance | null;
    };
    const block = doc.acceptance;
    if (!block || typeof block !== "object") return null;
    return {
      status: String(block.status || "pending"),
      accepted_by: block.accepted_by ?? null,
      accepted_at: block.accepted_at ?? null,
      inbox_status: block.inbox_status,
    };
  } catch {
    return null;
  }
}
