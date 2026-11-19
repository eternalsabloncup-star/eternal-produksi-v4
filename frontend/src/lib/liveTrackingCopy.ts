/**
 * Inbox Live Tracking: generate existing API once, then copy.
 * Status "sudah dibuat" comes from listTrackingLinks (backend), not sessionStorage.
 * No regenerate / revoke / encrypted storage.
 */

import {
  generateTrackingLink,
  listTrackingLinks,
  type TrackingLinkRow,
} from "../api/tracking";

export type LiveTrackingCopyErrorCode =
  | "missing_po"
  | "api"
  | "permission"
  | "clipboard"
  | "busy"
  | "already_created";

export class LiveTrackingCopyError extends Error {
  readonly code: LiveTrackingCopyErrorCode;
  /** True when generate succeeded but a later step (clipboard) failed. */
  readonly linkCreated: boolean;
  constructor(
    code: LiveTrackingCopyErrorCode,
    message: string,
    opts?: { linkCreated?: boolean },
  ) {
    super(message);
    this.name = "LiveTrackingCopyError";
    this.code = code;
    this.linkCreated = Boolean(opts?.linkCreated);
  }
}

const inFlight = new Set<string>();

export function hasActiveTrackingLink(items: TrackingLinkRow[]): boolean {
  return items.some((l) => l.status === "active");
}

export async function fetchTrackingLinkActive(poKey: string): Promise<boolean> {
  const key = String(poKey || "").trim();
  if (!key) return false;
  const items = await listTrackingLinks(key);
  return hasActiveTrackingLink(items);
}

export async function copyTextToClipboard(text: string): Promise<void> {
  if (!navigator.clipboard?.writeText) {
    throw new LiveTrackingCopyError(
      "clipboard",
      "Clipboard tidak tersedia di browser ini.",
    );
  }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    throw new LiveTrackingCopyError(
      "clipboard",
      "Gagal menyalin link. Izinkan akses clipboard lalu coba lagi.",
    );
  }
}

function mapApiError(err: unknown): LiveTrackingCopyError {
  if (err instanceof LiveTrackingCopyError) return err;
  const msg = err instanceof Error ? err.message : "Gagal memproses Live Tracking.";
  const lower = msg.toLowerCase();
  if (
    lower.includes("izin") ||
    lower.includes("akses ditolak") ||
    lower.includes("403") ||
    lower.includes("forbidden")
  ) {
    return new LiveTrackingCopyError("permission", msg);
  }
  if (
    lower.includes("sudah ada") ||
    lower.includes("already") ||
    lower.includes("exists") ||
    lower.includes("409")
  ) {
    return new LiveTrackingCopyError("already_created", msg, { linkCreated: true });
  }
  return new LiveTrackingCopyError("api", msg);
}

export type GenerateAndCopyResult = {
  absoluteUrl: string;
  path: string;
  poKey: string;
};

/**
 * Generate link via existing POST, then copy absolute URL.
 * Does not regenerate. Does not call revoke.
 */
export async function generateAndCopyLiveTrackingLink(
  poKey: string,
  origin: string = typeof window !== "undefined" ? window.location.origin : "",
): Promise<GenerateAndCopyResult> {
  const key = String(poKey || "").trim();
  if (!key) {
    throw new LiveTrackingCopyError(
      "missing_po",
      "Identitas PO tidak tersedia pada baris ini.",
    );
  }
  if (inFlight.has(key)) {
    throw new LiveTrackingCopyError(
      "busy",
      "Permintaan Live Tracking untuk PO ini sedang diproses.",
    );
  }
  inFlight.add(key);
  try {
    let items: TrackingLinkRow[];
    try {
      items = await listTrackingLinks(key);
    } catch (err) {
      throw mapApiError(err);
    }
    if (hasActiveTrackingLink(items)) {
      throw new LiveTrackingCopyError(
        "already_created",
        "Link Live Tracking sudah dibuat untuk PO ini.",
        { linkCreated: true },
      );
    }

    let path = "";
    try {
      const gen = await generateTrackingLink(key);
      path = gen.path;
    } catch (err) {
      throw mapApiError(err);
    }

    if (!path.startsWith("/t/")) {
      throw new LiveTrackingCopyError(
        "api",
        "URL tracking tidak valid dari server.",
        { linkCreated: true },
      );
    }
    const absoluteUrl = `${origin.replace(/\/$/, "")}${path}`;
    try {
      await copyTextToClipboard(absoluteUrl);
    } catch (err) {
      if (err instanceof LiveTrackingCopyError && err.code === "clipboard") {
        throw new LiveTrackingCopyError("clipboard", err.message, {
          linkCreated: true,
        });
      }
      throw err;
    }
    return { absoluteUrl, path, poKey: key };
  } finally {
    inFlight.delete(key);
  }
}

export function isLiveTrackingCopyInFlight(poKey: string): boolean {
  return inFlight.has(String(poKey || "").trim());
}
