import { describe, expect, it } from "vitest";
import { mapHeaderToPoRow } from "./ApiPoSource";
import {
  displayAccurateLabel,
  formatAccurateLabel,
} from "./formatAccurateLabel";
import {
  resolveInboxPoSourceMode,
  resolveShellDataMode,
  shellModeBadgeLabel,
} from "./resolveInboxMode";

describe("formatAccurateLabel", () => {
  it("formats payment terms object to business label", () => {
    expect(formatAccurateLabel({ id: 1, name: "Net 14" })).toBe("Net 14");
    expect(formatAccurateLabel("{'name': 'COD'}")).toBe("COD");
    expect(displayAccurateLabel({})).toBe("—");
    expect(formatAccurateLabel("[object Object]")).toBe("");
  });

  it("formats expedition object without raw JSON", () => {
    const label = formatAccurateLabel({ name: "JNE YES", id: 4 });
    expect(label).toBe("JNE YES");
    expect(label.includes("{")).toBe(false);
    expect(label.includes("object Object")).toBe(false);
  });
});

describe("resolveInboxPoSourceMode", () => {
  it("never defaults production unset to DEMO", () => {
    expect(resolveInboxPoSourceMode(undefined, { production: true })).toBe("api");
    expect(resolveInboxPoSourceMode("", { production: true })).toBe("api");
  });

  it("keeps DEMO for explicit demo and for development unset", () => {
    expect(resolveInboxPoSourceMode("demo", { production: true })).toBe("demo");
    expect(resolveInboxPoSourceMode(undefined, { production: false })).toBe("demo");
  });

  it("API mode has distinct badge from DEMO", () => {
    expect(shellModeBadgeLabel("api")).toBe("Accurate · MongoDB V4");
    expect(shellModeBadgeLabel("demo")).toBe("● DEMO");
    expect(shellModeBadgeLabel("api")).not.toBe(shellModeBadgeLabel("demo"));
    expect(resolveShellDataMode("/app/inbox", "api")).toBe("api");
    expect(resolveShellDataMode("/app/control", "api")).toBe("demo");
    expect(resolveShellDataMode("/app/schedule", "api")).toBe("api");
    expect(resolveShellDataMode("/app/schedule", "demo")).toBe("demo");
    expect(resolveShellDataMode("/app/production-flow", "api")).toBe("demo");
    expect(resolveShellDataMode("/app/tasks/operator", "api")).toBe("api");
    expect(resolveShellDataMode("/app/tasks/operator", "demo")).toBe("demo");
    expect(resolveShellDataMode("/app/settings", "demo")).toBe("api");
    expect(resolveShellDataMode("/app/buffer-stock", "api")).toBe("demo");
    expect(resolveShellDataMode("/app/delivery", "api")).toBe("api");
    expect(resolveShellDataMode("/app/delivery", "demo")).toBe("demo");
    expect(resolveShellDataMode("/app/dashboard", "api")).toBe("shell");
    expect(resolveShellDataMode("/app/reports", "api")).toBe("shell");
  });
});

describe("mapHeaderToPoRow PO 95106", () => {
  it("maps two Cetak/Non-Cetak lines without inventing print progress", () => {
    const row = mapHeaderToPoRow({
      accurate_id: "95106",
      source_po_id: "accurate:95106",
      po_no: "PO/18/11/2026/000010",
      origin: "accurate",
      operational: {},
      header: {
        po_no: "PO/18/11/2026/000010",
        customer: { name: "Pelanggan Simulasi V4" },
        payment_terms: { name: "CBD" },
        expedition: { name: "Ekspedisi Internal" },
        po_date: "18/11/2026",
        items: [
          {
            line_id: "accurate-line:106318",
            accurate_line_id: "106318",
            item_code: "BRG-SIM-001",
            item_name: "Barang Simulasi Cetak",
            qty: 1000,
            unit: { name: "PCS" },
            process_raw: "Cetak",
            process_kind: "Cetak",
            color: "Hijau Tua",
          },
          {
            line_id: "accurate-line:106319",
            accurate_line_id: "106319",
            item_code: "BRG-SIM-002",
            item_name: "Barang Simulasi Tidak Cetak",
            qty: 50,
            unit: "PACK",
            process_raw: "Tidak Cetak",
            process_kind: "Non-Cetak",
            color: "Putih",
          },
        ],
      },
    });
    expect(row).not.toBeNull();
    expect(row!.accurateId).toBe("95106");
    expect(row!.poNo).toBe("PO/18/11/2026/000010");
    expect(row!.customer).toBe("Pelanggan Simulasi V4");
    expect(row!.paymentTerms).toBe("CBD");
    expect(row!.expedition).toBe("Ekspedisi Internal");
    expect(row!.items).toHaveLength(2);
    expect(row!.items.map((i) => i.processKind)).toEqual(["Cetak", "Non-Cetak"]);
    expect(row!.printed).toBe(0);
    expect(row!.qc).toBe(0);
    expect(row!.status).toBe("PO Baru");
    expect(row!.origin).toBe("accurate");
  });

  it("uses operational status when present without inventing schedule fields", () => {
    const row = mapHeaderToPoRow({
      accurate_id: "95106",
      po_no: "PO/18/11/2026/000010",
      operational: { status: "PO Masuk" },
      header: {
        po_no: "PO/18/11/2026/000010",
        customer: "X",
        items: [],
      },
    });
    expect(row!.status).toBe("PO Masuk");
    expect(row!.printed).toBe(0);
  });
});

describe("ApiPoSource no silent DEMO fallback", () => {
  it("getInboxPoSource stays on api when mode api even if unused", async () => {
    const { getInboxPoSource } = await import("./DemoPoSource");
    const source = getInboxPoSource("api");
    expect(source.kind).toBe("api");
    const demo = getInboxPoSource("demo");
    expect(demo.kind).toBe("demo");
  });
});
