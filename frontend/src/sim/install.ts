import { isApiSimulationEnabled } from "./enabled";
import { handleSimRequest, type SimSession } from "./handlers";

declare global {
  interface Window {
    __ETERNAL_API_SIMULATION__?: boolean;
  }
}

const session: SimSession = { active: true };

/** Install fetch interceptor. No-op outside DEV simulation. */
export function installApiSimulation(): boolean {
  if (!isApiSimulationEnabled()) return false;
  if (typeof window === "undefined") return false;
  if (window.__ETERNAL_API_SIMULATION__) return true;

  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
    const handled = handleSimRequest(input, init, session);
    if (handled) return Promise.resolve(handled);
    return nativeFetch(input, init);
  };

  document.cookie = "eternal_csrf=sim-csrf-eternal-v4; path=/; SameSite=Lax";
  window.__ETERNAL_API_SIMULATION__ = true;
  (
    window as Window & { __ETERNAL_PREVIEW_STARTED__?: string }
  ).__ETERNAL_PREVIEW_STARTED__ = new Date().toISOString();
  return true;
}

export function getSimSession(): SimSession {
  return session;
}
