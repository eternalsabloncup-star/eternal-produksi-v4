/** Live Tracking API client — publik bertoken + manajemen internal. */

export type MilestoneState =
  | "completed"
  | "in_progress"
  | "pending"
  | "not_applicable"
  | "attention_required"
  | "unknown";

export type TrackingMilestone = {
  id: string;
  seq: number;
  label: string;
  state: MilestoneState;
  detail: string;
  timestamp: string | null;
  relevant: boolean;
  sources?: string[];
  gaps?: string[];
};

export type TrackingDocumentCategory = {
  category: string;
  label: string;
  status: "available" | "waiting" | "locked";
  items: Array<Record<string, unknown>>;
  reason?: string | null;
  note?: string | null;
};

export type TrackingPayload = {
  view: "customer" | "internal";
  generated_at: string;
  order: {
    accurate_po_id: string | null;
    po_no: string;
    customer: string;
    po_date: string;
    eta_ship: string | null;
    has_print_items: boolean;
    status_label: string;
    last_updated_at: string | null;
    summary: {
      customer: string;
      po_no: string;
      po_date: string;
      eta_ship: string;
      item_count: number;
      items: Array<{
        item_code: string;
        item_name: string;
        qty: number;
        unit: string;
        process_kind: string | null;
        color: string;
      }>;
    };
  };
  progress: {
    milestone: { completed: number; relevant: number; ratio: number; label: string };
    production: {
      qty_po: number;
      qty_produced: number | null;
      qty_qc_passed: number | null;
      ratio: number | null;
      source: string | null;
    };
    shipping: {
      qty_shipped: number | null;
      qty_remaining: number | null;
      ratio: number | null;
      source: string | null;
    };
    active_milestone: TrackingMilestone | null;
    order_complete: boolean;
    completion_blocked_reason: string | null;
  };
  milestones: TrackingMilestone[];
  documents: TrackingDocumentCategory[];
  help: {
    title: string;
    hours: string;
    phone: string | null;
    email: string;
    whatsapp: string | null;
    note: string;
  };
  polling: { interval_ms: number; strategy: string };
  link?: {
    status: string;
    scopes: string[];
    expires_at: string | null;
    customer_slug: string;
    po_slug: string;
  };
  /** Eligibility popup Approval Design — tanpa raw approval token. */
  design_approval?: DesignApprovalEligibility;
  links?: TrackingLinkRow[];
  internal?: Record<string, unknown>;
};

export type DesignApprovalEligibility = {
  eligible: boolean;
  pending_approval: boolean;
  design_version: number | null;
  expires_at: string | null;
  link_status: "none" | "active" | "approved" | "unavailable" | string;
};

export type DesignApprovalResolveResult = {
  path: string;
  design_version: number;
  expires_at: string | null;
};

export type TrackingLinkRow = {
  id: string;
  accurate_po_id: string;
  po_no: string;
  customer_name: string;
  customer_slug: string;
  po_slug: string;
  status: "active" | "revoked" | "expired";
  scopes: string[];
  created_at: string;
  created_by: string | null;
  revoked_at: string | null;
  expires_at: string | null;
  access_count: number;
  last_accessed_at: string | null;
  /** True when server can re-reveal the public path (sealed token present). */
  url_recoverable?: boolean;
};

export type ActiveTrackingUrlResponse = {
  status: "ok";
  path: string;
  link_id: string;
  accurate_po_id: string;
  customer_slug: string;
  po_slug: string;
  url_recoverable: boolean;
};

export type TrackingApiErrorDetail = {
  code?: string;
  message?: string;
  link_id?: string;
  accurate_po_id?: string;
  requires_explicit_recovery?: boolean;
};

export type GenerateLinkResponse = {
  token: string;
  path: string;
  link: TrackingLinkRow;
  accurate_po_id: string;
  po_no: string;
  customer_slug: string;
  po_slug: string;
  revoked_previous?: number;
};

