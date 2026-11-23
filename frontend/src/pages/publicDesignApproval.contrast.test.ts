/** @vitest-environment node */
// @ts-nocheck — reads CSS from disk; node types not in frontend tsconfig.
import { readFileSync } from "fs";
import { dirname, resolve } from "path";
import { fileURLToPath } from "url";
import { describe, expect, it } from "vitest";
import pageSource from "./DesignApprovalPage.tsx?raw";
import previewSource from "../components/PublicDesignEvidencePreview.tsx?raw";

const cssSource = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "./publicDesignApproval.css"),
  "utf8",
);

describe("public Design Approval contrast + preview contract", () => {
  it("scopes table header and signature label contrast under ep-public-approval", () => {
    expect(cssSource).toMatch(/\.ep-public-approval \.ep-table th/);
    expect(cssSource).toMatch(/background:\s*#1a4d3e/);
    expect(cssSource).toMatch(/color:\s*#f7faf8/);
    expect(cssSource).toMatch(/\.ep-public-approval \.ep-signature-label/);
    expect(cssSource).toMatch(/color:\s*#e8eee9/);
    expect(cssSource).toMatch(/max-width:\s*960px/);
    expect(cssSource).toMatch(/\.ep-signature-wrap[\s\S]*max-width:\s*560px/);
  });

  it("keeps reject panel mandatory reason + confirm + busy guards", () => {
    expect(pageSource).toMatch(/Alasan penolakan \(wajib\)/);
    expect(pageSource).toMatch(/reason\.length < 3/);
    expect(pageSource).toMatch(/window\.confirm/);
    expect(pageSource).toMatch(/if \(submitting\) return/);
    expect(pageSource).toMatch(/design-reject-panel/);
    expect(pageSource).toMatch(/Pastikan desain, warna, dan informasi produk/);
  });

  it("reuses PDF.js for in-app PDF preview without new tab", () => {
    expect(previewSource).toMatch(/pdfjs-dist\/build\/pdf\.mjs/);
    expect(previewSource).toMatch(/getDocument/);
    expect(previewSource).toMatch(/publicDesignEvidenceUrl/);
    expect(previewSource).toMatch(/URL\.createObjectURL/);
    expect(previewSource).not.toMatch(/window\.open/);
    expect(previewSource).toMatch(/document\.body\.style\.overflow/);
    expect(previewSource).toMatch(/fitToWidth|Muat lebar/);
    expect(previewSource).toMatch(/<img /);
  });
});
