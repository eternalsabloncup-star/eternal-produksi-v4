import { useCallback, useEffect, useMemo, useState } from "react";
import {
  confirmPaintConversion,
  confirmPaintInbound,
  confirmPaintReturn,
  createPaintWithdrawal,
  fetchPaintAccurateStatus,
  fetchPaintCatalog,
  fetchPaintDocuments,
  fetchPaintPurposes,
  fetchPaintReady,
  fetchPaintReport,
  fetchPaintStock,
  finalizePaintStocktake,
  newPaintIdempotencyKey,
  recordPaintWaste,
  softDeletePaintDocument,
  startPaintStocktake,
  upsertPaintCatalog,
  upsertPaintPurpose,
  type PaintCatalogItem,
  type PaintDocument,
  type PaintPurpose,
  type PaintStockRow,
} from "../api/paint";
import { fetchBranches, type SettingsBranch } from "../api/settings";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";

type TabId =
  | "stock"
  | "inbound"
  | "withdraw"
  | "return"
  | "waste"
  | "reports"
  | "stocktake"
  | "master";

const TABS: Array<{ id: TabId; label: string }> = [
  { id: "stock", label: "Stok Cat" },
  { id: "inbound", label: "Cat Masuk" },
  { id: "withdraw", label: "Pengambilan" },
  { id: "return", label: "Pengembalian / Konversi" },
  { id: "waste", label: "Waste" },
  { id: "reports", label: "Laporan" },
  { id: "stocktake", label: "Stock Opname" },
  { id: "master", label: "Master & Pengaturan" },
];

function paintLabel(p: PaintCatalogItem): string {
  return `${p.brand} · ${p.kind} · ${p.color}`;
}

function grams(mg: number | undefined): string {
  if (mg == null) return "—";
  return (mg / 1000).toLocaleString("id-ID", { maximumFractionDigits: 3 });
}

