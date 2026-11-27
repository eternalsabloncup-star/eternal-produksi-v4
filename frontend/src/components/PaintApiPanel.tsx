import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  confirmPaintConversion,
  confirmPaintInbound,
  confirmPaintReturn,
  correctPaintDocument,
  createPaintStocktakeSession,
  createPaintWithdrawal,
  fetchPaintAccurateStatus,
  fetchPaintCatalog,
  fetchPaintDocuments,
  fetchPaintPurposes,
  fetchPaintReady,
  fetchPaintReport,
  fetchPaintStock,
  fetchPaintSummary,
  fetchPaintUsers,
  fetchWithdrawalOutstanding,
  finalizePaintStocktake,
  newPaintIdempotencyKey,
  previewPaintStocktake,
  recordPaintWaste,
  softDeletePaintDocument,
  upsertPaintCatalog,
  upsertPaintPurpose,
  type PaintCatalogItem,
  type PaintDocument,
  type PaintKpiSummary,
  type PaintPurpose,
  type PaintStockRow,
  type PaintUser,
} from "../api/paint";
import { fetchBranches, type SettingsBranch } from "../api/settings";
import {
  SettingsConfirmDialog,
  type SettingsConfirmRequest,
} from "../pages/settings/SettingsConfirmDialog";
import { DataTable } from "./DataTable/DataTable";
import { DEFAULT_TABLE_PAGE_SIZE } from "./DataTable/Pagination";
import type { ColumnDef } from "./DataTable/types";
import { KpiSummary, type KpiItem } from "./KpiSummary";

type PrimaryTab = "stock" | "history" | "reports" | "settings";
type ActionModal =
  | null
  | "inbound"
  | "withdraw"
  | "return"
  | "waste"
  | "stocktake"
  | "detail";
type SettingsSub = "catalog" | "purposes" | "accurate";
type ReportKind = "usage_by_user" | "movements" | "stock_balances";
type ReturnMode = "same" | "black";

const PRIMARY_TABS: Array<{ id: PrimaryTab; label: string }> = [
  { id: "stock", label: "Stok Cat" },
  { id: "history", label: "Riwayat Transaksi" },
  { id: "reports", label: "Laporan" },
  { id: "settings", label: "Pengaturan" },
];

const DOC_LABEL: Record<string, string> = {
  inbound_manual: "Cat Masuk",
  withdrawal: "Pengambilan",
  return: "Pengembalian",
  conversion: "Konversi",
  waste: "Waste",
  reversal: "Koreksi",
  stocktake_adjustment: "Stock Opname",
};

function paintLabel(p: PaintCatalogItem | undefined, paintId: string): string {
  if (!p) return paintId;
  return `${p.brand} · ${p.kind} · ${p.color}`;
}

function fmtG(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return "—";
  return value.toLocaleString("id-ID", { maximumFractionDigits: 3 });
}

function gramsFromMg(mg: number | undefined): number {
  return (mg ?? 0) / 1000;
}

