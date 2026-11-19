import { API_SIMULATION_BANNER, isApiSimulationEnabled } from "./enabled";
import { previewFingerprint } from "./previewMode";

/** Banner wajib saat preview memakai komponen API + fixture lokal. */
export function ApiSimulationBanner() {
  if (!isApiSimulationEnabled()) return null;
  const fp = previewFingerprint();
  return (
    <div
      className="ep-demo-banner"
      role="status"
      data-testid="api-simulation-banner"
      data-preview-host="127.0.0.1:5173"
      style={{
        background: "rgba(47, 107, 92, 0.35)",
        borderColor: "rgba(118, 210, 192, 0.45)",
      }}
    >
      {API_SIMULATION_BANNER} · {fp}
    </div>
  );
}
