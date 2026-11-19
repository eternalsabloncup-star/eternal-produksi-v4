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
  constructor(message: string, status = 0) {
    super(message);
    this.name = "PoAcceptError";
    this.status = status;
  }
}

async function readError(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: string };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    // ignore
  }
  return `Gagal menerima PO (HTTP ${res.status}).`;
}

export async function acceptPo(accuratePoId: string): Promise<AcceptPoResponse> {
  const csrf = getCsrfToken();
  const res = await fetch(`/api/po/${encodeURIComponent(accuratePoId)}/accept`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
    },
  });
  if (!res.ok) throw new PoAcceptError(await readError(res), res.status);
  return (await res.json()) as AcceptPoResponse;
}