async function parseError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as {
      detail?: string | TrackingApiErrorDetail;
    };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail === "object") {
      const msg = data.detail.message || data.detail.code;
      if (msg) return String(msg);
    }
  } catch {
    // ignore
  }
  return "Terjadi kesalahan. Silakan coba lagi.";
}

export class TrackingApiError extends Error {
  readonly status: number;
  readonly detail: TrackingApiErrorDetail | string;
  constructor(
    status: number,
    detail: TrackingApiErrorDetail | string,
    fallbackMessage: string,
  ) {
    const message =
      typeof detail === "string"
        ? detail
        : detail.message || detail.code || fallbackMessage;
    super(message);
    this.name = "TrackingApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function parseErrorDetail(
  response: Response,
): Promise<TrackingApiErrorDetail | string> {
  try {
    const data = (await response.json()) as {
      detail?: string | TrackingApiErrorDetail;
    };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail === "object") return data.detail;
  } catch {
    // ignore
  }
  return "Terjadi kesalahan. Silakan coba lagi.";
}

function csrfHeader(): HeadersInit {
  const match = document.cookie
    .split(";")
    .map((p) => p.trim())
    .find((p) => p.startsWith("eternal_csrf="));
  const csrf = match ? decodeURIComponent(match.slice("eternal_csrf=".length)) : null;
  return csrf ? { "X-CSRF-Token": csrf } : {};
}

export async function fetchPublicTracking(
  customerSlug: string,
  poSlug: string,
  token: string,
): Promise<TrackingPayload> {
  const response = await fetch(
    `/api/public/tracking/${encodeURIComponent(customerSlug)}/${encodeURIComponent(poSlug)}/${encodeURIComponent(token)}`,
    {
      method: "GET",
      credentials: "omit",
      headers: { Accept: "application/json" },
      cache: "no-store",
    },
  );
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  return (await response.json()) as TrackingPayload;
}

/** URL relatif PDF Faktur Accurate (token di path; jangan cache). */
export function publicInvoicePdfUrl(
  customerSlug: string,
  poSlug: string,
  token: string,
  invoiceId: string,
): string {
  return `/api/public/tracking/${encodeURIComponent(customerSlug)}/${encodeURIComponent(poSlug)}/${encodeURIComponent(token)}/invoices/${encodeURIComponent(invoiceId)}/pdf`;
}

