import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
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
import {
  allRequiredCounted,
  canCreateStocktakeSession,
  canEnterStocktakePreview,
  canFinalizeStocktake,
  conversionMassStatus,
  isPreviewStale,
  resolvePaintUserEmail,
  stocktakeCountsFingerprint,
} from "./paintWorkspaceGuards";

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
  const [stPreviewFingerprint, setStPreviewFingerprint] = useState<
    string | null
  >(null);
  const [stocktakeId, setStocktakeId] = useState<string | null>(null);
  const [modalTriggerEl, setModalTriggerEl] = useState<HTMLElement | null>(
    null,
  );

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
    setStPreviewFingerprint(null);
    setStStep(1);
  }, [branchId]);

  function openPaintModal(
    next: ActionModal,
    trigger?: EventTarget | null,
  ) {
    setModalTriggerEl(
      trigger instanceof HTMLElement ? trigger : null,
    );
    setModal(next);
  }

  function closePaintModal() {
    if (busy) return;
    setModal(null);
    setSelectedDoc(null);
    const el = modalTriggerEl;
    setModalTriggerEl(null);
    window.setTimeout(() => el?.focus(), 0);
  }

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
  const convSourcesReady = convSources.every(
    (s) =>
      Boolean(s.withdrawal_id) &&
      Boolean(s.withdrawal_line_id) &&
      Number(s.qty_g) > 0,
  );
  const convResultReady =
    Boolean(convForm.destination_paint_id) &&
    Number(convForm.verified_return_g) > 0;
  const convStatus = conversionMassStatus({
    sourcesReady: convSourcesReady,
    resultReady: convResultReady,
    sourceTotal: convSourceTotal,
    destG: convDest,
    wasteG: convWaste,
  });
  const stAllCounted = allRequiredCounted(stLines);
  const stCanPreview = canEnterStocktakePreview(branchId, stLines);
  const stPreviewFresh =
    Boolean(stPreview) &&
    !isPreviewStale(stLines, stPreviewFingerprint);
  const stCanSession = canCreateStocktakeSession({
    branchId,
    lines: stLines,
    preview: stPreview,
    previewFingerprint: stPreviewFingerprint,
  });
  const stCanFinalize = canFinalizeStocktake({
    stocktakeId,
    lines: stLines,
  });
  const stPreviewSummary = useMemo(() => {
    const raw = stPreview?.lines;
    const lines = Array.isArray(raw)
      ? (raw as Array<{ delta_mg?: number | null; counted_mg?: number | null }>)
      : [];
    const lineCount = Number(stPreview?.line_count ?? lines.length) || 0;
    const counted = lines.filter((l) => l.counted_mg != null).length || stLines.length;
    const withDiff = lines.filter(
      (l) => l.delta_mg != null && Number(l.delta_mg) !== 0,
    );
    const totalDeltaMg = withDiff.reduce(
      (s, l) => s + Number(l.delta_mg || 0),
      0,
    );
    return {
      lineCount,
      counted,
      diffCount: withDiff.length,
      totalDeltaG: totalDeltaMg / 1000,
    };
  }, [stPreview, stLines.length]);

  function confirmDocDetails(doc: PaintDocument): string[] {
    const lines = (doc.lines || []).map(
      (ln) =>
        `${paintLabel(catalogMap.get(ln.paint_id), ln.paint_id)}: ${fmtG(ln.qty_g ?? gramsFromMg(Math.abs(ln.qty_mg)))} g`,
    );
    return [
      `Identitas: ${DOC_LABEL[doc.doc_type] || doc.doc_type} · ${doc.document_id}`,
      `Status: ${doc.status} · Cabang: ${doc.branch_id}`,
      `Pengguna: ${doc.used_by || doc.recorded_by || "—"}`,
      ...(lines.length ? [`Item: ${lines.join("; ")}`] : []),
    ];
  }

  function openSoftDelete(doc: PaintDocument) {
    setPendingDeleteId(doc.document_id);
    setPendingCorrectId(null);
    setConfirmReq({
      title: "Soft Delete transaksi cat",
      message: `${DOC_LABEL[doc.doc_type] || doc.doc_type} · ${doc.document_id}`,
      details: [
        ...confirmDocDetails(doc),
        "Dampak: stok dibalikkan via ledger compensating jika aman; jejak audit tetap ada.",
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
        ...confirmDocDetails(doc),
        "Dampak: koreksi memakai compensating ledger (bukan overwrite diam-diam).",
        "Transaksi dengan return/konversi/waste terkait dapat ditolak backend.",
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

      <div
        className="ep-paint-toolbar"
        data-testid="paint-toolbar"
        aria-label="Toolbar Manajemen Cat"
      >
        <div className="ep-paint-toolbar-scroll">
          <div
            className="ep-paint-toolbar-nav"
            role="tablist"
            aria-label="Area Manajemen Cat"
          >
            {PRIMARY_TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                className={`ep-settings-subtab ep-paint-toolbar-item${tab === t.id ? " is-active" : ""}`}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <span className="ep-paint-toolbar-sep" aria-hidden="true" />
          <div
            className="ep-paint-toolbar-actions"
            aria-label="Aksi utama"
          >
            {can("paint.withdraw.create") ? (
              <button
                type="button"
                className="ep-btn is-primary-action ep-paint-toolbar-item"
                onClick={(e) => {
                  openPaintModal("withdraw", e.currentTarget);
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
                className="ep-btn is-primary-action ep-paint-toolbar-item"
                title="Pengembalian / Konversi"
                onClick={(e) => {
                  openPaintModal("return", e.currentTarget);
                  void refreshDocs();
                }}
              >
                Pengembalian/Konversi
              </button>
            ) : null}
            {can("paint.inbound.confirm") ? (
              <button
                type="button"
                className="ep-btn ep-paint-toolbar-item"
                onClick={(e) => openPaintModal("inbound", e.currentTarget)}
              >
                Cat Masuk
              </button>
            ) : null}
            {can("paint.waste.record") ? (
              <button
                type="button"
                className="ep-btn ep-paint-toolbar-item"
                onClick={(e) => {
                  openPaintModal("waste", e.currentTarget);
                  void refreshDocs();
                }}
              >
                Waste
              </button>
            ) : null}
            {can("paint.stocktake.run") ? (
              <button
                type="button"
                className="ep-btn ep-paint-toolbar-item"
                onClick={(e) => {
                  openPaintModal("stocktake", e.currentTarget);
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
                  setStPreviewFingerprint(null);
                }}
              >
                Stock Opname
              </button>
            ) : null}
          </div>
        </div>
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
              openPaintModal("detail", document.activeElement);
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
          busy={busy}
          onClose={closePaintModal}
          footer={
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={closePaintModal} disabled={busy}>
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
            className="ep-paint-form"
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
                closePaintModal();
              });
            }}
          >
            <p className="muted">
              Manual terkontrol. Integrasi Accurate purchasing tetap nonaktif.
            </p>
            <div className="ep-paint-form-grid">
              <label>
                Jenis cat
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
                Berat (g)
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
            </div>
          </form>
        </PaintModal>
      ) : null}

      {modal === "withdraw" ? (
        <PaintModal
          title="Pengambilan Cat"
          kicker="Serah fisik"
          busy={busy}
          onClose={closePaintModal}
          footer={
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={closePaintModal} disabled={busy}>
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
            className="ep-paint-form"
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
              const resolvedEmail = resolvePaintUserEmail(
                withdrawForm.used_by_email,
                users,
              );
              if (!resolvedEmail) {
                setError(
                  "Pilih karyawan valid dari hasil pencarian (nama atau email).",
                );
                return;
              }
              void runAction("Pengambilan tercatat.", async () => {
                await createPaintWithdrawal({
                  branch_id: branchId,
                  used_by_email: resolvedEmail,
                  purpose_id: withdrawForm.purpose_id,
                  accurate_po_id: withdrawForm.accurate_po_id || undefined,
                  note: withdrawForm.note || undefined,
                  items,
                  idempotency_key: newPaintIdempotencyKey("wd"),
                });
                closePaintModal();
                setWithdrawItems([{ paint_id: "", qty_g: "" }]);
              });
            }}
          >
            <section className="ep-paint-form-section">
              <div className="ep-paint-form-grid is-3">
                <label>
                  Pengguna
                  <input
                    list="paint-user-list"
                    required
                    type="text"
                    autoComplete="off"
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
                    placeholder="Cari nama atau email"
                    aria-describedby="paint-user-hint"
                  />
                  <datalist id="paint-user-list">
                    {users.map((u) => (
                      <option key={u.email} value={u.email}>
                        {u.display_name || u.email}
                      </option>
                    ))}
                  </datalist>
                  <span id="paint-user-hint" className="ep-paint-stock-hint">
                    Pilih dari hasil pencarian backend.
                  </span>
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
                  {purposeNeedsPo ? "Referensi PO" : "Referensi PO (opsional)"}
                  <input
                    required={Boolean(purposeNeedsPo)}
                    value={withdrawForm.accurate_po_id}
                    onChange={(e) =>
                      setWithdrawForm((s) => ({
                        ...s,
                        accurate_po_id: e.target.value,
                      }))
                    }
                  />
                </label>
              </div>
            </section>
            <section className="ep-paint-form-section">
              <h3 className="ep-paint-form-section-title">Daftar cat</h3>
              <div className="ep-paint-item-rows">
                {withdrawItems.map((it, idx) => (
                  <div className="ep-paint-item-row" key={idx}>
                    <label>
                      Jenis cat
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
                              {paintLabel(c, c.paint_id)}
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
                    <label>
                      Stok tersedia
                      <input
                        type="text"
                        readOnly
                        tabIndex={-1}
                        value={
                          it.paint_id
                            ? `${fmtG(stockByPaint.get(it.paint_id) ?? 0)} g`
                            : "—"
                        }
                        aria-label="Stok tersedia"
                      />
                    </label>
                    <button
                      type="button"
                      className="ep-btn"
                      disabled={withdrawItems.length <= 1}
                      onClick={() =>
                        setWithdrawItems((rows) =>
                          rows.filter((_, i) => i !== idx),
                        )
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
            </section>
            <div className="ep-paint-mass-box">
              Total gram:{" "}
              <strong className="ep-paint-qty">{fmtG(withdrawTotal)} g</strong>
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
          busy={busy}
          onClose={closePaintModal}
          footer={
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={closePaintModal} disabled={busy}>
                Batal
              </button>
              <button
                type="submit"
                form="paint-return-form"
                className="ep-btn primary"
                disabled={
                  busy ||
                  (returnMode === "black" && convStatus !== "balanced")
                }
              >
                Konfirmasi Pengembalian / Konversi
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
              className="ep-paint-form"
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
                  closePaintModal();
                });
              }}
            >
              {!can("paint.return.confirm") ? (
                <p className="muted">Tidak berwenang konfirmasi pengembalian.</p>
              ) : (
                <section className="ep-paint-form-section">
                  <h3 className="ep-paint-form-section-title">Pengembalian warna asal</h3>
                  <div className="ep-paint-form-grid">
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
                            {(d.used_by || "—") +
                              (d.created_at
                                ? ` · ${new Date(d.created_at).toLocaleString("id-ID", { timeZone: "Asia/Jakarta", dateStyle: "short", timeStyle: "short" })}`
                                : "")}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Item cat
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
                              {paintLabel(catalogMap.get(ln.paint_id), ln.paint_id)}
                            </option>
                          ),
                        )}
                      </select>
                    </label>
                    <label>
                      Outstanding (g)
                      <input
                        type="text"
                        readOnly
                        tabIndex={-1}
                        value={
                          returnForm.withdrawal_line_id
                            ? `${fmtG(
                                (outstandingByWd[returnForm.withdrawal_id] || []).find(
                                  (o) =>
                                    o.line_id === returnForm.withdrawal_line_id,
                                )?.outstanding_g,
                              )} g`
                            : "—"
                        }
                      />
                    </label>
                    <label>
                      Berat dikembalikan (g)
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
                      {returnForm.withdrawal_line_id ? (
                        <span className="ep-paint-stock-hint">
                          Maksimum return:{" "}
                          {fmtG(
                            (outstandingByWd[returnForm.withdrawal_id] || []).find(
                              (o) => o.line_id === returnForm.withdrawal_line_id,
                            )?.outstanding_g,
                          )}{" "}
                          g
                        </span>
                      ) : null}
                    </label>
                  </div>
                </section>
              )}
            </form>
          ) : (
            <form
              id="paint-return-form"
              className="ep-paint-form"
              onSubmit={(e) => {
                e.preventDefault();
                if (!branchId) {
                  setError("Pilih cabang terlebih dahulu.");
                  return;
                }
                if (convStatus !== "balanced") {
                  setError(
                    convStatus === "incomplete"
                      ? "Lengkapi sumber dan hasil timbangan terlebih dahulu."
                      : "Hasil + waste tidak boleh melebihi total gram sumber.",
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
                  closePaintModal();
                });
              }}
            >
              {!can("paint.conversion.confirm") ? (
                <p className="muted">Tidak berwenang konversi warna.</p>
              ) : (
                <>
                  <section className="ep-paint-form-section">
                    <h3 className="ep-paint-form-section-title">1. Sumber cat</h3>
                    <div className="ep-paint-item-rows">
                      {convSources.map((src, idx) => {
                        const outLn = (
                          outstandingByWd[src.withdrawal_id] || []
                        ).find((l) => l.line_id === src.withdrawal_line_id);
                        return (
                          <div className="ep-paint-source-card" key={idx}>
                            <div className="ep-paint-source-card-head">
                              <strong>Sumber #{idx + 1}</strong>
                              {convSources.length > 1 ? (
                                <button
                                  type="button"
                                  className="ep-btn"
                                  onClick={() =>
                                    setConvSources((rows) =>
                                      rows.filter((_, i) => i !== idx),
                                    )
                                  }
                                >
                                  Hapus sumber
                                </button>
                              ) : null}
                            </div>
                            <div className="ep-paint-form-grid is-3">
                              <label>
                                Pengambilan asal
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
                                    <option
                                      key={d.document_id}
                                      value={d.document_id}
                                    >
                                      {(d.used_by || "—") +
                                        (d.created_at
                                          ? ` · ${new Date(d.created_at).toLocaleString("id-ID", { timeZone: "Asia/Jakarta", dateStyle: "short", timeStyle: "short" })}`
                                          : "")}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              <label>
                                Item / warna asal
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
                                  {(
                                    outstandingByWd[src.withdrawal_id] || []
                                  ).map((ln) => (
                                    <option key={ln.line_id} value={ln.line_id}>
                                      {paintLabel(
                                        catalogMap.get(ln.paint_id),
                                        ln.paint_id,
                                      )}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              <label>
                                Berat Sumber (g)
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
                                {outLn ? (
                                  <span className="ep-paint-stock-hint">
                                    Outstanding: {fmtG(outLn.outstanding_g)} g
                                  </span>
                                ) : null}
                              </label>
                            </div>
                          </div>
                        );
                      })}
                    </div>
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
                  </section>

                  <section className="ep-paint-form-section">
                    <h3 className="ep-paint-form-section-title">2. Hasil konversi</h3>
                    <div className="ep-paint-form-grid is-3">
                      <label>
                        Masuk ke Stok Hitam
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
                        Berat Hasil Timbangan (g)
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
                        Waste / Selisih (g)
                        <input
                          type="number"
                          min={0}
                          step="any"
                          value={convForm.waste_g}
                          onChange={(e) =>
                            setConvForm((s) => ({
                              ...s,
                              waste_g: e.target.value,
                            }))
                          }
                        />
                      </label>
                    </div>
                  </section>

                  <section className="ep-paint-form-section">
                    <h3 className="ep-paint-form-section-title">3. Rekonsiliasi</h3>
                    <div
                      className={`ep-paint-recon ${
                        convStatus === "balanced"
                          ? "is-ok"
                          : convStatus === "unbalanced"
                            ? "is-warn"
                            : ""
                      }`}
                      role="status"
                    >
                      <div className="ep-paint-recon-row">
                        <span>Total gram sumber</span>
                        <span className="ep-paint-qty">{fmtG(convSourceTotal)} g</span>
                      </div>
                      <div className="ep-paint-recon-row">
                        <span>Total gram kembali</span>
                        <span className="ep-paint-qty">{fmtG(convDest)} g</span>
                      </div>
                      <div className="ep-paint-recon-row">
                        <span>Waste</span>
                        <span className="ep-paint-qty">{fmtG(convWaste)} g</span>
                      </div>
                      <div className="ep-paint-recon-row">
                        <span>Selisih (sumber − hasil − waste)</span>
                        <span className="ep-paint-qty">
                          {convStatus === "incomplete"
                            ? "—"
                            : `${fmtG(convSourceTotal - convDest - convWaste)} g`}
                        </span>
                      </div>
                      <div className="ep-paint-recon-row is-span-2">
                        <p className="ep-paint-recon-status">
                          Status:{" "}
                          {convStatus === "incomplete"
                            ? "Belum Lengkap"
                            : convStatus === "unbalanced"
                              ? "Tidak Seimbang"
                              : "Seimbang"}
                        </p>
                      </div>
                    </div>
                  </section>
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
          busy={busy}
          onClose={closePaintModal}
          footer={
            <div className="ep-modal-foot-actions">
              <button type="button" className="ep-btn" onClick={closePaintModal} disabled={busy}>
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
            className="ep-paint-form"
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
                closePaintModal();
              });
            }}
          >
            <div className="ep-paint-form-grid">
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
                      {paintLabel(catalogMap.get(ln.paint_id), ln.paint_id)} ·
                      sisa {fmtG(ln.outstanding_g)} g
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
                {wasteForm.withdrawal_line_id ? (
                  <span className="ep-paint-stock-hint">
                    Maksimum:{" "}
                    {fmtG(
                      (outstandingByWd[wasteForm.withdrawal_id] || []).find(
                        (o) => o.line_id === wasteForm.withdrawal_line_id,
                      )?.outstanding_g,
                    )}{" "}
                    g outstanding
                  </span>
                ) : null}
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
            </div>
          </form>
        </PaintModal>
      ) : null}

      {modal === "stocktake" ? (
        <PaintModal
          title="Stock Opname"
          kicker={`Langkah ${stStep} dari 4`}
          busy={busy}
          onClose={closePaintModal}
          footer={
            <div className="ep-modal-foot-actions">
              {stStep > 1 ? (
                <button
                  type="button"
                  className="ep-btn"
                  disabled={busy}
                  onClick={() => setStStep((s) => (s > 1 ? ((s - 1) as 1 | 2 | 3 | 4) : s))}
                >
                  Kembali
                </button>
              ) : null}
              <button
                type="button"
                className="ep-btn"
                disabled={busy}
                onClick={closePaintModal}
              >
                Batal
              </button>
              {stStep === 1 ? (
                <button
                  type="button"
                  className="ep-btn primary"
                  disabled={!stCanPreview}
                  onClick={() => setStStep(2)}
                >
                  Lanjut preview
                </button>
              ) : null}
              {stStep === 2 ? (
                <>
                  <button
                    type="button"
                    className="ep-btn"
                    disabled={busy || !stCanPreview}
                    onClick={() =>
                      void runAction("Preview dihitung ulang (stok belum berubah).", async () => {
                        const fp = stocktakeCountsFingerprint(stLines);
                        const res = await previewPaintStocktake({
                          branch_id: branchId,
                          counts: stLines.map((l) => ({
                            paint_id: l.paint_id,
                            counted_g: Number(l.counted_g),
                          })),
                        });
                        setStPreview(res.stocktake);
                        setStPreviewFingerprint(fp);
                      })
                    }
                  >
                    Jalankan preview
                  </button>
                  <button
                    type="button"
                    className="ep-btn primary"
                    disabled={!stCanSession}
                    onClick={() => setStStep(3)}
                  >
                    Lanjut buat sesi
                  </button>
                </>
              ) : null}
              {stStep === 3 ? (
                <button
                  type="button"
                  className="ep-btn primary"
                  disabled={busy || !stCanSession}
                  onClick={() =>
                    void runAction("Sesi stocktake disimpan.", async () => {
                      try {
                        const res = await createPaintStocktakeSession({
                          branch_id: branchId,
                          counts: stLines.map((l) => ({
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
                      } catch (err) {
                        const msg =
                          err instanceof Error ? err.message : "Gagal buat sesi.";
                        if (/conflict|versi|version|cas/i.test(msg)) {
                          setStPreview(null);
                          setStPreviewFingerprint(null);
                          setStStep(2);
                          throw new Error(
                            `${msg} — jalankan preview ulang sebelum membuat sesi.`,
                          );
                        }
                        throw err;
                      }
                    })
                  }
                >
                  Buat sesi stocktake
                </button>
              ) : null}
              {stStep === 4 ? (
                <button
                  type="button"
                  className="ep-btn primary"
                  disabled={
                    busy ||
                    !stCanFinalize ||
                    !can("paint.stocktake.finalize")
                  }
                  onClick={() => {
                    if (!stocktakeId || !stCanFinalize) return;
                    setConfirmReq({
                      title: "Finalisasi Stock Opname",
                      message:
                        "Periode akan dikunci sampai cutoff snapshot. Tindakan ini memakai hitungan sesi tersimpan.",
                      details: [
                        `Cabang: ${branchId}`,
                        `Session: ${stocktakeId}`,
                        `Item dihitung: ${stLines.length}`,
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
              ) : null}
            </div>
          }
        >
          <div className="ep-paint-wizard-steps" aria-label="Progress stocktake">
            {[
              [1, "Hitung fisik"],
              [2, "Preview selisih"],
              [3, "Buat sesi"],
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
            <div className="ep-paint-form">
              {!branchId ? (
                <p className="ep-error">Pilih cabang di header sebelum stocktake.</p>
              ) : null}
              <p className="muted">
                Isi gram fisik untuk setiap item. Angka 0 sah; kolom kosong belum
                dihitung.
              </p>
              <div style={{ overflowX: "auto", maxWidth: "100%" }}>
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
                            aria-label={`Hitung fisik ${paintLabel(catalogMap.get(ln.paint_id), ln.paint_id)}`}
                            onChange={(e) => {
                              const v = e.target.value;
                              setStLines((rows) =>
                                rows.map((r) =>
                                  r.paint_id === ln.paint_id
                                    ? {
                                        ...r,
                                        counted_g: v,
                                        counted: v.trim() !== "",
                                      }
                                    : r,
                                ),
                              );
                              setStPreview(null);
                              setStPreviewFingerprint(null);
                            }}
                          />
                        </td>
                        <td>{ln.counted_g.trim() !== "" ? "Dihitung" : "Belum"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!stAllCounted ? (
                <p className="ep-error" role="status">
                  Belum dihitung:{" "}
                  {stLines.filter((l) => l.counted_g.trim() === "").length} item
                  wajib.
                </p>
              ) : null}
            </div>
          ) : null}

          {stStep === 2 ? (
            <div className="ep-paint-form">
              {!stAllCounted ? (
                <p className="ep-error">
                  Ada item belum dihitung. Kembali ke langkah 1.
                </p>
              ) : null}
              {stPreview && !stPreviewFresh ? (
                <p className="ep-error" role="alert">
                  Preview kedaluwarsa karena angka fisik berubah. Jalankan preview
                  ulang.
                </p>
              ) : null}
              {stPreviewFresh ? (
                <div className="ep-paint-mass-box is-ok" role="status">
                  <p style={{ margin: "0 0 0.5rem" }}>
                    Preview berhasil. Terdapat {stPreviewSummary.lineCount} jenis
                    cat yang diperiksa. Stok belum berubah.
                  </p>
                  <div className="ep-paint-recon" style={{ border: 0, padding: 0, background: "transparent" }}>
                    <div className="ep-paint-recon-row">
                      <span>Jumlah cat dihitung</span>
                      <span className="ep-paint-qty">{stPreviewSummary.counted}</span>
                    </div>
                    <div className="ep-paint-recon-row">
                      <span>Jumlah cat berselisih</span>
                      <span className="ep-paint-qty">{stPreviewSummary.diffCount}</span>
                    </div>
                    <div className="ep-paint-recon-row">
                      <span>Total selisih</span>
                      <span className="ep-paint-qty">
                        {fmtG(stPreviewSummary.totalDeltaG)} g
                      </span>
                    </div>
                    <div className="ep-paint-recon-row is-span-2">
                      <p className="ep-paint-recon-status">
                        Status lanjut: {stCanSession ? "Siap buat sesi" : "Belum siap"}
                      </p>
                    </div>
                  </div>
                </div>
              ) : (
                <p className="muted">
                  Jalankan preview untuk melihat selisih tanpa mengubah stok.
                </p>
              )}
            </div>
          ) : null}

          {stStep === 3 ? (
            <div className="ep-paint-form">
              {!stCanSession ? (
                <p className="ep-error">
                  Buat sesi hanya setelah preview sukses atas hitungan terkini.
                </p>
              ) : (
                <p className="muted">
                  Preview valid untuk cabang {branchId}. Membuat sesi akan
                  menyimpan snapshot untuk finalisasi.
                </p>
              )}
              {stocktakeId ? (
                <p className="muted">Session: {stocktakeId}</p>
              ) : null}
            </div>
          ) : null}

          {stStep === 4 ? (
            <div className="ep-paint-form">
              <div className="ep-paint-mass-box">
                Cabang {branchId} · Dihitung{" "}
                {stLines.filter((l) => l.counted_g.trim() !== "").length}/
                {stLines.length} · Session {stocktakeId || "—"}
              </div>
              <p className="muted">
                Finalisasi mengunci periode sampai cutoff snapshot. Pastikan semua
                item wajib sudah dihitung.
              </p>
              {!stAllCounted ? (
                <p className="ep-error">Masih ada item wajib belum dihitung.</p>
              ) : null}
            </div>
          ) : null}
        </PaintModal>
      ) : null}

      {modal === "detail" && selectedDoc ? (
        <PaintModal
          title={DOC_LABEL[selectedDoc.doc_type] || selectedDoc.doc_type}
          kicker={selectedDoc.document_id}
          busy={busy}
          onClose={closePaintModal}
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
                disabled={busy}
                onClick={closePaintModal}
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
          if (busy) return;
          const delId = pendingDeleteId;
          const corr = pendingCorrectId;
          if (delId) {
            void runAction("Dokumen dihapus (soft).", async () => {
              await softDeletePaintDocument(delId, {
                reason,
                idempotency_key: newPaintIdempotencyKey("sd"),
              });
              setConfirmOpen(false);
              setPendingDeleteId(null);
              setPendingCorrectId(null);
              closePaintModal();
            });
          } else if (corr?.startsWith("finalize:")) {
            const sid = corr.slice("finalize:".length);
            void runAction("Stocktake finalized.", async () => {
              try {
                await finalizePaintStocktake(sid, {
                  idempotency_key: newPaintIdempotencyKey("fin"),
                  counts: stLines.map((l) => ({
                    paint_id: l.paint_id,
                    counted_g: Number(l.counted_g),
                  })),
                });
                setConfirmOpen(false);
                setPendingDeleteId(null);
                setPendingCorrectId(null);
                closePaintModal();
              } catch (err) {
                const msg =
                  err instanceof Error ? err.message : "Finalisasi gagal.";
                if (/conflict|versi|version|cas/i.test(msg)) {
                  setConfirmOpen(false);
                  setPendingCorrectId(null);
                  setStPreview(null);
                  setStPreviewFingerprint(null);
                  setStStep(2);
                  throw new Error(
                    `${msg} — jalankan preview ulang, lalu buat sesi baru bila perlu.`,
                  );
                }
                throw err;
              }
            });
          } else if (corr) {
            void runAction("Transaksi dikoreksi.", async () => {
              await correctPaintDocument(corr, {
                reason,
                idempotency_key: newPaintIdempotencyKey("corr"),
              });
              setConfirmOpen(false);
              setPendingDeleteId(null);
              setPendingCorrectId(null);
              closePaintModal();
            });
          }
        }}
      />
    </div>
  );
}

function PaintModal(props: {
  title: string;
  kicker?: string;
  busy?: boolean;
  onClose: () => void;
  children: ReactNode;
  footer: ReactNode;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const root = dialogRef.current;
    if (!root) return;
    const focusables = () =>
      [
        ...root.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ].filter((el) => !el.hasAttribute("disabled"));
    window.setTimeout(() => {
      const list = focusables();
      (list.find((el) => el !== closeRef.current) || closeRef.current)?.focus();
    }, 0);

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (!props.busy) {
          e.preventDefault();
          props.onClose();
        }
        return;
      }
      if (e.key !== "Tab") return;
      const list = focusables();
      if (!list.length) return;
      const first = list[0];
      const last = list[list.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.busy, props.onClose, props.title]);

  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      onClick={(e) => {
        if (e.target === e.currentTarget && !props.busy) props.onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="ep-modal ep-paint-modal"
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
            ref={closeRef}
            type="button"
            className="ep-x"
            onClick={props.onClose}
            aria-label="Tutup"
            disabled={props.busy}
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