export function PaintApiPanel() {
  const [tab, setTab] = useState<PrimaryTab>("stock");
  const [modal, setModal] = useState<ActionModal>(null);
  const [settingsSub, setSettingsSub] = useState<SettingsSub>("catalog");
  const [perms, setPerms] = useState<string[]>([]);
  const [accurateBanner, setAccurateBanner] = useState<string | null>(null);
  const [accurateDetail, setAccurateDetail] = useState("");
  const [branches, setBranches] = useState<SettingsBranch[]>([]);
  const [branchId, setBranchId] = useState("");
  const [catalog, setCatalog] = useState<PaintCatalogItem[]>([]);
  const [purposes, setPurposes] = useState<PaintPurpose[]>([]);
  const [stock, setStock] = useState<PaintStockRow[]>([]);
  const [summary, setSummary] = useState<PaintKpiSummary | null>(null);
  const [docs, setDocs] = useState<PaintDocument[]>([]);
  const [docsTotal, setDocsTotal] = useState(0);
  const [docsPage, setDocsPage] = useState(1);
  const [docsPageSize, setDocsPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [docsQ, setDocsQ] = useState("");
  const [docsType, setDocsType] = useState("");
  const [stockPage, setStockPage] = useState(1);
  const [stockPageSize, setStockPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);
  const [stockQ, setStockQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<PaintDocument | null>(null);
  const [confirmReq, setConfirmReq] = useState<SettingsConfirmRequest | null>(
    null,
  );
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [pendingCorrectId, setPendingCorrectId] = useState<string | null>(null);

  const [reportKind, setReportKind] = useState<ReportKind>("usage_by_user");
  const [reportRows, setReportRows] = useState<Array<Record<string, unknown>>>(
    [],
  );
  const [reportPage, setReportPage] = useState(1);
  const [reportPageSize, setReportPageSize] = useState(DEFAULT_TABLE_PAGE_SIZE);

  const [users, setUsers] = useState<PaintUser[]>([]);
  const [userQ, setUserQ] = useState("");

  // Forms
  const [inboundForm, setInboundForm] = useState({
    paint_id: "",
    qty_g: "",
    supplier: "",
    note: "",
  });
  const [withdrawForm, setWithdrawForm] = useState({
    used_by_email: "",
    purpose_id: "",
    accurate_po_id: "",
    note: "",
  });
  const [withdrawItems, setWithdrawItems] = useState<
    Array<{ paint_id: string; qty_g: string }>
  >([{ paint_id: "", qty_g: "" }]);
  const [returnMode, setReturnMode] = useState<ReturnMode>("same");
  const [returnForm, setReturnForm] = useState({
    withdrawal_id: "",
    withdrawal_line_id: "",
    qty_g: "",
    note: "",
  });
  const [outstandingByWd, setOutstandingByWd] = useState<
    Record<
      string,
      Array<{
        line_id: string;
        paint_id: string;
        withdrawn_g: number;
        outstanding_g: number;
      }>
    >
  >({});
  const [convSources, setConvSources] = useState<
    Array<{ withdrawal_id: string; withdrawal_line_id: string; qty_g: string }>
  >([{ withdrawal_id: "", withdrawal_line_id: "", qty_g: "" }]);
  const [convForm, setConvForm] = useState({
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

  // Stocktake wizard
  const [stStep, setStStep] = useState<1 | 2 | 3 | 4>(1);
  const [stLines, setStLines] = useState<
    Array<{
      paint_id: string;
      system_g: number;
      counted_g: string;
      counted: boolean;
    }>
  >([]);
  const [stPreview, setStPreview] = useState<Record<string, unknown> | null>(
    null,
  );
  const [stocktakeId, setStocktakeId] = useState<string | null>(null);

  const can = useCallback((p: string) => perms.includes(p), [perms]);

  const catalogMap = useMemo(() => {
    const m = new Map<string, PaintCatalogItem>();
    for (const c of catalog) m.set(c.paint_id, c);
    return m;
  }, [catalog]);

  const stockByPaint = useMemo(() => {
    const m = new Map<string, number>();
    for (const s of stock) {
      if (!branchId || s.branch_id === branchId) {
        m.set(s.paint_id, s.qty_g ?? gramsFromMg(s.qty_mg));
      }
    }
    return m;
  }, [stock, branchId]);

  const withdrawals = useMemo(
    () =>
      docs.filter((d) => d.doc_type === "withdrawal" && d.status === "posted"),
    [docs],
  );

  const blackPaints = useMemo(
    () => catalog.filter((c) => c.is_black && c.is_active !== false),
    [catalog],
  );

  const refreshCore = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ready, branchRes, cat, pur, st, sum, acc] = await Promise.all([
        fetchPaintReady(),
        fetchBranches().catch(() => ({ items: [] as SettingsBranch[] })),
        fetchPaintCatalog(false),
        fetchPaintPurposes(false),
        fetchPaintStock(branchId || undefined),
        fetchPaintSummary(branchId || undefined).catch(() => null),
        fetchPaintAccurateStatus().catch(() => null),
      ]);
      setPerms(ready.permissions || []);
      setAccurateBanner(ready.accurate?.ui_banner || acc?.banner || null);
      setAccurateDetail(ready.accurate?.detail || acc?.detail || "");
      setBranches((branchRes.items || []).filter((b) => b.is_active !== false));
      if (!branchId && (branchRes.items || []).length === 1) {
        setBranchId(branchRes.items[0].code);
      }
      setCatalog(cat.items || []);
      setPurposes(pur.items || []);
      setStock(st.items || []);
      setSummary(sum);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Gagal memuat Manajemen Cat.",
      );
    } finally {
      setLoading(false);
    }
  }, [branchId]);

  const refreshDocs = useCallback(async () => {
    try {
      const res = await fetchPaintDocuments({
        branch_id: branchId || undefined,
        doc_type: docsType || undefined,
        limit: docsPageSize,
        offset: (docsPage - 1) * docsPageSize,
        q: docsQ.trim() || undefined,
      });
      setDocs(res.items || []);
      setDocsTotal(res.total || 0);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Gagal memuat riwayat transaksi.",
      );
    }
  }, [branchId, docsType, docsPage, docsPageSize, docsQ]);

  useEffect(() => {
    void refreshCore();
  }, [refreshCore]);

  useEffect(() => {
    if (tab === "history" || modal === "return" || modal === "waste" || modal === "withdraw") {
      void refreshDocs();
    }
  }, [tab, modal, refreshDocs]);

  useEffect(() => {
    setStockPage(1);
    setDocsPage(1);
    setReportPage(1);
    setSelectedDoc(null);
    setOutstandingByWd({});
    setReturnForm({
      withdrawal_id: "",
      withdrawal_line_id: "",
      qty_g: "",
      note: "",
    });
    setStocktakeId(null);
    setStPreview(null);
    setStStep(1);
  }, [branchId]);

  async function runAction(label: string, fn: () => Promise<unknown>) {
    setBusy(true);
    setFlash(null);
    setError(null);
    try {
      await fn();
      setFlash(label);
      await refreshCore();
      await refreshDocs();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Operasi gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function loadUsers(q: string) {
    try {
      const res = await fetchPaintUsers({
        branch_id: branchId || undefined,
        q: q || undefined,
        limit: 40,
      });
      setUsers(res.items || []);
    } catch {
      setUsers([]);
    }
  }

  async function loadOutstanding(withdrawalId: string) {
    if (!withdrawalId) return;
    try {
      const res = await fetchWithdrawalOutstanding(withdrawalId);
      setOutstandingByWd((prev) => ({
        ...prev,
        [withdrawalId]: (res.lines || []).map((ln) => ({
          line_id: ln.line_id,
          paint_id: ln.paint_id,
          withdrawn_g: ln.withdrawn_g,
          outstanding_g: ln.outstanding_g,
        })),
      }));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Gagal memuat sisa outstanding.",
      );
    }
  }

  async function loadReport(kind: ReportKind) {
    setError(null);
    try {
      const rep = await fetchPaintReport(kind, {
        branch_id: branchId || undefined,
      });
      setReportKind(kind);
      setReportRows(rep.items || []);
      setReportPage(1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat laporan.");
    }
  }

  const kpis = useMemo((): KpiItem[] => {
    const period =
      summary?.period?.from || summary?.period?.to
        ? "Periode aktif"
        : "Semua periode terfilter cabang";
    return [
      {
        label: "Stok Tersedia",
        value: `${fmtG(summary?.available_g)} g`,
        foot: "Saldo cabang (backend)",
      },
      {
        label: "Penggunaan Bersih",
        value: `${fmtG(summary?.net_usage_g)} g`,
        foot: period,
      },
      {
        label: "Pengembalian",
        value: `${fmtG(summary?.return_g)} g`,
        foot: period,
      },
      {
        label: "Waste",
        value: `${fmtG(summary?.waste_g)} g`,
        foot: period,
      },
    ];
  }, [summary]);

  const filteredStock = useMemo(() => {
    const needle = stockQ.trim().toLowerCase();
    if (!needle) return stock;
    return stock.filter((row) => {
      const c = catalogMap.get(row.paint_id);
      return [row.paint_id, row.branch_id, c?.brand, c?.kind, c?.color]
        .filter(Boolean)
        .join(" ")
        .toLowerCase()
        .includes(needle);
    });
  }, [stock, stockQ, catalogMap]);

  const stockPageRows = useMemo(() => {
    const start = (stockPage - 1) * stockPageSize;
    return filteredStock.slice(start, start + stockPageSize);
  }, [filteredStock, stockPage, stockPageSize]);

  const stockColumns: ColumnDef<PaintStockRow>[] = useMemo(
    () => [
      {
        id: "branch",
        label: "Cabang",
        hideable: true,
        filterable: true,
        getFilterValue: (r) => r.branch_id,
        render: (r) => r.branch_id,
      },
      {
        id: "paint",
        label: "Cat",
        hideable: true,
        filterable: true,
        getFilterValue: (r) => paintLabel(catalogMap.get(r.paint_id), r.paint_id),
        render: (r) => paintLabel(catalogMap.get(r.paint_id), r.paint_id),
      },
      {
        id: "qty",
        label: "Saldo (g)",
        hideable: true,
        filterType: "number",
        getSortValue: (r) => r.qty_mg ?? 0,
        getFilterValue: (r) => String(r.qty_g ?? gramsFromMg(r.qty_mg)),
        render: (r) => (
          <span className="ep-paint-qty">
            {fmtG(r.qty_g ?? gramsFromMg(r.qty_mg))}
          </span>
        ),
      },
    ],
    [catalogMap],
  );

  const historyColumns: ColumnDef<PaintDocument>[] = useMemo(
    () => [
      {
        id: "created",
        label: "Tanggal",
        hideable: true,
        filterType: "date",
        getFilterValue: (r) => String(r.created_at || ""),
        render: (r) =>
          r.created_at
            ? new Date(r.created_at).toLocaleString("id-ID", {
                timeZone: "Asia/Jakarta",
              })
            : "—",
      },
      {
        id: "type",
        label: "Jenis",
        hideable: true,
        filterable: true,
        getFilterValue: (r) => DOC_LABEL[r.doc_type] || r.doc_type,
        render: (r) => DOC_LABEL[r.doc_type] || r.doc_type,
      },
      {
        id: "id",
        label: "Nomor",
        hideable: true,
        filterable: true,
        getFilterValue: (r) => r.document_id,
        render: (r) => <span className="key">{r.document_id}</span>,
      },
      {
        id: "user",
        label: "Pengguna",
        hideable: true,
        filterable: true,
        getFilterValue: (r) => r.used_by || r.recorded_by || "",
        render: (r) => r.used_by || r.recorded_by || "—",
      },
      {
        id: "branch",
        label: "Cabang",
        hideable: true,
        getFilterValue: (r) => r.branch_id,
        render: (r) => r.branch_id,
      },
      {
        id: "qty",
        label: "Qty (g)",
        hideable: true,
        render: (r) => {
          const total = (r.lines || []).reduce(
            (s, ln) => s + Math.abs(ln.qty_g ?? gramsFromMg(ln.qty_mg)),
            0,
          );
          return <span className="ep-paint-qty">{fmtG(total)}</span>;
        },
      },
      {
        id: "status",
        label: "Status",
        hideable: true,
        filterable: true,
        getFilterValue: (r) => r.status,
        render: (r) => r.status,
      },
    ],
    [],
  );

  const reportPageRows = useMemo(() => {
    const start = (reportPage - 1) * reportPageSize;
    return reportRows.slice(start, start + reportPageSize);
  }, [reportRows, reportPage, reportPageSize]);

  const reportColumns: ColumnDef<Record<string, unknown>>[] = useMemo(() => {
    if (reportKind === "usage_by_user") {
      return [
        {
          id: "user",
          label: "Pengguna",
          getFilterValue: (r) => String(r.used_by || ""),
          render: (r) => String(r.used_by || "—"),
        },
        {
          id: "w",
          label: "Diambil (g)",
          render: (r) => (
            <span className="ep-paint-qty">{fmtG(Number(r.withdraw_g))}</span>
          ),
        },
        {
          id: "r",
          label: "Kembali (g)",
          render: (r) => (
            <span className="ep-paint-qty">{fmtG(Number(r.return_g))}</span>
          ),
        },
        {
          id: "n",
          label: "Bersih (g)",
          render: (r) => (
            <span className="ep-paint-qty">{fmtG(Number(r.net_usage_g))}</span>
          ),
        },
        {
          id: "ws",
          label: "Waste (g)",
          render: (r) => (
            <span className="ep-paint-qty">{fmtG(Number(r.waste_g))}</span>
          ),
        },
      ];
    }
    if (reportKind === "stock_balances") {
      return [
        {
          id: "branch",
          label: "Cabang",
          render: (r) => String(r.branch_id || "—"),
        },
        {
          id: "paint",
          label: "Cat",
          render: (r) =>
            paintLabel(
              catalogMap.get(String(r.paint_id || "")),
              String(r.paint_id || ""),
            ),
        },
        {
          id: "qty",
          label: "Saldo (g)",
          render: (r) => (
            <span className="ep-paint-qty">
              {fmtG(Number(r.qty_g ?? gramsFromMg(Number(r.qty_mg))))}
            </span>
          ),
        },
      ];
    }
    return [
      {
        id: "id",
        label: "Dokumen",
        render: (r) => String(r.document_id || "—"),
      },
      {
        id: "type",
        label: "Jenis",
        render: (r) =>
          DOC_LABEL[String(r.doc_type || "")] || String(r.doc_type || "—"),
      },
      {
        id: "user",
        label: "Pengguna",
        render: (r) => String(r.used_by || r.recorded_by || "—"),
      },
      {
        id: "when",
        label: "Waktu",
        render: (r) =>
          r.created_at
            ? new Date(String(r.created_at)).toLocaleString("id-ID", {
                timeZone: "Asia/Jakarta",
              })
            : "—",
      },
    ];
  }, [reportKind, catalogMap]);

  const withdrawTotal = withdrawItems.reduce(
    (s, it) => s + (Number(it.qty_g) || 0),
    0,
  );

  const convSourceTotal = convSources.reduce(
    (s, it) => s + (Number(it.qty_g) || 0),
    0,
  );
  const convDest = Number(convForm.verified_return_g) || 0;
  const convWaste = Number(convForm.waste_g) || 0;
  const convBalanced = convDest + convWaste <= convSourceTotal + 1e-9;

  function openSoftDelete(doc: PaintDocument) {
    setPendingDeleteId(doc.document_id);
    setPendingCorrectId(null);
    setConfirmReq({
      title: "Soft Delete transaksi cat",
      message: `${DOC_LABEL[doc.doc_type] || doc.doc_type} · ${doc.document_id}`,
      details: [
        `Status: ${doc.status}`,
        `Cabang: ${doc.branch_id}`,
        `Pengguna: ${doc.used_by || doc.recorded_by || "—"}`,
        "Dampak stok akan dibalikkan melalui ledger compensating jika aman.",
      ],
      tone: "danger",
      requireReason: true,
      reasonLabel: "Alasan hapus",
      confirmLabel: "Konfirmasi Hapus",
      cancelLabel: "Batal",
    });
    setConfirmOpen(true);
  }

  function openCorrect(doc: PaintDocument) {
    setPendingCorrectId(doc.document_id);
    setPendingDeleteId(null);
    setConfirmReq({
      title: "Koreksi transaksi posted",
      message: `${DOC_LABEL[doc.doc_type] || doc.doc_type} · ${doc.document_id}`,
      details: [
        "Koreksi memakai compensating ledger (bukan overwrite diam-diam).",
        "Transaksi dengan dependent return/konversi/waste dapat ditolak backend.",
      ],
      tone: "danger",
      requireReason: true,
      reasonLabel: "Alasan koreksi",
      confirmLabel: "Konfirmasi Koreksi",
      cancelLabel: "Batal",
    });
    setConfirmOpen(true);
  }

  const purposeNeedsPo = purposes.find(
    (p) => p.purpose_id === withdrawForm.purpose_id,
  )?.requires_po;

  return (
    <div data-testid="paint-api-panel" className="ep-paint-workspace">
      <header className="ep-paint-header">
        <div>
          <h1>Manajemen Cat</h1>
          <p className="ep-sub">Stok & Penggunaan Cat</p>
        </div>
        <div className="ep-paint-header-actions">
          <label>
            Cabang{" "}
            <select
              value={branchId}
              aria-label="Pilih cabang"
              onChange={(e) => setBranchId(e.target.value)}
            >
              <option value="">Semua cabang akses</option>
              {branches.map((b) => (
                <option key={b.code} value={b.code}>
                  {b.code} — {b.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="ep-btn"
            disabled={busy || loading}
            onClick={() => void refreshCore()}
          >
            Refresh
          </button>
        </div>
      </header>

      {accurateBanner ? (
        <p className="ep-settings-card-note" role="status">
          {accurateBanner}
        </p>
      ) : null}

      <KpiSummary items={kpis} />

      <div className="ep-paint-primary-actions" aria-label="Aksi utama">
        {can("paint.withdraw.create") ? (
          <button
            type="button"
            className="ep-btn is-primary-action"
            onClick={() => {
              setModal("withdraw");
              void loadUsers("");
              void refreshDocs();
            }}
          >
            Ambil Cat
          </button>
        ) : null}
        {can("paint.return.confirm") || can("paint.conversion.confirm") ? (
          <button
            type="button"
            className="ep-btn is-primary-action"
            onClick={() => {
              setModal("return");
              void refreshDocs();
            }}
          >
            Pengembalian / Konversi
          </button>
        ) : null}
        {can("paint.inbound.confirm") ? (
          <button
            type="button"
            className="ep-btn"
            onClick={() => setModal("inbound")}
          >
            Cat Masuk
          </button>
        ) : null}
        {can("paint.waste.record") ? (
          <button
            type="button"
            className="ep-btn"
            onClick={() => {
              setModal("waste");
              void refreshDocs();
            }}
          >
            Waste
          </button>
        ) : null}
        {can("paint.stocktake.run") ? (
          <button
            type="button"
            className="ep-btn"
            onClick={() => {
              setModal("stocktake");
              setStStep(1);
              setStLines(
                stock
                  .filter((s) => !branchId || s.branch_id === branchId)
                  .map((s) => ({
                    paint_id: s.paint_id,
                    system_g: s.qty_g ?? gramsFromMg(s.qty_mg),
                    counted_g: "",
                    counted: false,
                  })),
              );
              setStocktakeId(null);
              setStPreview(null);
            }}
          >
            Stock Opname
          </button>
        ) : null}
      </div>

      <div
        className="ep-settings-subtabs"
        role="tablist"
        aria-label="Area Manajemen Cat"
      >
        {PRIMARY_TABS.map((t) => (
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
          tableId="paint:stock-ux-v1"
          columns={stockColumns}
          rows={loading ? [] : stockPageRows}
          filterSourceRows={filteredStock}
          rowKey={(r) => `${r.branch_id}:${r.paint_id}`}
          enableColumnManager
          inlineColumnTools
          page={stockPage}
          pageSize={stockPageSize}
          total={loading ? 0 : filteredStock.length}
          onPageChange={setStockPage}
          onPageSizeChange={(n) => {
            setStockPageSize(n);
            setStockPage(1);
          }}
          emptyMessage={
            loading ? "Memuat stok…" : "Belum ada saldo cat untuk filter ini."
          }
          filterSlot={() => (
            <div className="ep-filterbar is-compact">
              <strong>Stok Cat</strong>
              <div className="ep-search">
                <input
                  type="search"
                  value={stockQ}
                  onChange={(e) => {
                    setStockQ(e.target.value);
                    setStockPage(1);
                  }}
                  placeholder="Cari merek / jenis / warna"
                  aria-label="Cari stok cat"
                />
              </div>
            </div>
          )}
        />
      ) : null}

      {tab === "history" ? (
        <>
          <div className="ep-filterbar is-compact">
            <strong>Riwayat</strong>
            <select
              aria-label="Filter jenis transaksi"
              value={docsType}
              onChange={(e) => {
                setDocsType(e.target.value);
                setDocsPage(1);
              }}
            >
              <option value="">Semua jenis</option>
              {Object.entries(DOC_LABEL).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            <div className="ep-search">
              <input
                type="search"
                value={docsQ}
                onChange={(e) => {
                  setDocsQ(e.target.value);
                  setDocsPage(1);
                }}
                placeholder="Cari nomor / pengguna"
                aria-label="Cari transaksi cat"
              />
            </div>
          </div>
          <DataTable
            tableId="paint:history-ux-v1"
            columns={historyColumns}
            rows={docs}
            rowKey={(r) => r.document_id}
            enableColumnManager
            page={docsPage}
            pageSize={docsPageSize}
            total={docsTotal}
            onPageChange={setDocsPage}
            onPageSizeChange={(n) => {
              setDocsPageSize(n);
              setDocsPage(1);
            }}
            onRowActivate={(r) => {
              setSelectedDoc(r);
              setModal("detail");
            }}
            emptyMessage="Belum ada transaksi untuk filter ini."
          />
        </>
      ) : null}

      {tab === "reports" ? (
        <section className="ep-settings-card">
          <h3 className="ep-settings-card-title">Laporan penggunaan</h3>
          {!can("paint.reports.view") ? (
            <p className="muted">Tidak berwenang melihat laporan.</p>
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
                    onClick={() => void loadReport(kind)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <p className="muted">
                Cabang: {branchId || "semua akses"} · Sumber: backend
              </p>
              <DataTable
                tableId={`paint:report-${reportKind}-v1`}
                columns={reportColumns}
                rows={reportPageRows}
                rowKey={(r) =>
                  String(
                    r.document_id ||
                      `${r.used_by || ""}:${r.paint_id || ""}:${r.branch_id || ""}:${r.created_at || ""}`,
                  )
                }
                page={reportPage}
                pageSize={reportPageSize}
                total={reportRows.length}
                onPageChange={setReportPage}
                onPageSizeChange={(n) => {
                  setReportPageSize(n);
                  setReportPage(1);
                }}
                emptyMessage="Pilih jenis laporan untuk memuat data."
              />
            </>
          )}
        </section>
      ) : null}

      {tab === "settings" ? (
        <section className="ep-settings-card">
          <div
            className="ep-settings-subtabs"
            role="tablist"
            aria-label="Pengaturan cat"
          >
            {(
              [
                ["catalog", "Master Cat"],
                ["purposes", "Master Tujuan"],
                ["accurate", "Accurate"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={settingsSub === id}
                className={`ep-settings-subtab${settingsSub === id ? " is-active" : ""}`}
                onClick={() => setSettingsSub(id)}
              >
                {label}
              </button>
            ))}
          </div>

          {settingsSub === "catalog" ? (
            <>
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
                    Cat Hitam
                  </label>
                  <button type="submit" className="ep-btn primary" disabled={busy}>
                    Simpan
                  </button>
                </form>
              ) : (
                <p className="muted">Tidak berwenang mengelola master.</p>
              )}
              <ul>
                {catalog.map((c) => (
                  <li key={c.paint_id}>
                    {paintLabel(c, c.paint_id)}
                    {c.is_black ? " · Hitam" : ""}
                    {c.is_active === false ? " · nonaktif" : ""}
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          {settingsSub === "purposes" ? (
            <>
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
                        setMasterPurpose((s) => ({
                          ...s,
                          name: e.target.value,
                        }))
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
                  <button type="submit" className="ep-btn primary" disabled={busy}>
                    Simpan
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
            </>
          ) : null}

          {settingsSub === "accurate" ? (
            <>
              <h3 className="ep-settings-card-title">Integrasi Accurate</h3>
              <p className="ep-settings-card-note" role="status">
                Integrasi Accurate Belum Aktif
              </p>
              <p className="muted">{accurateDetail || "Adapter dark — stok otomatis ditolak."}</p>
            </>
          ) : null}
        </section>
      ) : null}

      {/* ——— Modals ——— */}
      {modal === "inbound" ? (
        <PaintModal
          title="Cat Masuk"
          kicker="Penerimaan fisik"
          onClose={() => setModal(null)}
          footer={
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={() => setModal(null)}>
                Batal
              </button>
              <button
                type="submit"
                form="paint-inbound-form"
                className="ep-btn primary"
                disabled={busy}
              >
                Konfirmasi penerimaan fisik
              </button>
            </div>
          }
        >
          <form
            id="paint-inbound-form"
            className="ep-settings-form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              if (!branchId) {
                setError("Pilih cabang terlebih dahulu.");
                return;
              }
              void runAction("Penerimaan cat dikonfirmasi.", async () => {
                await confirmPaintInbound({
                  branch_id: branchId,
                  paint_id: inboundForm.paint_id,
                  qty_g: Number(inboundForm.qty_g),
                  supplier: inboundForm.supplier || undefined,
                  note: inboundForm.note || undefined,
                  physical_confirmed: true,
                  idempotency_key: newPaintIdempotencyKey("in"),
                });
                setModal(null);
              });
            }}
          >
            <p className="muted">
              Manual terkontrol. Integrasi Accurate purchasing tetap nonaktif.
            </p>
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
                      {paintLabel(c, c.paint_id)}
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
          </form>
        </PaintModal>
      ) : null}

      {modal === "withdraw" ? (
        <PaintModal
          title="Pengambilan Cat"
          kicker="Serah fisik"
          onClose={() => setModal(null)}
          footer={
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={() => setModal(null)}>
                Batal
              </button>
              <button
                type="submit"
                form="paint-withdraw-form"
                className="ep-btn primary"
                disabled={busy}
              >
                Konfirmasi serah fisik
              </button>
            </div>
          }
        >
          <form
            id="paint-withdraw-form"
            className="ep-settings-form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              if (!branchId) {
                setError("Pilih cabang terlebih dahulu.");
                return;
              }
              const merged = new Map<string, number>();
              for (const it of withdrawItems) {
                if (!it.paint_id || !(Number(it.qty_g) > 0)) continue;
                merged.set(
                  it.paint_id,
                  (merged.get(it.paint_id) || 0) + Number(it.qty_g),
                );
              }
              const items = [...merged.entries()].map(([paint_id, qty_g]) => ({
                paint_id,
                qty_g,
              }));
              if (!items.length) {
                setError("Minimal satu item cat dengan Qty positif.");
                return;
              }
              void runAction("Pengambilan tercatat.", async () => {
                await createPaintWithdrawal({
                  branch_id: branchId,
                  used_by_email: withdrawForm.used_by_email,
                  purpose_id: withdrawForm.purpose_id,
                  accurate_po_id: withdrawForm.accurate_po_id || undefined,
                  note: withdrawForm.note || undefined,
                  items,
                  idempotency_key: newPaintIdempotencyKey("wd"),
                });
                setModal(null);
                setWithdrawItems([{ paint_id: "", qty_g: "" }]);
              });
            }}
          >
            <label>
              Pengguna cat
              <input
                list="paint-user-list"
                required
                type="email"
                value={withdrawForm.used_by_email}
                onChange={(e) => {
                  setWithdrawForm((s) => ({
                    ...s,
                    used_by_email: e.target.value,
                  }));
                  setUserQ(e.target.value);
                  void loadUsers(e.target.value);
                }}
                onFocus={() => void loadUsers(userQ)}
                placeholder="Cari email pengguna"
              />
              <datalist id="paint-user-list">
                {users.map((u) => (
                  <option key={u.email} value={u.email}>
                    {u.display_name}
                  </option>
                ))}
              </datalist>
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
            {purposeNeedsPo ? (
              <label>
                Referensi PO
                <input
                  required
                  value={withdrawForm.accurate_po_id}
                  onChange={(e) =>
                    setWithdrawForm((s) => ({
                      ...s,
                      accurate_po_id: e.target.value,
                    }))
                  }
                />
              </label>
            ) : (
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
            )}
            <div className="ep-paint-item-rows">
              <strong>Item cat</strong>
              {withdrawItems.map((it, idx) => (
                <div className="ep-paint-item-row" key={idx}>
                  <label>
                    Cat
                    <select
                      required
                      value={it.paint_id}
                      onChange={(e) =>
                        setWithdrawItems((rows) =>
                          rows.map((r, i) =>
                            i === idx ? { ...r, paint_id: e.target.value } : r,
                          ),
                        )
                      }
                    >
                      <option value="">Pilih</option>
                      {catalog
                        .filter((c) => c.is_active !== false)
                        .map((c) => (
                          <option key={c.paint_id} value={c.paint_id}>
                            {paintLabel(c, c.paint_id)} · stok{" "}
                            {fmtG(stockByPaint.get(c.paint_id) ?? 0)} g
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    Qty (g)
                    <input
                      required
                      type="number"
                      min={0.001}
                      step="any"
                      value={it.qty_g}
                      onChange={(e) =>
                        setWithdrawItems((rows) =>
                          rows.map((r, i) =>
                            i === idx ? { ...r, qty_g: e.target.value } : r,
                          ),
                        )
                      }
                    />
                  </label>
                  <button
                    type="button"
                    className="ep-btn"
                    disabled={withdrawItems.length <= 1}
                    onClick={() =>
                      setWithdrawItems((rows) => rows.filter((_, i) => i !== idx))
                    }
                  >
                    Hapus
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="ep-btn"
                onClick={() =>
                  setWithdrawItems((rows) => [
                    ...rows,
                    { paint_id: "", qty_g: "" },
                  ])
                }
              >
                Tambah Item
              </button>
            </div>
            <div className="ep-paint-mass-box">
              Total gram: <strong className="ep-paint-qty">{fmtG(withdrawTotal)}</strong>
            </div>
            <label>
              Catatan
              <input
                value={withdrawForm.note}
                onChange={(e) =>
                  setWithdrawForm((s) => ({ ...s, note: e.target.value }))
                }
              />
            </label>
          </form>
        </PaintModal>
      ) : null}

      {modal === "return" ? (
        <PaintModal
          title="Pengembalian / Konversi"
          kicker="Penerimaan return"
          onClose={() => setModal(null)}
          footer={
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={() => setModal(null)}>
                Batal
              </button>
              <button
                type="submit"
                form="paint-return-form"
                className="ep-btn primary"
                disabled={busy}
              >
                Konfirmasi
              </button>
            </div>
          }
        >
          <div className="ep-settings-subtabs" role="tablist" aria-label="Mode return">
            <button
              type="button"
              role="tab"
              aria-selected={returnMode === "same"}
              className={`ep-settings-subtab${returnMode === "same" ? " is-active" : ""}`}
              onClick={() => setReturnMode("same")}
            >
              Warna asal
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={returnMode === "black"}
              className={`ep-settings-subtab${returnMode === "black" ? " is-active" : ""}`}
              onClick={() => setReturnMode("black")}
            >
              Campuran → Hitam
            </button>
          </div>

          {returnMode === "same" ? (
            <form
              id="paint-return-form"
              className="ep-settings-form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                void runAction("Pengembalian dikonfirmasi.", async () => {
                  await confirmPaintReturn({
                    withdrawal_id: returnForm.withdrawal_id,
                    withdrawal_line_id: returnForm.withdrawal_line_id,
                    qty_g: Number(returnForm.qty_g),
                    note: returnForm.note || undefined,
                    condition: "fit",
                    idempotency_key: newPaintIdempotencyKey("rt"),
                  });
                  setModal(null);
                });
              }}
            >
              {!can("paint.return.confirm") ? (
                <p className="muted">Tidak berwenang konfirmasi pengembalian.</p>
              ) : (
                <>
                  <label>
                    Pengambilan asal
                    <select
                      required
                      value={returnForm.withdrawal_id}
                      onChange={(e) => {
                        const id = e.target.value;
                        setReturnForm((s) => ({
                          ...s,
                          withdrawal_id: id,
                          withdrawal_line_id: "",
                        }));
                        void loadOutstanding(id);
                      }}
                    >
                      <option value="">Pilih</option>
                      {withdrawals.map((d) => (
                        <option key={d.document_id} value={d.document_id}>
                          {d.document_id} · {d.used_by || "—"}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Item
                    <select
                      required
                      value={returnForm.withdrawal_line_id}
                      onChange={(e) =>
                        setReturnForm((s) => ({
                          ...s,
                          withdrawal_line_id: e.target.value,
                        }))
                      }
                    >
                      <option value="">Pilih baris</option>
                      {(outstandingByWd[returnForm.withdrawal_id] || []).map(
                        (ln) => (
                        <option key={ln.line_id} value={ln.line_id}>
                          {paintLabel(catalogMap.get(ln.paint_id), ln.paint_id)} ·
                          awal {fmtG(ln.withdrawn_g)} · sisa{" "}
                          {fmtG(ln.outstanding_g)} g
                        </option>
                      ),
                      )}
                    </select>
                  </label>
                  {returnForm.withdrawal_line_id ? (
                    <p className="muted">
                      Maks return:{" "}
                      {fmtG(
                        (outstandingByWd[returnForm.withdrawal_id] || []).find(
                          (o) => o.line_id === returnForm.withdrawal_line_id,
                        )?.outstanding_g,
                      )}{" "}
                      g
                    </p>
                  ) : null}
                  <label>
                    Qty kembali (g)
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
                </>
              )}
            </form>
          ) : (
            <form
              id="paint-return-form"
              className="ep-settings-form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                if (!branchId) {
                  setError("Pilih cabang terlebih dahulu.");
                  return;
                }
                if (!convBalanced) {
                  setError(
                    "Hasil + waste tidak boleh melebihi total gram sumber.",
                  );
                  return;
                }
                void runAction("Konversi ke Hitam tercatat.", async () => {
                  await confirmPaintConversion({
                    branch_id: branchId,
                    destination_paint_id: convForm.destination_paint_id,
                    source_lines: convSources
                      .filter(
                        (s) =>
                          s.withdrawal_id &&
                          s.withdrawal_line_id &&
                          Number(s.qty_g) > 0,
                      )
                      .map((s) => ({
                        withdrawal_id: s.withdrawal_id,
                        withdrawal_line_id: s.withdrawal_line_id,
                        qty_g: Number(s.qty_g),
                      })),
                    verified_return_g: convDest,
                    waste_g: convWaste,
                    idempotency_key: newPaintIdempotencyKey("cv"),
                  });
                  setModal(null);
                });
              }}
            >
              {!can("paint.conversion.confirm") ? (
                <p className="muted">Tidak berwenang konversi warna.</p>
              ) : (
                <>
                  {convSources.map((src, idx) => (
                    <div className="ep-paint-item-rows" key={idx}>
                      <label>
                        Sumber pengambilan #{idx + 1}
                        <select
                          required
                          value={src.withdrawal_id}
                          onChange={(e) => {
                            const id = e.target.value;
                            setConvSources((rows) =>
                              rows.map((r, i) =>
                                i === idx
                                  ? {
                                      ...r,
                                      withdrawal_id: id,
                                      withdrawal_line_id: "",
                                    }
                                  : r,
                              ),
                            );
                            void loadOutstanding(id);
                          }}
                        >
                          <option value="">Pilih</option>
                          {withdrawals.map((d) => (
                            <option key={d.document_id} value={d.document_id}>
                              {d.document_id} · {d.used_by || "—"}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        Baris
                        <select
                          required
                          value={src.withdrawal_line_id}
                          onChange={(e) =>
                            setConvSources((rows) =>
                              rows.map((r, i) =>
                                i === idx
                                  ? {
                                      ...r,
                                      withdrawal_line_id: e.target.value,
                                    }
                                  : r,
                              ),
                            )
                          }
                        >
                          <option value="">Pilih</option>
                          {(outstandingByWd[src.withdrawal_id] || []).map(
                            (ln) => (
                            <option key={ln.line_id} value={ln.line_id}>
                              {paintLabel(
                                catalogMap.get(ln.paint_id),
                                ln.paint_id,
                              )}{" "}
                              · sisa {fmtG(ln.outstanding_g)} g
                            </option>
                          ),
                          )}
                        </select>
                      </label>
                      <label>
                        Gram sumber
                        <input
                          required
                          type="number"
                          min={0.001}
                          step="any"
                          value={src.qty_g}
                          onChange={(e) =>
                            setConvSources((rows) =>
                              rows.map((r, i) =>
                                i === idx
                                  ? { ...r, qty_g: e.target.value }
                                  : r,
                              ),
                            )
                          }
                        />
                      </label>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="ep-btn"
                    onClick={() =>
                      setConvSources((rows) => [
                        ...rows,
                        {
                          withdrawal_id: "",
                          withdrawal_line_id: "",
                          qty_g: "",
                        },
                      ])
                    }
                  >
                    Tambah sumber
                  </button>
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
                      <option value="">Pilih</option>
                      {blackPaints.map((c) => (
                        <option key={c.paint_id} value={c.paint_id}>
                          {paintLabel(c, c.paint_id)}
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
                  <div
                    className={`ep-paint-mass-box ${convBalanced ? "is-ok" : "is-warn"}`}
                  >
                    Sumber {fmtG(convSourceTotal)} g · Hasil {fmtG(convDest)} g ·
                    Waste {fmtG(convWaste)} g
                    {!convBalanced
                      ? " — tidak seimbang (hasil+waste > sumber)"
                      : " — seimbang"}
                  </div>
                </>
              )}
            </form>
          )}
        </PaintModal>
      ) : null}

      {modal === "waste" ? (
        <PaintModal
          title="Catat Waste"
          kicker="Tidak mengurangi gudang dua kali"
          onClose={() => setModal(null)}
          footer={
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={() => setModal(null)}>
                Batal
              </button>
              <button
                type="submit"
                form="paint-waste-form"
                className="ep-btn primary"
                disabled={busy}
              >
                Catat waste
              </button>
            </div>
          }
        >
          <form
            id="paint-waste-form"
            className="ep-settings-form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              void runAction("Waste tercatat.", async () => {
                await recordPaintWaste({
                  withdrawal_id: wasteForm.withdrawal_id,
                  withdrawal_line_id: wasteForm.withdrawal_line_id,
                  qty_g: Number(wasteForm.qty_g),
                  reason: wasteForm.reason,
                  idempotency_key: newPaintIdempotencyKey("ws"),
                });
                setModal(null);
              });
            }}
          >
            <label>
              Pengambilan
              <select
                required
                value={wasteForm.withdrawal_id}
                onChange={(e) => {
                  const id = e.target.value;
                  setWasteForm((s) => ({
                    ...s,
                    withdrawal_id: id,
                    withdrawal_line_id: "",
                  }));
                  void loadOutstanding(id);
                }}
              >
                <option value="">Pilih</option>
                {withdrawals.map((d) => (
                  <option key={d.document_id} value={d.document_id}>
                    {d.document_id} · {d.used_by || "—"}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Item
              <select
                required
                value={wasteForm.withdrawal_line_id}
                onChange={(e) =>
                  setWasteForm((s) => ({
                    ...s,
                    withdrawal_line_id: e.target.value,
                  }))
                }
              >
                <option value="">Pilih</option>
                {(outstandingByWd[wasteForm.withdrawal_id] || []).map((ln) => (
                  <option key={ln.line_id} value={ln.line_id}>
                    {paintLabel(catalogMap.get(ln.paint_id), ln.paint_id)} · sisa{" "}
                    {fmtG(ln.outstanding_g)} g
                  </option>
                ))}
              </select>
            </label>
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
          </form>
        </PaintModal>
      ) : null}

      {modal === "stocktake" ? (
        <PaintModal
          title="Stock Opname"
          kicker={`Langkah ${stStep} dari 4`}
          onClose={() => setModal(null)}
          footer={
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={() => setModal(null)}>
                Tutup
              </button>
            </div>
          }
        >
          <div className="ep-paint-wizard-steps" aria-label="Progress stocktake">
            {[
              [1, "Hitung fisik"],
              [2, "Preview"],
              [3, "Sesi"],
              [4, "Finalisasi"],
            ].map(([n, label]) => (
              <span
                key={n}
                className={`ep-settings-subtab${stStep === n ? " is-active" : ""}`}
              >
                {n}. {label}
              </span>
            ))}
          </div>

          {stStep === 1 ? (
            <>
              {!branchId ? (
                <p className="ep-error">Pilih cabang di header sebelum stocktake.</p>
              ) : null}
              <table className="ep-settings-data-table is-compact">
                <thead>
                  <tr>
                    <th>Cat</th>
                    <th>Sistem (g)</th>
                    <th>Fisik (g)</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {stLines.map((ln) => (
                    <tr key={ln.paint_id}>
                      <td>
                        {paintLabel(catalogMap.get(ln.paint_id), ln.paint_id)}
                      </td>
                      <td className="ep-paint-qty">{fmtG(ln.system_g)}</td>
                      <td>
                        <input
                          type="number"
                          min={0}
                          step="any"
                          value={ln.counted_g}
                          aria-label={`Hitung fisik ${ln.paint_id}`}
                          onChange={(e) =>
                            setStLines((rows) =>
                              rows.map((r) =>
                                r.paint_id === ln.paint_id
                                  ? {
                                      ...r,
                                      counted_g: e.target.value,
                                      counted: e.target.value !== "",
                                    }
                                  : r,
                              ),
                            )
                          }
                        />
                      </td>
                      <td>{ln.counted ? "Dihitung" : "Belum"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button
                type="button"
                className="ep-btn primary"
                disabled={!branchId}
                onClick={() => setStStep(2)}
              >
                Lanjut preview
              </button>
            </>
          ) : null}

          {stStep === 2 ? (
            <>
              <button
                type="button"
                className="ep-btn"
                disabled={busy || !branchId}
                onClick={() =>
                  void runAction("Preview dry-run (tanpa tulis DB).", async () => {
                    const res = await previewPaintStocktake({
                      branch_id: branchId,
                      counts: stLines
                        .filter((l) => l.counted)
                        .map((l) => ({
                          paint_id: l.paint_id,
                          counted_g: Number(l.counted_g),
                        })),
                    });
                    setStPreview(res.stocktake);
                  })
                }
              >
                Jalankan preview
              </button>
              {stPreview ? (
                <div className="ep-paint-mass-box is-ok">
                  Status: {String(stPreview.status)} · persisted={" "}
                  {String(stPreview.persisted)} · baris{" "}
                  {String(stPreview.line_count ?? "")}
                </div>
              ) : null}
              <p className="muted">
                Belum dihitung: {stLines.filter((l) => !l.counted).length} item
              </p>
              <div className="ep-modal-foot-actions">
                <button type="button" className="ep-btn" onClick={() => setStStep(1)}>
                  Kembali
                </button>
                <button
                  type="button"
                  className="ep-btn primary"
                  onClick={() => setStStep(3)}
                >
                  Lanjut buat sesi
                </button>
              </div>
            </>
          ) : null}

          {stStep === 3 ? (
            <>
              <button
                type="button"
                className="ep-btn primary"
                disabled={busy || !branchId}
                onClick={() =>
                  void runAction("Sesi stocktake disimpan.", async () => {
                    const res = await createPaintStocktakeSession({
                      branch_id: branchId,
                      counts: stLines
                        .filter((l) => l.counted)
                        .map((l) => ({
                          paint_id: l.paint_id,
                          counted_g: Number(l.counted_g),
                        })),
                    });
                    const st = res.stocktake as {
                      stocktake_id: string;
                      cutoff_at?: string;
                    };
                    setStocktakeId(st.stocktake_id);
                    setStStep(4);
                  })
                }
              >
                Buat sesi stocktake
              </button>
              {stocktakeId ? (
                <p className="muted">Session: {stocktakeId}</p>
              ) : null}
              <button type="button" className="ep-btn" onClick={() => setStStep(2)}>
                Kembali
              </button>
            </>
          ) : null}

          {stStep === 4 ? (
            <>
              <div className="ep-paint-mass-box">
                Cabang {branchId} · Dihitung{" "}
                {stLines.filter((l) => l.counted).length}/{stLines.length} ·
                Session {stocktakeId || "—"}
              </div>
              <button
                type="button"
                className="ep-btn primary"
                disabled={busy || !stocktakeId || !can("paint.stocktake.finalize")}
                onClick={() => {
                  if (!stocktakeId) return;
                  setConfirmReq({
                    title: "Finalisasi Stock Opname",
                    message: "Periode akan dikunci sampai cutoff snapshot.",
                    details: [
                      `Cabang: ${branchId}`,
                      `Session: ${stocktakeId}`,
                      `Item dihitung: ${stLines.filter((l) => l.counted).length}`,
                    ],
                    tone: "danger",
                    confirmLabel: "Finalize & kunci",
                    cancelLabel: "Batal",
                  });
                  setConfirmOpen(true);
                  setPendingDeleteId(null);
                  setPendingCorrectId(`finalize:${stocktakeId}`);
                }}
              >
                Finalisasi & kunci periode
              </button>
              <button type="button" className="ep-btn" onClick={() => setStStep(3)}>
                Kembali
              </button>
            </>
          ) : null}
        </PaintModal>
      ) : null}

      {modal === "detail" && selectedDoc ? (
        <PaintModal
          title={DOC_LABEL[selectedDoc.doc_type] || selectedDoc.doc_type}
          kicker={selectedDoc.document_id}
          onClose={() => {
            setModal(null);
            setSelectedDoc(null);
          }}
          footer={
            <div className="ep-modal-foot-actions">
              {can("paint.document.edit") &&
              selectedDoc.status === "posted" ? (
                <button
                  type="button"
                  className="ep-btn"
                  onClick={() => openCorrect(selectedDoc)}
                >
                  Koreksi
                </button>
              ) : null}
              {can("paint.document.soft_delete") &&
              selectedDoc.status === "posted" ? (
                <button
                  type="button"
                  className="ep-btn"
                  onClick={() => openSoftDelete(selectedDoc)}
                >
                  Soft Delete
                </button>
              ) : null}
              <button
                type="button"
                className="ep-btn"
                onClick={() => {
                  setModal(null);
                  setSelectedDoc(null);
                }}
              >
                Tutup
              </button>
            </div>
          }
        >
          <p className="muted">
            {selectedDoc.status} · {selectedDoc.branch_id} ·{" "}
            {selectedDoc.used_by || selectedDoc.recorded_by || "—"}
          </p>
          <ul>
            {(selectedDoc.lines || []).map((ln) => (
              <li key={ln.line_id}>
                {paintLabel(catalogMap.get(ln.paint_id), ln.paint_id)}:{" "}
                <span className="ep-paint-qty">
                  {fmtG(ln.qty_g ?? gramsFromMg(Math.abs(ln.qty_mg)))} g
                </span>
              </li>
            ))}
          </ul>
          {selectedDoc.source_lines?.length ? (
            <>
              <strong>Sumber konversi</strong>
              <ul>
                {selectedDoc.source_lines.map((sl, i) => (
                  <li key={i}>
                    {paintLabel(catalogMap.get(sl.paint_id), sl.paint_id)}:{" "}
                    {fmtG(sl.qty_g ?? gramsFromMg(sl.qty_mg))} g
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </PaintModal>
      ) : null}

      <SettingsConfirmDialog
        open={confirmOpen}
        request={confirmReq}
        busy={busy}
        onCancel={() => {
          setConfirmOpen(false);
          setPendingDeleteId(null);
          setPendingCorrectId(null);
        }}
        onConfirm={(reason) => {
          const delId = pendingDeleteId;
          const corr = pendingCorrectId;
          setConfirmOpen(false);
          if (delId) {
            void runAction("Dokumen dihapus (soft).", async () => {
              await softDeletePaintDocument(delId, {
                reason,
                idempotency_key: newPaintIdempotencyKey("sd"),
              });
              setModal(null);
              setSelectedDoc(null);
            });
          } else if (corr?.startsWith("finalize:")) {
            const sid = corr.slice("finalize:".length);
            void runAction("Stocktake finalized.", async () => {
              await finalizePaintStocktake(sid, {
                idempotency_key: newPaintIdempotencyKey("fin"),
                counts: stLines
                  .filter((l) => l.counted)
                  .map((l) => ({
                    paint_id: l.paint_id,
                    counted_g: Number(l.counted_g),
                  })),
              });
              setModal(null);
            });
          } else if (corr) {
            void runAction("Transaksi dikoreksi.", async () => {
              await correctPaintDocument(corr, {
                reason,
                idempotency_key: newPaintIdempotencyKey("corr"),
              });
              setModal(null);
              setSelectedDoc(null);
            });
          }
          setPendingDeleteId(null);
          setPendingCorrectId(null);
        }}
      />
    </div>
  );
}

function PaintModal(props: {
  title: string;
  kicker?: string;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
}) {
  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      onClick={(e) => {
        if (e.target === e.currentTarget) props.onClose();
      }}
    >
      <div
        className="ep-modal"
        role="dialog"
        aria-modal="true"
        aria-label={props.title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ep-modal-head">
          <div>
            {props.kicker ? (
              <div className="ep-modal-kicker">{props.kicker}</div>
            ) : null}
            <h2>{props.title}</h2>
          </div>
          <button
            type="button"
            className="ep-x"
            onClick={props.onClose}
            aria-label="Tutup"
          >
            ×
          </button>
        </div>
        <div className="ep-modal-body">{props.children}</div>
        <div className="ep-modal-foot">{props.footer}</div>
      </div>
    </div>
  );
}
