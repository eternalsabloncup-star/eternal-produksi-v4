import { describe, expect, it } from "vitest";
import source from "./DesignApprovalPage.tsx?raw";

/**
 * Guard: public Approval Design must reuse PR#51 signature helpers
 * (CSS width ≠ bitmap causes skewed strokes on mobile).
 */
describe("DesignApprovalPage signature contract", () => {
  it("imports canvasPointerPos and syncSignatureCanvasSize", () => {
    expect(source).toMatch(/canvasPointerPos/);
    expect(source).toMatch(/syncSignatureCanvasSize/);
    expect(source).toMatch(/from ["'].*signatureCanvas["']/);
  });

  it("uses ep-signature-wrap and pointer capture lifecycle", () => {
    expect(source).toMatch(/ep-signature-wrap/);
    expect(source).toMatch(/setPointerCapture/);
    expect(source).toMatch(/releasePointerCapture/);
  });

  it("guards double submit while submitting", () => {
    expect(source).toMatch(/if \(submitting\) return/);
    expect(source).toMatch(/aria-busy=\{submitting\}/);
  });
});
