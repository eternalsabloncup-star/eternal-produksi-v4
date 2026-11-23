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
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.toString()
          : input.url;
    const handled = handleSimRequest(input, init, session);
    if (handled) {
      // Dev-only: delay public approve for SLOW tokens so busy/double-submit is observable.
      if (
        /\/api\/public\/design-approval\/[^/]*SLOW[^/]*\/approve/i.test(url) &&
        String(init?.method || "GET").toUpperCase() === "POST"
      ) {
        return new Promise<Response>((resolve) => {
          setTimeout(() => resolve(handled), 700);
        });
      }
      return Promise.resolve(handled);
    }
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
