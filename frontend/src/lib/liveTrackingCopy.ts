/**
 * Inbox Live Tracking: generate sekali, salin berkali-kali via reveal URL.
 * Raw token tidak disimpan di localStorage / sessionStorage.
 */

import {
  fetchActiveTrackingUrl,
  generateTrackingLink,
  listTrackingLinks,
  regenerateTrackingLink,
  TrackingApiError,
  type TrackingLinkRow,
} from "../api/tracking";

export type LiveTrackingCopyErrorCode =
  | "missing_po"
  | "api"
  | "permission"
  | "clipboard"
  | "busy"
  | "already_created"
  | "legacy_unrecoverable"
  | "not_found";

export class LiveTrackingCopyError extends Error {
  readonly code: LiveTrackingCopyErrorCode;
  /** True when generate/regenerate succeeded but a later step (clipboard) failed. */
  readonly linkCreated: boolean;
  readonly linkId?: string;
  constructor(
    code: LiveTrackingCopyErrorCode,
    message: string,
    opts?: { linkCreated?: boolean; linkId?: string },
  ) {
    super(message);
    this.name = "LiveTrackingCopyError";
    this.code = code;
    this.linkCreated = Boolean(opts?.linkCreated);
    this.linkId = opts?.linkId;
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
  if (err instanceof TrackingApiError) {
    const detail = err.detail;
    const code =
      typeof detail === "object" && detail?.code ? String(detail.code) : "";
    const msg = err.message;
    if (err.status === 403 || /izin|forbidden|akses ditolak/i.test(msg)) {
      return new LiveTrackingCopyError("permission", msg);
    }
    if (code === "legacy_unrecoverable" || err.status === 409) {
      const linkId =
        typeof detail === "object" ? detail.link_id : undefined;
      if (code === "legacy_unrecoverable" || detail && typeof detail === "object" && detail.requires_explicit_recovery) {
        return new LiveTrackingCopyError("legacy_unrecoverable", msg, {
          linkCreated: true,
          linkId,
        });
      }
      return new LiveTrackingCopyError("already_created", msg, {
        linkCreated: true,
      });
    }
    if (err.status === 404) {
      return new LiveTrackingCopyError("not_found", msg);
    }
    return new LiveTrackingCopyError("api", msg);
  }
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
  mode: "generate" | "copy" | "recover";
};

function absoluteFromPath(path: string, origin: string): string {
  if (!path.startsWith("/t/")) {
    throw new LiveTrackingCopyError(
      "api",
      "URL tracking tidak valid dari server.",
      { linkCreated: true },
    );
  }
  return `${origin.replace(/\/$/, "")}${path}`;
}

async function copyPath(
  path: string,
  origin: string,
  poKey: string,
  mode: GenerateAndCopyResult["mode"],
  opts?: { linkCreated?: boolean },
): Promise<GenerateAndCopyResult> {
  const absoluteUrl = absoluteFromPath(path, origin);
  try {
    await copyTextToClipboard(absoluteUrl);
  } catch (err) {
    if (err instanceof LiveTrackingCopyError && err.code === "clipboard") {
      throw new LiveTrackingCopyError("clipboard", err.message, {
        linkCreated: opts?.linkCreated ?? mode !== "copy",
      });
    }
    throw err;
  }
  return { absoluteUrl, path, poKey, mode };
}

function beginFlight(key: string): void {
  if (inFlight.has(key)) {
    throw new LiveTrackingCopyError(
      "busy",
      "Permintaan Live Tracking untuk PO ini sedang diproses.",
    );
  }
  inFlight.add(key);
}

/**
 * Salin link aktif yang sama (GET reveal). Tidak POST generate.
 */
export async function copyActiveLiveTrackingLink(
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
  beginFlight(key);
  try {
    let revealed;
    try {
      revealed = await fetchActiveTrackingUrl(key);
    } catch (err) {
      throw mapApiError(err);
    }
    return await copyPath(revealed.path, origin, key, "copy", {
      linkCreated: true,
    });
  } finally {
    inFlight.delete(key);
  }
}

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
  beginFlight(key);
  try {
    let items: TrackingLinkRow[];
    try {
      items = await listTrackingLinks(key);
    } catch (err) {
      throw mapApiError(err);
    }
    if (hasActiveTrackingLink(items)) {
      // Prefer reveal of existing sealed link over rejecting.
      try {
        const revealed = await fetchActiveTrackingUrl(key);
        return await copyPath(revealed.path, origin, key, "copy", {
          linkCreated: true,
        });
      } catch (err) {
        const mapped = mapApiError(err);
        if (mapped.code === "legacy_unrecoverable") throw mapped;
        throw new LiveTrackingCopyError(
          "already_created",
          "Link Live Tracking sudah dibuat untuk PO ini.",
          { linkCreated: true },
        );
      }
    }

    let path = "";
    try {
      const gen = await generateTrackingLink(key);
      path = gen.path;
    } catch (err) {
      throw mapApiError(err);
    }

    return await copyPath(path, origin, key, "generate", { linkCreated: true });
  } finally {
    inFlight.delete(key);
  }
}

/**
 * Pemulihan terkendali untuk link hash-only legacy.
 * Wajib confirmed === true (persetujuan eksplisit operator).
 */
export async function recoverAndCopyLiveTrackingLink(
  poKey: string,
  opts: { confirmed: boolean },
  origin: string = typeof window !== "undefined" ? window.location.origin : "",
): Promise<GenerateAndCopyResult> {
  const key = String(poKey || "").trim();
  if (!key) {
    throw new LiveTrackingCopyError(
      "missing_po",
      "Identitas PO tidak tersedia pada baris ini.",
    );
  }
  if (!opts.confirmed) {
    throw new LiveTrackingCopyError(
      "legacy_unrecoverable",
      "Pemulihan dibatalkan — regenerate memerlukan persetujuan eksplisit.",
      { linkCreated: true },
    );
  }
  beginFlight(key);
  try {
    let path = "";
    try {
      const gen = await regenerateTrackingLink(key);
      path = gen.path;
    } catch (err) {
      throw mapApiError(err);
    }
    return await copyPath(path, origin, key, "recover", { linkCreated: true });
  } finally {
    inFlight.delete(key);
  }
}

/**
 * Satu klik Inbox: buat+salin bila belum ada, atau salin link aktif yang sama.
 */
export async function createOrCopyLiveTrackingLink(
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
  beginFlight(key);
  try {
    try {
      const revealed = await fetchActiveTrackingUrl(key);
      return await copyPath(revealed.path, origin, key, "copy", {
        linkCreated: true,
      });
    } catch (err) {
      const mapped = mapApiError(err);
      if (mapped.code === "legacy_unrecoverable") throw mapped;
      if (mapped.code === "permission") throw mapped;
      if (mapped.code !== "not_found") throw mapped;
      // 404 / absent → generate baru
    }

    let path = "";
    try {
      const gen = await generateTrackingLink(key);
      path = gen.path;
    } catch (genErr) {
      const mapped = mapApiError(genErr);
      if (mapped.code === "already_created") {
        const revealed = await fetchActiveTrackingUrl(key).catch((e) => {
          throw mapApiError(e);
        });
        return await copyPath(revealed.path, origin, key, "copy", {
          linkCreated: true,
        });
      }
      throw mapped;
    }
    return await copyPath(path, origin, key, "generate", { linkCreated: true });
  } finally {
    inFlight.delete(key);
  }
}

export function isLiveTrackingCopyInFlight(poKey: string): boolean {
  return inFlight.has(String(poKey || "").trim());
}
