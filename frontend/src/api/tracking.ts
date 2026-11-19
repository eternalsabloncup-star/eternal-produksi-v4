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
  links?: TrackingLinkRow[];
  internal?: Record<string, unknown>;
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
    const data = (await response.json()) as { detail?: string };
    if (typeof data.detail === "string") return data.detail;
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
): Promise<GenerateLinkResponse> {
  const response = await fetch(
    `/api/tracking/${encodeURIComponent(accuratePoId)}/links/regenerate`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", ...csrfHeader() },
      body: "{}",
    },
  );
  if (!response.ok) throw new Error(await parseError(response));
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