/** Buka PDF Faktur — fetch no-store, lalu object URL. Fail-closed bila bukan PDF. */
export async function openPublicInvoicePdf(
  customerSlug: string,
  poSlug: string,
  token: string,
  invoiceId: string,
): Promise<void> {
  const response = await fetch(
    publicInvoicePdfUrl(customerSlug, poSlug, token, invoiceId),
    {
      method: "GET",
      credentials: "omit",
      headers: { Accept: "application/pdf" },
      cache: "no-store",
    },
  );
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  const ctype = (response.headers.get("content-type") || "").toLowerCase();
  const buf = await response.arrayBuffer();
  const head = new Uint8Array(buf.slice(0, 4));
  const magicOk =
    head.length >= 4 &&
    head[0] === 0x25 &&
    head[1] === 0x50 &&
    head[2] === 0x44 &&
    head[3] === 0x46; // %PDF
  if (!magicOk && !ctype.includes("application/pdf")) {
    throw new Error("Dokumen PDF tidak tersedia.");
  }
  const blob = new Blob([buf], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  const opened = window.open(url, "_blank", "noopener,noreferrer");
  if (!opened) {
    // Popup blocked — fallback unduh/navigasi sama tab.
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.click();
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

/** Resolve path /approval/:token — read-only; jangan hardcode URL. */
export async function resolvePublicDesignApproval(
  customerSlug: string,
  poSlug: string,
  token: string,
): Promise<DesignApprovalResolveResult> {
  const response = await fetch(
    `/api/public/tracking/${encodeURIComponent(customerSlug)}/${encodeURIComponent(poSlug)}/${encodeURIComponent(token)}/design-approval`,
    {
      method: "GET",
      credentials: "omit",
      headers: { Accept: "application/json" },
      cache: "no-store",
    },
  );
  if (!response.ok) {
    throw new Error(await parseError(response));
  }
  const body = (await response.json()) as DesignApprovalResolveResult;
  if (!body.path || typeof body.path !== "string" || !body.path.startsWith("/approval/")) {
    throw new Error("Link Approval Design tidak tersedia.");
  }
  return body;
}

export async function fetchInternalTracking(
  accuratePoId: string,
): Promise<TrackingPayload> {
  const response = await fetch(`/api/tracking/${encodeURIComponent(accuratePoId)}`, {
    method: "GET",
    credentials: "include",
  });
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()) as TrackingPayload;
}

export async function listTrackingLinks(
  accuratePoId: string,
): Promise<TrackingLinkRow[]> {
  const response = await fetch(
    `/api/tracking/${encodeURIComponent(accuratePoId)}/links`,
    { method: "GET", credentials: "include" },
  );
  if (!response.ok) throw new Error(await parseError(response));
  const body = (await response.json()) as { items: TrackingLinkRow[] };
  return body.items;
}

/** Reveal public path for the same active link (no generate / no regenerate). */
export async function fetchActiveTrackingUrl(
  accuratePoId: string,
): Promise<ActiveTrackingUrlResponse> {
  const response = await fetch(
    `/api/tracking/${encodeURIComponent(accuratePoId)}/links/active-url`,
    { method: "GET", credentials: "include" },
  );
  if (!response.ok) {
    throw new TrackingApiError(
      response.status,
      await parseErrorDetail(response),
      "Gagal mengambil URL Live Tracking.",
    );
  }
  return (await response.json()) as ActiveTrackingUrlResponse;
}

export async function generateTrackingLink(
  accuratePoId: string,
  ttlSeconds?: number,
): Promise<GenerateLinkResponse> {
  const response = await fetch(
    `/api/tracking/${encodeURIComponent(accuratePoId)}/links`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...csrfHeader() },
      body: JSON.stringify(ttlSeconds ? { ttl_seconds: ttlSeconds } : {}),
    },
  );
  if (!response.ok) throw new Error(await parseError(response));
  return (await response.json()) as GenerateLinkResponse;
}

export async function regenerateTrackingLink(
  accuratePoId: string,
  opts?: { expectedActiveLinkId?: string; ttlSeconds?: number },
): Promise<GenerateLinkResponse> {
  const body: Record<string, unknown> = {};
  if (opts?.ttlSeconds) body.ttl_seconds = opts.ttlSeconds;
  if (opts?.expectedActiveLinkId) {
    body.expected_active_link_id = opts.expectedActiveLinkId;
  }
  const response = await fetch(
    `/api/tracking/${encodeURIComponent(accuratePoId)}/links/regenerate`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...csrfHeader() },
      body: JSON.stringify(body),
    },
  );
  if (!response.ok) {
    throw new TrackingApiError(
      response.status,
      await parseErrorDetail(response),
      "Gagal regenerate Live Tracking.",
    );
  }
  return (await response.json()) as GenerateLinkResponse;
}

export async function revokeTrackingLink(
  linkId: string,
  reason?: string,
): Promise<void> {
  const response = await fetch(
    `/api/tracking/links/${encodeURIComponent(linkId)}/revoke`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...csrfHeader() },
      body: JSON.stringify({ reason: reason || null }),
    },
  );
  if (!response.ok) throw new Error(await parseError(response));
}

export function formatQty(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("id-ID").format(value);
}

export function formatTs(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat("id-ID", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Jakarta",
  }).format(d);
}

export const MILESTONE_STATE_LABEL: Record<MilestoneState, string> = {
  completed: "Selesai",
  in_progress: "Berjalan",
  pending: "Menunggu",
  not_applicable: "Tidak Berlaku",
  attention_required: "Perlu Perhatian",
  unknown: "Belum Diketahui",
};
