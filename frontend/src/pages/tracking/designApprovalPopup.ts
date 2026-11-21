/** Helpers dismiss + eligibility popup Approval Design (Live Tracking). */

import type { DesignApprovalEligibility } from "../../api/tracking";

/** sessionStorage key non-sensitif — per PO slug + design version. */
export function designApprovalDismissKey(
  poSlug: string,
  designVersion: number,
): string {
  return `elt.dap.dismiss:${poSlug}:v${designVersion}`;
}

export function isDesignApprovalDismissed(
  poSlug: string,
  designVersion: number,
): boolean {
  if (typeof sessionStorage === "undefined") return false;
  try {
    return sessionStorage.getItem(designApprovalDismissKey(poSlug, designVersion)) === "1";
  } catch {
    return false;
  }
}

export function dismissDesignApproval(poSlug: string, designVersion: number): void {
  if (typeof sessionStorage === "undefined") return;
  try {
    sessionStorage.setItem(designApprovalDismissKey(poSlug, designVersion), "1");
  } catch {
    // quota / private mode — popup may reappear; acceptable fail-soft
  }
}

/** Backend adalah source-of-truth; frontend tidak menebak dari milestone. */
export function shouldShowDesignApprovalPopup(
  eligibility: DesignApprovalEligibility | null | undefined,
  poSlug: string,
): boolean {
  if (!eligibility?.eligible) return false;
  if (!eligibility.pending_approval) return false;
  if (eligibility.link_status !== "active") return false;
  const version = eligibility.design_version;
  if (version == null || !Number.isFinite(version)) return false;
  if (isDesignApprovalDismissed(poSlug, version)) return false;
  return true;
}