export function PaintApiPanel() {
  const [tab, setTab] = useState<TabId>("stock");
  const [perms, setPerms] = useState<string[]>([]);
  const [accurateBanner, setAccurateBanner] = useState<string | null>(null);
  const [branches, setBranches] = useState<SettingsBranch[]>([]);
  const [branchId, setBranchId] = useState("");
  const [catalog, setCatalog] = useState<PaintCatalogItem[]>([]);
  const [purposes, setPurposes] = useState<PaintPurpose[]>([]);
  const [stock, setStock] = useState<PaintStockRow[]>([]);
  const [docs, setDocs] = useState<PaintDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [q, setQ] = useState("");

  const [inboundForm, setInboundForm] = useState({
    paint_id: "",
    qty_g: "",
    supplier: "",
    note: "",
  });
  const [withdrawForm, setWithdrawForm] = useState({
    used_by_email: "",
    purpose_id: "",
    paint_id: "",
    qty_g: "",
    accurate_po_id: "",
    note: "",
  });
  const [returnForm, setReturnForm] = useState({
    withdrawal_id: "",
    withdrawal_line_id: "",
    qty_g: "",
    note: "",
  });
  const [convForm, setConvForm] = useState({
    withdrawal_id: "",
    withdrawal_line_id: "",
    source_qty_g: "",
    destination_paint_id: "",
    verified_return_g: "",
    waste_g: "0",
  });
  const [wasteForm, setWasteForm] = useState({
    withdrawal_id: "",
    withdrawal_line_id: "",
    qty_g: "",
    reason: "",
  });
  const [masterPaint, setMasterPaint] = useState({
    brand: "",
    kind: "",
    color: "",
    is_black: false,
  });
  const [masterPurpose, setMasterPurpose] = useState({
    name: "",
    requires_po: false,
  });
  const [stocktakeId, setStocktakeId] = useState<string | null>(null);
  const [stocktakeLines, setStocktakeLines] = useState<
    Array<{ paint_id: string; system_g: number; counted_g: string }>
  >([]);
  const [reportRows, setReportRows] = useState<Array<Record<string, unknown>>>(
    [],
  );

  const can = useCallback(
    (perm: string) => perms.includes(perm),
    [perms],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ready, branchRes, cat, pur, st, documents, acc] = await Promise.all([
        fetchPaintReady(),
        fetchBranches().catch(() => ({ items: [] as SettingsBranch[] })),
        fetchPaintCatalog(false),
        fetchPaintPurposes(false),
        fetchPaintStock(branchId || undefined),
        fetchPaintDocuments({
          branch_id: branchId || undefined,
          limit: 200,
        }),
        fetchPaintAccurateStatus(),
      ]);
      setPerms(ready.permissions || []);
      setAccurateBanner(ready.accurate?.ui_banner || acc.banner || null);
      setBranches(branchRes.items || []);
      if (!branchId && (branchRes.items || []).length === 1) {
        setBranchId(branchRes.items[0].code);
      }
      setCatalog(cat.items || []);
      setPurposes(pur.items || []);
      setStock(st.items || []);
      setDocs(documents.items || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat Manajemen Cat.");
    } finally {
      setLoading(false);
    }
  }, [branchId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const catalogMap = useMemo(() => {
    const m = new Map<string, PaintCatalogItem>();
    for (const c of catalog) m.set(c.paint_id, c);
    return m;
  }, [catalog]);

  const filteredStock = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return stock;
    return stock.filter((row) => {
      const c = catalogMap.get(row.paint_id);
      const hay = [
        row.paint_id,
        row.branch_id,
        c?.brand,
        c?.kind,
        c?.color,
        String(row.qty_g ?? ""),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [stock, q, catalogMap]);

  const pageRows = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filteredStock.slice(start, start + pageSize);
  }, [filteredStock, page, pageSize]);

  const stockColumns: ColumnDef<PaintStockRow>[] = useMemo(
    () => [
      {
        id: "branch",
        label: "Cabang",
        hideable: true,
        filterable: true,
        filterType: "text",
        getFilterValue: (r) => r.branch_id,
        render: (r) => r.branch_id,
      },
      {
        id: "paint",
        label: "Cat",
        hideable: true,
        filterable: true,
        filterType: "text",
        getFilterValue: (r) => {
          const c = catalogMap.get(r.paint_id);
          return c ? paintLabel(c) : r.paint_id;
        },
        render: (r) => {
          const c = catalogMap.get(r.paint_id);
          return c ? paintLabel(c) : r.paint_id;
        },
      },
      {
        id: "qty",
        label: "Saldo (g)",
        hideable: true,
        filterType: "number",
        getFilterValue: (r) =>
          String(r.qty_g ?? (r.qty_mg != null ? r.qty_mg / 1000 : "")),
        getSortValue: (r) => r.qty_mg ?? 0,
        render: (r) => (r.qty_g != null ? String(r.qty_g) : grams(r.qty_mg)),
      },
      {
        id: "version",
        label: "Versi",
        hideable: true,
        getFilterValue: (r) => String(r.version ?? ""),
        render: (r) => String(r.version ?? "—"),
      },
    ],
    [catalogMap],
  );

  async function runAction(label: string, fn: () => Promise<unknown>) {
    setFlash(null);
    setError(null);
    try {
      await fn();
      setFlash(label);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operasi gagal.");
    }
  }

  const withdrawals = useMemo(
    () => docs.filter((d) => d.doc_type === "withdrawal" && d.status === "posted"),
    [docs],
  );

  const blackPaints = useMemo(
    () => catalog.filter((c) => c.is_black && c.is_active !== false),
    [catalog],
  );

  return (
    <div data-testid="paint-api-panel" className="ep-paint-panel">
      {accurateBanner ? (
        <p className="ep-settings-card-note" role="status">
          {accurateBanner}
        </p>
      ) : null}

      <div className="ep-settings-toolbar" style={{ gap: "0.75rem", flexWrap: "wrap" }}>
        <label>
          Cabang{" "}
          <select
            value={branchId}
            onChange={(e) => {
              setBranchId(e.target.value);
              setPage(1);
            }}
          >
            <option value="">Semua cabang akses</option>
            {branches.map((b) => (
              <option key={b.code} value={b.code}>
                {b.code} — {b.name}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="ep-btn" onClick={() => void refresh()}>
          Muat ulang
        </button>
      </div>

      <div
        className="ep-settings-subtabs"
        role="tablist"
        aria-label="Manajemen Cat"
        style={{ marginBottom: "0.75rem" }}
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`ep-settings-subtab${tab === t.id ? " is-active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error ? (
        <p className="ep-error" role="alert">
          {error}
        </p>
      ) : null}
      {flash ? (
        <p className="ep-settings-card-note" role="status">
          {flash}
        </p>
      ) : null}
      {loading ? <p className="muted">Memuat Manajemen Cat…</p> : null}

      {tab === "stock" ? (
        <DataTable
          tableId="paint:stock-v1"
          columns={stockColumns}
          rows={loading ? [] : pageRows}
          filterSourceRows={filteredStock}
          rowKey={(r) => `${r.branch_id}:${r.paint_id}`}
          enableColumnManager
          inlineColumnTools
          emptyMessage={
            loading ? "Memuat stok cat…" : "Belum ada saldo cat untuk filter ini."
          }
          page={page}
          pageSize={pageSize}
          total={loading ? 0 : filteredStock.length}
          onPageChange={setPage}
          onPageSizeChange={(n) => {
            setPageSize(n);
            setPage(1);
          }}
          filterSlot={(tools) => (
            <div className="ep-filterbar is-compact">
              <strong>Stok Cat</strong>
              <div className="ep-search">
                <input
                  type="search"
                  placeholder="Cari merek / jenis / warna"
                  value={q}
                  onChange={(e) => {
                    setQ(e.target.value);
                    setPage(1);
                  }}
                  aria-label="Cari stok cat"
                />
              </div>
              <button
                type="button"
                className="ep-btn sm"
                onClick={() => tools.openColumns()}
              >
                Kolom
              </button>
            </div>
          )}
        />
      ) : null}

      {tab === "inbound" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Cat Masuk</h3>
          {!can("paint.inbound.confirm") ? (
            <p className="muted">Anda tidak berwenang konfirmasi penerimaan.</p>
          ) : (
            <form
              className="ep-settings-form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                if (!branchId) {
                  setError("Pilih cabang terlebih dahulu.");
                  return;
                }
                void runAction("Penerimaan cat dikonfirmasi.", () =>
                  confirmPaintInbound({
                    branch_id: branchId,
                    paint_id: inboundForm.paint_id,
                    qty_g: Number(inboundForm.qty_g),
                    supplier: inboundForm.supplier || undefined,
                    note: inboundForm.note || undefined,
                    physical_confirmed: true,
                    idempotency_key: newPaintIdempotencyKey("in"),
                  }),
                );
              }}
            >
              <label>
                Cat
                <select
                  required
                  value={inboundForm.paint_id}
                  onChange={(e) =>
                    setInboundForm((s) => ({ ...s, paint_id: e.target.value }))
                  }
                >
                  <option value="">Pilih cat</option>
                  {catalog
                    .filter((c) => c.is_active !== false)
                    .map((c) => (
                      <option key={c.paint_id} value={c.paint_id}>
                        {paintLabel(c)}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Berat (gram)
                <input
                  required
                  type="number"
                  min={0.001}
                  step="any"
                  value={inboundForm.qty_g}
                  onChange={(e) =>
                    setInboundForm((s) => ({ ...s, qty_g: e.target.value }))
                  }
                />
              </label>
              <label>
                Pemasok / sumber
                <input
                  value={inboundForm.supplier}
                  onChange={(e) =>
                    setInboundForm((s) => ({ ...s, supplier: e.target.value }))
                  }
                />
              </label>
              <label>
                Catatan
                <input
                  value={inboundForm.note}
                  onChange={(e) =>
                    setInboundForm((s) => ({ ...s, note: e.target.value }))
                  }
                />
              </label>
              <button type="submit" className="ep-btn primary">
                Konfirmasi penerimaan fisik
              </button>
            </form>
          )}
          <DocumentList
            docs={docs.filter((d) => d.doc_type === "inbound_manual")}
            catalogMap={catalogMap}
            canDelete={can("paint.document.soft_delete")}
            onDelete={(id, reason) =>
              void runAction("Dokumen dihapus (soft).", () =>
                softDeletePaintDocument(id, {
                  reason,
                  idempotency_key: newPaintIdempotencyKey("sd"),
                }),
              )
            }
          />
        </section>
      ) : null}

      {tab === "withdraw" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Pengambilan Cat</h3>
          {!can("paint.withdraw.create") ? (
            <p className="muted">Anda tidak berwenang membuat pengambilan.</p>
          ) : (
            <form
              className="ep-settings-form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                if (!branchId) {
                  setError("Pilih cabang terlebih dahulu.");
                  return;
                }
                void runAction("Pengambilan tercatat.", () =>
                  createPaintWithdrawal({
                    branch_id: branchId,
                    used_by_email: withdrawForm.used_by_email,
                    purpose_id: withdrawForm.purpose_id,
                    accurate_po_id: withdrawForm.accurate_po_id || undefined,
                    note: withdrawForm.note || undefined,
                    items: [
                      {
                        paint_id: withdrawForm.paint_id,
                        qty_g: Number(withdrawForm.qty_g),
                      },
                    ],
                    idempotency_key: newPaintIdempotencyKey("wd"),
                  }),
                );
              }}
            >
              <label>
                Pengguna cat (email)
                <input
                  required
                  type="email"
                  value={withdrawForm.used_by_email}
                  onChange={(e) =>
                    setWithdrawForm((s) => ({
                      ...s,
                      used_by_email: e.target.value,
                    }))
                  }
                />
              </label>
              <label>
                Tujuan
                <select
                  required
                  value={withdrawForm.purpose_id}
                  onChange={(e) =>
                    setWithdrawForm((s) => ({
                      ...s,
                      purpose_id: e.target.value,
                    }))
                  }
                >
                  <option value="">Pilih tujuan</option>
                  {purposes
                    .filter((p) => p.is_active !== false)
                    .map((p) => (
                      <option key={p.purpose_id} value={p.purpose_id}>
                        {p.name}
                        {p.requires_po ? " (wajib PO)" : ""}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Referensi PO (opsional)
                <input
                  value={withdrawForm.accurate_po_id}
                  onChange={(e) =>
                    setWithdrawForm((s) => ({
                      ...s,
                      accurate_po_id: e.target.value,
                    }))
                  }
                />
              </label>
              <label>
                Cat
                <select
                  required
                  value={withdrawForm.paint_id}
                  onChange={(e) =>
                    setWithdrawForm((s) => ({ ...s, paint_id: e.target.value }))
                  }
                >
                  <option value="">Pilih cat</option>
                  {catalog
                    .filter((c) => c.is_active !== false)
                    .map((c) => (
                      <option key={c.paint_id} value={c.paint_id}>
                        {paintLabel(c)}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Berat (gram)
                <input
                  required
                  type="number"
                  min={0.001}
                  step="any"
                  value={withdrawForm.qty_g}
                  onChange={(e) =>
                    setWithdrawForm((s) => ({ ...s, qty_g: e.target.value }))
                  }
                />
              </label>
              <label>
                Catatan
                <input
                  value={withdrawForm.note}
                  onChange={(e) =>
                    setWithdrawForm((s) => ({ ...s, note: e.target.value }))
                  }
                />
              </label>
              <button type="submit" className="ep-btn primary">
                Konfirmasi serah fisik
              </button>
            </form>
          )}
          <DocumentList
            docs={withdrawals}
            catalogMap={catalogMap}
            canDelete={can("paint.document.soft_delete")}
            onDelete={(id, reason) =>
              void runAction("Pengambilan dihapus (soft).", () =>
                softDeletePaintDocument(id, {
                  reason,
                  idempotency_key: newPaintIdempotencyKey("sd"),
                }),
              )
            }
          />
        </section>
      ) : null}

      {tab === "return" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Pengembalian warna asal</h3>
          {can("paint.return.confirm") ? (
            <form
              className="ep-settings-form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                void runAction("Pengembalian dikonfirmasi.", () =>
                  confirmPaintReturn({
                    withdrawal_id: returnForm.withdrawal_id,
                    withdrawal_line_id: returnForm.withdrawal_line_id,
                    qty_g: Number(returnForm.qty_g),
                    note: returnForm.note || undefined,
                    condition: "fit",
                    idempotency_key: newPaintIdempotencyKey("rt"),
                  }),
                );
              }}
            >
              <WithdrawalLinePickers
                withdrawals={withdrawals}
                catalogMap={catalogMap}
                withdrawalId={returnForm.withdrawal_id}
                lineId={returnForm.withdrawal_line_id}
                onWithdrawal={(id) =>
                  setReturnForm((s) => ({
                    ...s,
                    withdrawal_id: id,
                    withdrawal_line_id: "",
                  }))
                }
                onLine={(id) =>
                  setReturnForm((s) => ({ ...s, withdrawal_line_id: id }))
                }
              />
              <label>
                Berat kembali (g)
                <input
                  required
                  type="number"
                  min={0.001}
                  step="any"
                  value={returnForm.qty_g}
                  onChange={(e) =>
                    setReturnForm((s) => ({ ...s, qty_g: e.target.value }))
                  }
                />
              </label>
              <button type="submit" className="ep-btn primary">
                Konfirmasi penerimaan return
              </button>
            </form>
          ) : (
            <p className="muted">Tidak berwenang konfirmasi pengembalian.</p>
          )}

          <h3 className="ep-settings-card-title">Konversi → Cat Hitam</h3>
          {can("paint.conversion.confirm") ? (
            <form
              className="ep-settings-form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                if (!branchId) {
                  setError("Pilih cabang terlebih dahulu.");
                  return;
                }
                void runAction("Konversi ke Hitam tercatat.", () =>
                  confirmPaintConversion({
                    branch_id: branchId,
                    destination_paint_id: convForm.destination_paint_id,
                    source_lines: [
                      {
                        withdrawal_id: convForm.withdrawal_id,
                        withdrawal_line_id: convForm.withdrawal_line_id,
                        qty_g: Number(convForm.source_qty_g),
                      },
                    ],
                    verified_return_g: Number(convForm.verified_return_g),
                    waste_g: Number(convForm.waste_g || 0),
                    idempotency_key: newPaintIdempotencyKey("cv"),
                  }),
                );
              }}
            >
              <WithdrawalLinePickers
                withdrawals={withdrawals}
                catalogMap={catalogMap}
                withdrawalId={convForm.withdrawal_id}
                lineId={convForm.withdrawal_line_id}
                onWithdrawal={(id) =>
                  setConvForm((s) => ({
                    ...s,
                    withdrawal_id: id,
                    withdrawal_line_id: "",
                  }))
                }
                onLine={(id) =>
                  setConvForm((s) => ({ ...s, withdrawal_line_id: id }))
                }
              />
              <label>
                Berat sumber (g)
                <input
                  required
                  type="number"
                  min={0.001}
                  step="any"
                  value={convForm.source_qty_g}
                  onChange={(e) =>
                    setConvForm((s) => ({ ...s, source_qty_g: e.target.value }))
                  }
                />
              </label>
              <label>
                Destination Hitam
                <select
                  required
                  value={convForm.destination_paint_id}
                  onChange={(e) =>
                    setConvForm((s) => ({
                      ...s,
                      destination_paint_id: e.target.value,
                    }))
                  }
                >
                  <option value="">Pilih cat Hitam</option>
                  {blackPaints.map((c) => (
                    <option key={c.paint_id} value={c.paint_id}>
                      {paintLabel(c)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Hasil timbangan (g)
                <input
                  required
                  type="number"
                  min={0.001}
                  step="any"
                  value={convForm.verified_return_g}
                  onChange={(e) =>
                    setConvForm((s) => ({
                      ...s,
                      verified_return_g: e.target.value,
                    }))
                  }
                />
              </label>
              <label>
                Waste / selisih (g)
                <input
                  type="number"
                  min={0}
                  step="any"
                  value={convForm.waste_g}
                  onChange={(e) =>
                    setConvForm((s) => ({ ...s, waste_g: e.target.value }))
                  }
                />
              </label>
              <button type="submit" className="ep-btn primary">
                Posting konversi atomik
              </button>
            </form>
          ) : (
            <p className="muted">Tidak berwenang konversi warna.</p>
          )}
        </section>
      ) : null}

      {tab === "waste" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Waste</h3>
          {can("paint.waste.record") ? (
            <form
              className="ep-settings-form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                void runAction("Waste tercatat.", () =>
                  recordPaintWaste({
                    withdrawal_id: wasteForm.withdrawal_id,
                    withdrawal_line_id: wasteForm.withdrawal_line_id,
                    qty_g: Number(wasteForm.qty_g),
                    reason: wasteForm.reason,
                    idempotency_key: newPaintIdempotencyKey("ws"),
                  }),
                );
              }}
            >
              <WithdrawalLinePickers
                withdrawals={withdrawals}
                catalogMap={catalogMap}
                withdrawalId={wasteForm.withdrawal_id}
                lineId={wasteForm.withdrawal_line_id}
                onWithdrawal={(id) =>
                  setWasteForm((s) => ({
                    ...s,
                    withdrawal_id: id,
                    withdrawal_line_id: "",
                  }))
                }
                onLine={(id) =>
                  setWasteForm((s) => ({ ...s, withdrawal_line_id: id }))
                }
              />
              <label>
                Berat waste (g)
                <input
                  required
                  type="number"
                  min={0.001}
                  step="any"
                  value={wasteForm.qty_g}
                  onChange={(e) =>
                    setWasteForm((s) => ({ ...s, qty_g: e.target.value }))
                  }
                />
              </label>
              <label>
                Alasan
                <input
                  required
                  value={wasteForm.reason}
                  onChange={(e) =>
                    setWasteForm((s) => ({ ...s, reason: e.target.value }))
                  }
                />
              </label>
              <button type="submit" className="ep-btn primary">
                Catat waste
              </button>
            </form>
          ) : (
            <p className="muted">Tidak berwenang mencatat waste.</p>
          )}
        </section>
      ) : null}

      {tab === "reports" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Laporan penggunaan</h3>
          {!can("paint.reports.view") ? (
            <p className="muted">Tidak berwenang melihat laporan cat.</p>
          ) : (
            <>
              <div className="ep-settings-toolbar" style={{ gap: "0.5rem" }}>
                {(
                  [
                    ["usage_by_user", "Per pengguna"],
                    ["movements", "Mutasi"],
                    ["stock_balances", "Saldo"],
                  ] as const
                ).map(([kind, label]) => (
                  <button
                    key={kind}
                    type="button"
                    className="ep-btn"
                    onClick={() =>
                      void (async () => {
                        setError(null);
                        try {
                          const rep = await fetchPaintReport(kind, {
                            branch_id: branchId || undefined,
                          });
                          setReportRows(rep.items || []);
                        } catch (err) {
                          setError(
                            err instanceof Error
                              ? err.message
                              : "Gagal memuat laporan.",
                          );
                        }
                      })()
                    }
                  >
                    {label}
                  </button>
                ))}
              </div>
              <pre
                className="ep-settings-card-note"
                style={{ overflow: "auto", maxHeight: 360 }}
              >
                {reportRows.length
                  ? JSON.stringify(reportRows, null, 2)
                  : "Pilih jenis laporan."}
              </pre>
            </>
          )}
        </section>
      ) : null}

      {tab === "stocktake" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Stock Opname</h3>
          {can("paint.stocktake.run") ? (
            <button
              type="button"
              className="ep-btn"
              onClick={() =>
                void runAction("Dry run stocktake dibuat.", async () => {
                  if (!branchId) throw new Error("Pilih cabang terlebih dahulu.");
                  const res = await startPaintStocktake({ branch_id: branchId });
                  const st = res.stocktake as {
                    stocktake_id: string;
                    lines?: Array<{
                      paint_id: string;
                      system_g?: number;
                      system_mg?: number;
                    }>;
                  };
                  setStocktakeId(st.stocktake_id);
                  setStocktakeLines(
                    (st.lines || []).map((ln) => ({
                      paint_id: ln.paint_id,
                      system_g:
                        ln.system_g ??
                        (ln.system_mg != null ? ln.system_mg / 1000 : 0),
                      counted_g: String(
                        ln.system_g ??
                          (ln.system_mg != null ? ln.system_mg / 1000 : 0),
                      ),
                    })),
                  );
                })
              }
            >
              Mulai dry run
            </button>
          ) : (
            <p className="muted">Tidak berwenang menjalankan stocktake.</p>
          )}
          {stocktakeId ? (
            <div style={{ marginTop: "1rem" }}>
              <p className="muted">Stocktake: {stocktakeId} (dry run)</p>
              <table className="ep-settings-data-table is-compact">
                <thead>
                  <tr>
                    <th>Cat</th>
                    <th>Sistem (g)</th>
                    <th>Fisik (g)</th>
                    <th>Selisih</th>
                  </tr>
                </thead>
                <tbody>
                  {stocktakeLines.map((ln) => {
                    const counted = Number(ln.counted_g || 0);
                    const delta = counted - ln.system_g;
                    const c = catalogMap.get(ln.paint_id);
                    return (
                      <tr key={ln.paint_id}>
                        <td>{c ? paintLabel(c) : ln.paint_id}</td>
                        <td>{ln.system_g}</td>
                        <td>
                          <input
                            type="number"
                            step="any"
                            value={ln.counted_g}
                            onChange={(e) =>
                              setStocktakeLines((rows) =>
                                rows.map((r) =>
                                  r.paint_id === ln.paint_id
                                    ? { ...r, counted_g: e.target.value }
                                    : r,
                                ),
                              )
                            }
                          />
                        </td>
                        <td>{delta.toLocaleString("id-ID")}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {can("paint.stocktake.finalize") ? (
                <button
                  type="button"
                  className="ep-btn primary"
                  style={{ marginTop: "0.75rem" }}
                  onClick={() => {
                    if (
                      !window.confirm(
                        "Finalize stocktake? Periode akan dikunci sampai cutoff.",
                      )
                    ) {
                      return;
                    }
                    void runAction("Stocktake finalized.", () =>
                      finalizePaintStocktake(stocktakeId, {
                        idempotency_key: newPaintIdempotencyKey("fin"),
                        counts: stocktakeLines.map((ln) => ({
                          paint_id: ln.paint_id,
                          counted_g: Number(ln.counted_g),
                        })),
                      }),
                    );
                  }}
                >
                  Finalize & kunci periode
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {tab === "master" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Master Cat</h3>
          {can("paint.master.manage") ? (
            <form
              className="ep-settings-form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                void runAction("Master cat disimpan.", () =>
                  upsertPaintCatalog(masterPaint),
                );
              }}
            >
              <label>
                Merek
                <input
                  required
                  value={masterPaint.brand}
                  onChange={(e) =>
                    setMasterPaint((s) => ({ ...s, brand: e.target.value }))
                  }
                />
              </label>
              <label>
                Jenis
                <input
                  required
                  value={masterPaint.kind}
                  onChange={(e) =>
                    setMasterPaint((s) => ({ ...s, kind: e.target.value }))
                  }
                />
              </label>
              <label>
                Warna
                <input
                  required
                  value={masterPaint.color}
                  onChange={(e) =>
                    setMasterPaint((s) => ({ ...s, color: e.target.value }))
                  }
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={masterPaint.is_black}
                  onChange={(e) =>
                    setMasterPaint((s) => ({
                      ...s,
                      is_black: e.target.checked,
                    }))
                  }
                />{" "}
                Cat Hitam (tujuan konversi)
              </label>
              <button type="submit" className="ep-btn primary">
                Simpan master cat
              </button>
            </form>
          ) : (
            <p className="muted">Tidak berwenang mengelola master.</p>
          )}
          <ul>
            {catalog.map((c) => (
              <li key={c.paint_id}>
                {paintLabel(c)}
                {c.is_black ? " · Hitam" : ""}
                {c.is_active === false ? " · nonaktif" : ""}
              </li>
            ))}
          </ul>

          <h3 className="ep-settings-card-title">Master Tujuan</h3>
          {can("paint.master.manage") ? (
            <form
              className="ep-settings-form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                void runAction("Master tujuan disimpan.", () =>
                  upsertPaintPurpose(masterPurpose),
                );
              }}
            >
              <label>
                Nama kegiatan
                <input
                  required
                  value={masterPurpose.name}
                  onChange={(e) =>
                    setMasterPurpose((s) => ({ ...s, name: e.target.value }))
                  }
                />
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={masterPurpose.requires_po}
                  onChange={(e) =>
                    setMasterPurpose((s) => ({
                      ...s,
                      requires_po: e.target.checked,
                    }))
                  }
                />{" "}
                Wajib referensi PO
              </label>
              <button type="submit" className="ep-btn primary">
                Simpan tujuan
              </button>
            </form>
          ) : null}
          <ul>
            {purposes.map((p) => (
              <li key={p.purpose_id}>
                {p.name}
                {p.requires_po ? " · wajib PO" : ""}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function WithdrawalLinePickers(props: {
  withdrawals: PaintDocument[];
  catalogMap: Map<string, PaintCatalogItem>;
  withdrawalId: string;
  lineId: string;
  onWithdrawal: (id: string) => void;
  onLine: (id: string) => void;
}) {
  const selected = props.withdrawals.find(
    (d) => d.document_id === props.withdrawalId,
  );
  return (
    <>
      <label>
        Pengambilan asal
        <select
          required
          value={props.withdrawalId}
          onChange={(e) => props.onWithdrawal(e.target.value)}
        >
          <option value="">Pilih pengambilan</option>
          {props.withdrawals.map((d) => (
            <option key={d.document_id} value={d.document_id}>
              {d.document_id} · {d.used_by || d.recorded_by || "—"}
            </option>
          ))}
        </select>
      </label>
      <label>
        Baris item
        <select
          required
          value={props.lineId}
          onChange={(e) => props.onLine(e.target.value)}
        >
          <option value="">Pilih baris</option>
          {(selected?.lines || []).map((ln) => {
            const c = props.catalogMap.get(ln.paint_id);
            return (
              <option key={ln.line_id} value={ln.line_id}>
                {(c ? paintLabel(c) : ln.paint_id) +
                  ` · ${ln.qty_g ?? grams(Math.abs(ln.qty_mg))} g`}
              </option>
            );
          })}
        </select>
      </label>
    </>
  );
}

function DocumentList(props: {
  docs: PaintDocument[];
  catalogMap: Map<string, PaintCatalogItem>;
  canDelete: boolean;
  onDelete: (id: string, reason: string) => void;
}) {
  if (!props.docs.length) {
    return <p className="muted">Belum ada dokumen.</p>;
  }
  return (
    <table className="ep-settings-data-table is-compact" style={{ marginTop: "1rem" }}>
      <thead>
        <tr>
          <th>ID</th>
          <th>Status</th>
          <th>Detail</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {props.docs.slice(0, 50).map((d) => (
          <tr key={d.document_id}>
            <td>{d.document_id}</td>
            <td>{d.status}</td>
            <td>
              {(d.lines || [])
                .map((ln) => {
                  const c = props.catalogMap.get(ln.paint_id);
                  return `${c ? paintLabel(c) : ln.paint_id}: ${ln.qty_g ?? grams(Math.abs(ln.qty_mg))} g`;
                })
                .join("; ")}
            </td>
            <td>
              {props.canDelete && d.status === "posted" ? (
                <button
                  type="button"
                  className="ep-btn"
                  onClick={() => {
                    const reason = window.prompt("Alasan soft delete?");
                    if (!reason || reason.trim().length < 2) return;
                    props.onDelete(d.document_id, reason.trim());
                  }}
                >
                  Hapus
                </button>
              ) : null}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
