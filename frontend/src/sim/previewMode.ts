import { resolveInboxPoSourceMode } from "../demo/resolveInboxMode";
import { isApiSimulationEnabled } from "./enabled";

/**
 * Preview Owner / Cursor: always production API panels unless
 * VITE_INBOX_PO_SOURCE is explicitly `demo` AND simulation is off.
 */
export function useProductionApiPanels(): boolean {
  const mode = resolveInboxPoSourceMode(import.meta.env.VITE_INBOX_PO_SOURCE);
  if (isApiSimulationEnabled()) return true;
  return mode === "api";
}

/** Fingerprint so Owner can confirm the exact preview server. */
export function previewFingerprint(): string {
  const started =
    (typeof window !== "undefined" &&
      (window as Window & { __ETERNAL_PREVIEW_STARTED__?: string })
        .__ETERNAL_PREVIEW_STARTED__) ||
    "";
  return `preview=127.0.0.1:5173${started ? ` · ${started}` : ""}`;
}
