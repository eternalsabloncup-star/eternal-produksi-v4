/**
 * Kapabilitas filter Inbox Accurate (API) vs DEMO.
 * Filter yang tidak didukung harus dinonaktifkan di UI — jangan terlihat aktif tapi diabaikan.
 */

export type InboxSourceMode = "demo" | "api";

export type InboxFilterKey = "q" | "urgent" | "branch" | "status" | "machine";

/** Filter yang benar-benar diterapkan ke `/api/po` list. */
export const INBOX_API_SUPPORTED_FILTERS = ["q", "urgent"] as const;

export function inboxFilterEnabled(
  mode: InboxSourceMode,
  key: InboxFilterKey,
): boolean {
  if (mode === "demo") return true;
  return key === "q" || key === "urgent";
}

export type InboxListQueryLike = {
  page?: number;
  pageSize?: number;
  q?: string;
  urgent?: boolean;
  status?: string;
  branch?: string;
  machine?: string;
};

/**
 * Bangun query string list API.
 * Hanya filter yang didukung — branch/status/machine sengaja tidak dikirim.
 */
export function buildInboxApiListParams(query: InboxListQueryLike): URLSearchParams {
  const params = new URLSearchParams();
  const page = Math.max(1, query.page || 1);
  const pageSize = query.pageSize || 50;
  params.set("page", String(page));
  params.set("pageSize", String(pageSize));
  if (query.q?.trim()) params.set("q", query.q.trim());
  if (query.urgent) params.set("urgent", "true");
  params.set("sortBy", "updated_at");
  params.set("sortDir", "desc");
  return params;
}
