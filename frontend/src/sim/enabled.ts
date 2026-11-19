/**
 * Dev-only API simulation gate.
 * Production builds: import.meta.env.DEV === false → always off.
 */

export function isApiSimulationEnabled(
  env: ImportMetaEnv = import.meta.env,
): boolean {
  if (!env.DEV) return false;
  const raw = String(env.VITE_API_SIMULATION ?? "")
    .trim()
    .toLowerCase();
  // Explicit off.
  if (raw === "0" || raw === "false" || raw === "no" || raw === "off") {
    return false;
  }
  // Explicit on.
  if (raw === "1" || raw === "true" || raw === "yes" || raw === "on") {
    return true;
  }
  // Default DEV preview: simulation ON so Cursor uses API panels + fixtures
  // without requiring a committed .env.development.
  return raw === "";
}

export const API_SIMULATION_BANNER =
  "MODE SIMULASI API — komponen production, data fixture lokal.";
