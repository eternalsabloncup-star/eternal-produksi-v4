/**
 * Focused Tahap 01 checks for frontend PO domain (no Accurate mutation).
 * Run: node --experimental-strip-types is unavailable; use tsx via docker/host.
 */
import assert from "node:assert/strict";
import { createDemoPoSeed } from "../src/demo/fixtures.ts";
import {
  DemoPoSource,
  resolveInboxPoSourceMode,
} from "../src/demo/DemoPoSource.ts";
import { mapProcessFromSource } from "../src/domain/poProcessMapping.ts";

assert.equal(mapProcessFromSource("Cetak").ok, true);
assert.equal(mapProcessFromSource("Tidak Cetak").processKind, "Non-Cetak");
assert.equal(mapProcessFromSource("Polos").ok, false);
assert.equal(mapProcessFromSource("Cup PET 16oz + sablon").ok, false);

assert.equal(resolveInboxPoSourceMode(undefined), "demo");
assert.equal(resolveInboxPoSourceMode(""), "demo");
assert.equal(resolveInboxPoSourceMode("demo"), "demo");
assert.equal(resolveInboxPoSourceMode("api"), "api");
assert.equal(resolveInboxPoSourceMode("ACCURATE"), "api");

const seed = createDemoPoSeed();
assert.ok(seed.length > 0);

const multi = seed.find((p) => p.items.length >= 2);
assert.ok(multi, "butuh PO multi-item");
assert.equal(multi.origin, "demo");
assert.equal(multi.poNo, multi.id);
assert.ok(multi.sourcePoId.startsWith("demo:"));
const lineIds = new Set(multi.items.map((i) => i.lineId));
assert.equal(lineIds.size, multi.items.length, "lineId unik per item");
for (const it of multi.items) {
  assert.notEqual(it.lineId, it.code, "lineId ≠ kode barang");
  assert.ok(it.processKind === "Cetak" || it.processKind === "Non-Cetak");
}

const mixed = seed.find(
  (p) =>
    p.items.some((i) => i.processKind === "Cetak") &&
    p.items.some((i) => i.processKind === "Non-Cetak"),
);
assert.ok(mixed, "butuh PO kombinasi Cetak/Non-Cetak");

const source = new DemoPoSource(seed);
const a = await source.list({ page: 1, pageSize: 100 });
const b = await source.list({ page: 1, pageSize: 100 });
assert.equal(a.total, b.total);
assert.equal(a.items[0].sourcePoId, b.items[0].sourcePoId);
assert.deepEqual(
  a.items[0].items.map((i) => i.lineId),
  b.items[0].items.map((i) => i.lineId),
);

const byId = await source.getById(a.items[0].poNo);
assert.ok(byId);
assert.equal(byId.sourcePoId, a.items[0].sourcePoId);

const peek1 = source.peekIdentities();
const peek2 = source.peekIdentities();
assert.deepEqual(peek1, peek2);

assert.ok(a.items.every((r) => r.origin === "demo"));
assert.equal(a.source, "demo");

// Kolom Inbox existing tetap ada pada kontrak
const required = [
  "id",
  "poDate",
  "customer",
  "recipient",
  "address",
  "paymentTerms",
  "userName",
  "expedition",
  "etaShip",
  "shipZone",
  "seller",
  "urgent",
  "bufferLabel",
  "notes",
  "itemCount",
  "totalQtyDisplay",
  "items",
];
for (const key of required) {
  assert.ok(key in a.items[0], `kolom/field hilang: ${key}`);
}

console.log(
  JSON.stringify(
    {
      ok: true,
      poCount: seed.length,
      sampleMulti: { poNo: multi.poNo, items: multi.items.length },
      sampleMixed: {
        poNo: mixed.poNo,
        kinds: mixed.items.map((i) => i.processKind),
      },
    },
    null,
    2,
  ),
);
