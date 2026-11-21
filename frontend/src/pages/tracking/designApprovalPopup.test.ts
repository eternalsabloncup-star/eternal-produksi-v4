import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  designApprovalDismissKey,
  dismissDesignApproval,
  isDesignApprovalDismissed,
  shouldShowDesignApprovalPopup,
} from "./designApprovalPopup";

function installMemorySessionStorage() {
  const store = new Map<string, string>();
  const api = {
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null;
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
    removeItem(key: string) {
      store.delete(key);
    },
    clear() {
      store.clear();
    },
    key(i: number) {
      return [...store.keys()][i] ?? null;
    },
    get length() {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, "sessionStorage", {
    value: api,
    configurable: true,
  });
}

describe("designApprovalPopup", () => {
  beforeEach(() => {
    installMemorySessionStorage();
  });

  afterEach(() => {
    sessionStorage.clear();
  });

  it("no link / not eligible → no popup", () => {
    expect(
      shouldShowDesignApprovalPopup(
        {
          eligible: false,
          pending_approval: false,
          design_version: null,
          expires_at: null,
          link_status: "none",
        },
        "so-1",
      ),
    ).toBe(false);
  });

  it("active pending → popup", () => {
    expect(
      shouldShowDesignApprovalPopup(
        {
          eligible: true,
          pending_approval: true,
          design_version: 1,
          expires_at: "2026-11-22T00:00:00Z",
          link_status: "active",
        },
        "so-1",
      ),
    ).toBe(true);
  });

  it("approved / expired → no popup", () => {
    expect(
      shouldShowDesignApprovalPopup(
        {
          eligible: false,
          pending_approval: false,
          design_version: 1,
          expires_at: null,
          link_status: "approved",
        },
        "so-1",
      ),
    ).toBe(false);
    expect(
      shouldShowDesignApprovalPopup(
        {
          eligible: false,
          pending_approval: false,
          design_version: 1,
          expires_at: null,
          link_status: "unavailable",
        },
        "so-1",
      ),
    ).toBe(false);
  });

  it("dismiss X/Nanti Saja blocks poll reopen for same version", () => {
    const elig = {
      eligible: true,
      pending_approval: true,
      design_version: 1,
      expires_at: "2026-11-22T00:00:00Z",
      link_status: "active" as const,
    };
    expect(shouldShowDesignApprovalPopup(elig, "so-1")).toBe(true);
    dismissDesignApproval("so-1", 1);
    expect(isDesignApprovalDismissed("so-1", 1)).toBe(true);
    expect(sessionStorage.getItem(designApprovalDismissKey("so-1", 1))).toBe("1");
    expect(shouldShowDesignApprovalPopup(elig, "so-1")).toBe(false);
  });

  it("new design version reopens popup", () => {
    dismissDesignApproval("so-1", 1);
    expect(
      shouldShowDesignApprovalPopup(
        {
          eligible: true,
          pending_approval: true,
          design_version: 2,
          expires_at: "2026-11-22T00:00:00Z",
          link_status: "active",
        },
        "so-1",
      ),
    ).toBe(true);
  });
});
