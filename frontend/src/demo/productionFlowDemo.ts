import type { StageTableRow } from "../app/productionFlowStageTables";

/**
 * Fixture DEMO Alur Produksi — isolasi frontend saja.
 * Nomor PO berawalan DEMO-FLOW agar tidak tertukar data operasional.
 * Pemetaan tahap eksplisit; bukan aturan bisnis/Accurate.
 */

type DemoFlowRow = StageTableRow & { stageId: string };

const C = {
  a: "Demo Mitra Nusantara (SIM)",
  b: "Demo Cahaya Packindo (SIM)",
  c: "Demo Sari Kemasan Fiktif (SIM)",
  d: "Demo Prima Label Test (SIM)",
  e: "Demo Angkasa Print Lab (SIM)",
  f: "Demo Biru Laut Sample (SIM)",
} as const;

/** ~30 PO sintetis + baris tahap eksplisit (minimal 1 per tahap). */
const FLOW_DEMO_ROWS: DemoFlowRow[] = [
  // —— Order Masuk (PO-level) ——
  {
    stageId: "flow-order-masuk",
    key: "om-DEMO-FLOW-PO-001",
    values: {
      poNo: "DEMO-FLOW-PO-001",
      poDate: "2026-03-02",
      customer: C.a,
      orderStatus: "Normal",
      itemCount: "2",
      totalQty: "5.000 pcs",
      etaShip: "2026-03-20",
      seller: "Demo Seller A",
    },
  },
  {
    stageId: "flow-order-masuk",
    key: "om-DEMO-FLOW-PO-002",
    values: {
      poNo: "DEMO-FLOW-PO-002",
      poDate: "2026-03-03",
      customer: C.b,
      orderStatus: "Urgent",
      itemCount: "1",
      totalQty: "2.400 pcs",
      etaShip: "2026-03-18",
      seller: "Demo Seller B",
    },
  },
  {
    stageId: "flow-order-masuk",
    key: "om-DEMO-FLOW-PO-003",
    values: {
      poNo: "DEMO-FLOW-PO-003",
      poDate: "2026-03-04",
      customer: C.c,
      orderStatus: "Normal",
      itemCount: "3",
      totalQty: "8.100 pcs",
      etaShip: "2026-03-25",
      seller: "Demo Seller A",
    },
  },

  // —— Design (item-level) ——
  {
    stageId: "flow-design",
    key: "ds-DEMO-FLOW-PO-004-1",
    values: {
      poNo: "DEMO-FLOW-PO-004",
      customer: C.d,
      itemName: "Cup PET 12oz Demo Motif A",
      printColor: "Hijau Tua",
      designer: "Demo Designer 1",
      designStatus: "Draft",
      approvalStatus: "Belum diajukan",
      target: "08/03/2026",
      pdfUrl: "/demo/design-sample.pdf",
      pdfKind: "design",
    },
  },
  {
    stageId: "flow-design",
    key: "ds-DEMO-FLOW-PO-004-2",
    values: {
      poNo: "DEMO-FLOW-PO-004",
      customer: C.d,
      itemName: "Cup PET 16oz Demo Motif B",
      printColor: "Emas Soft",
      designer: "Demo Designer 1",
      designStatus: "Revisi",
      approvalStatus: "Belum diajukan",
      target: "09/03/2026",
      pdfUrl: "",
      pdfKind: "design",
    },
  },
  {
    stageId: "flow-design",
    key: "ds-DEMO-FLOW-PO-005-1",
    values: {
      poNo: "DEMO-FLOW-PO-005",
      customer: C.e,
      itemName: "Cup PP 14oz Demo Logo",
      printColor: "Hitam",
      designer: "Demo Designer 2",
      designStatus: "Draft",
      approvalStatus: "Belum diajukan",
      target: "10/03/2026",
      pdfUrl: "/demo/design-sample.pdf",
      pdfKind: "design",
    },
  },

  // —— Approval Design ——
  {
    stageId: "flow-approval-design",
    key: "ad-DEMO-FLOW-PO-006-1",
    values: {
      poNo: "DEMO-FLOW-PO-006",
      customer: C.f,
      itemName: "Cup PET 12oz Demo Wave",
      printColor: "Biru Soft",
      designer: "Demo Designer 2",
      designStatus: "Siap review",
      approvalStatus: "Menunggu",
      target: "11/03/2026",
      submittedAt: "2026-03-05",
      pdfUrl: "/demo/design-sample.pdf",
      pdfKind: "design",
    },
  },
  {
    stageId: "flow-approval-design",
    key: "ad-DEMO-FLOW-PO-007-1",
    values: {
      poNo: "DEMO-FLOW-PO-007",
      customer: C.a,
      itemName: "Cup PET 9oz Demo Stripe",
      printColor: "Merah",
      designer: "Demo Designer 1",
      designStatus: "Final (SIM)",
      approvalStatus: "Disetujui (SIM)",
      target: "07/03/2026",
      submittedAt: "2026-03-04",
      pdfUrl: "/demo/design-sample.pdf",
      pdfKind: "design",
    },
  },

  // —— Payment ——
  {
    stageId: "flow-payment",
    key: "pay-DEMO-FLOW-PO-008",
    values: {
      poNo: "DEMO-FLOW-PO-008",
      customer: C.b,
      paymentTerms: "DP 50% (SIM)",
      paymentStatus: "Belum lunas",
      paymentDate: "",
      verifyStatus: "Belum diajukan",
      submittedAt: "",
      confirmedAt: "",
      pdfUrl: "",
      pdfKind: "payment",
    },
  },
  {
    stageId: "flow-payment",
    key: "pay-DEMO-FLOW-PO-009",
    values: {
      poNo: "DEMO-FLOW-PO-009",
      customer: C.c,
      paymentTerms: "Tempo 14 hari (SIM)",
      paymentStatus: "Sebagian",
      paymentDate: "2026-03-06",
      verifyStatus: "Menunggu verifikasi",
      submittedAt: "2026-03-06",
      confirmedAt: "",
      pdfUrl: "/demo/payment-sample.pdf",
      pdfKind: "payment",
    },
  },

  // —— Approval Payment ——
  {
    stageId: "flow-approval-payment",
    key: "ap-DEMO-FLOW-PO-010",
    values: {
      poNo: "DEMO-FLOW-PO-010",
      customer: C.d,
      paymentTerms: "DP 30% (SIM)",
      paymentStatus: "Menunggu",
      paymentDate: "",
      verifyStatus: "Menunggu verifikasi",
      submittedAt: "2026-03-06",
      confirmedAt: "",
      pdfUrl: "/demo/payment-sample.pdf",
      pdfKind: "payment",
    },
  },
  {
    stageId: "flow-approval-payment",
    key: "ap-DEMO-FLOW-PO-011",
    values: {
      poNo: "DEMO-FLOW-PO-011",
      customer: C.e,
      paymentTerms: "Lunas di muka (SIM)",
      paymentStatus: "Tercatat (SIM)",
      paymentDate: "2026-03-05",
      verifyStatus: "Terverifikasi (SIM)",
      submittedAt: "2026-03-05",
      confirmedAt: "2026-03-06",
      pdfUrl: "/demo/payment-sample.pdf",
      pdfKind: "payment",
    },
  },

  // —— Penjadwalan ——
  {
    stageId: "flow-penjadwalan",
    key: "sch-DEMO-FLOW-PO-012-1",
    values: {
      poNo: "DEMO-FLOW-PO-012",
      customer: C.f,
      itemName: "Cup PET 12oz Demo Grid",
      qtyPo: "3.000",
      qtyScheduled: "1.500",
      machine: "Mesin Demo-01",
      scheduleDate: "2026-03-12",
    },
  },
  {
    stageId: "flow-penjadwalan",
    key: "sch-DEMO-FLOW-PO-013-1",
    values: {
      poNo: "DEMO-FLOW-PO-013",
      customer: C.a,
      itemName: "Cup PET 16oz Demo Dot",
      qtyPo: "4.200",
      qtyScheduled: "4.200",
      machine: "Mesin Demo-02",
      scheduleDate: "2026-03-13",
    },
  },

  // —— Screener ——
  {
    stageId: "flow-screener",
    key: "scr-DEMO-FLOW-PO-014-1",
    values: {
      poNo: "DEMO-FLOW-PO-014",
      customer: C.b,
      itemName: "Cup PP 12oz Demo Leaf",
      printColor: "Hijau Muda",
      screener: "Demo Screener 1",
      screenStatus: "Proses",
    },
  },
  {
    stageId: "flow-screener",
    key: "scr-DEMO-FLOW-PO-015-1",
    values: {
      poNo: "DEMO-FLOW-PO-015",
      customer: C.c,
      itemName: "Cup PET 14oz Demo Circle",
      printColor: "Biru Navy",
      screener: "Demo Screener 2",
      screenStatus: "Siap",
    },
  },

  // —— Persiapan ——
  {
    stageId: "flow-persiapan",
    key: "prep-DEMO-FLOW-PO-016-1",
    values: {
      poNo: "DEMO-FLOW-PO-016",
      customer: C.d,
      itemName: "Cup PET 12oz Demo Cup Prep",
      qtyNeeded: "2.000",
      qtyPrepared: "1.200",
      prepStatus: "Sebagian",
    },
  },
  {
    stageId: "flow-persiapan",
    key: "prep-DEMO-FLOW-PO-017-1",
    values: {
      poNo: "DEMO-FLOW-PO-017",
      customer: C.e,
      itemName: "Cup PET 9oz Demo Cup Prep",
      qtyNeeded: "6.000",
      qtyPrepared: "6.000",
      prepStatus: "Lengkap (SIM)",
    },
  },

  // —— Operator ——
  {
    stageId: "flow-operator",
    key: "op-DEMO-FLOW-PO-018-1",
    values: {
      poNo: "DEMO-FLOW-PO-018",
      customer: C.f,
      itemName: "Cup PET 12oz Demo Run",
      machine: "Mesin Demo-03",
      operator: "Demo Operator 1",
      qtyTarget: "2.500",
      qtyPrinted: "800",
    },
  },
  {
    stageId: "flow-operator",
    key: "op-DEMO-FLOW-PO-019-1",
    values: {
      poNo: "DEMO-FLOW-PO-019",
      customer: C.a,
      itemName: "Cup PET 16oz Demo Run",
      machine: "Mesin Demo-01",
      operator: "Demo Operator 2",
      qtyTarget: "3.600",
      qtyPrinted: "3.100",
    },
  },

  // —— Konfirmasi Sample ——
  {
    stageId: "flow-konfirmasi-sample",
    key: "smp-DEMO-FLOW-PO-020-1",
    values: {
      poNo: "DEMO-FLOW-PO-020",
      customer: C.b,
      itemName: "Cup PET 12oz Demo Sample",
      sampleDate: "2026-03-07",
      confirmStatus: "Menunggu konfirmasi",
    },
  },
  {
    stageId: "flow-konfirmasi-sample",
    key: "smp-DEMO-FLOW-PO-021-1",
    values: {
      poNo: "DEMO-FLOW-PO-021",
      customer: C.c,
      itemName: "Cup PP 14oz Demo Sample",
      sampleDate: "2026-03-06",
      confirmStatus: "Disetujui (SIM)",
    },
  },

  // —— Cetak ——
  {
    stageId: "flow-cetak",
    key: "ctk-DEMO-FLOW-PO-022-1",
    values: {
      poNo: "DEMO-FLOW-PO-022",
      customer: C.d,
      itemName: "Cup PET 12oz Demo Print",
      machine: "Mesin Demo-02",
      qtyPo: "5.000",
      qtyPrinted: "2.200",
      qtyRemaining: "2.800",
    },
  },
  {
    stageId: "flow-cetak",
    key: "ctk-DEMO-FLOW-PO-023-1",
    values: {
      poNo: "DEMO-FLOW-PO-023",
      customer: C.e,
      itemName: "Cup PET 16oz Demo Print",
      machine: "Mesin Demo-03",
      qtyPo: "1.800",
      qtyPrinted: "1.800",
      qtyRemaining: "0",
    },
  },

  // —— Packing ——
  {
    stageId: "flow-packing",
    key: "pkg-DEMO-FLOW-PO-024-1",
    values: {
      poNo: "DEMO-FLOW-PO-024",
      customer: C.f,
      itemName: "Cup PET 12oz Demo Pack",
      qtyPrinted: "4.000",
      qtyPacked: "3.500",
    },
  },
  {
    stageId: "flow-packing",
    key: "pkg-DEMO-FLOW-PO-025-1",
    values: {
      poNo: "DEMO-FLOW-PO-025",
      customer: C.a,
      itemName: "Cup PET 9oz Demo Pack",
      qtyPrinted: "2.700",
      qtyPacked: "2.700",
    },
  },

  // —— Surat Jalan ——
  {
    stageId: "flow-surat-jalan",
    key: "sj-DEMO-FLOW-SJ-001",
    values: {
      sjNo: "DEMO-FLOW-SJ-001",
      poNo: "DEMO-FLOW-PO-026",
      customer: C.b,
      qtyShip: "2.000",
      sjDate: "2026-03-14",
    },
  },
  {
    stageId: "flow-surat-jalan",
    key: "sj-DEMO-FLOW-SJ-002",
    values: {
      sjNo: "DEMO-FLOW-SJ-002",
      poNo: "DEMO-FLOW-PO-027",
      customer: C.c,
      qtyShip: "3.300",
      sjDate: "2026-03-15",
    },
  },

  // —— Loading ——
  {
    stageId: "flow-loading",
    key: "ld-DEMO-FLOW-SJ-003",
    values: {
      sjNo: "DEMO-FLOW-SJ-003",
      poNo: "DEMO-FLOW-PO-028",
      customer: C.d,
      qtyLoading: "2.500",
      loadingStatus: "Siap muat",
    },
  },
  {
    stageId: "flow-loading",
    key: "ld-DEMO-FLOW-SJ-004",
    values: {
      sjNo: "DEMO-FLOW-SJ-004",
      poNo: "DEMO-FLOW-PO-029",
      customer: C.e,
      qtyLoading: "1.100",
      loadingStatus: "Dimuat (SIM)",
    },
  },

  // —— Pengiriman ——
  {
    stageId: "flow-pengiriman",
    key: "kir-DEMO-FLOW-SJ-005",
    values: {
      sjNo: "DEMO-FLOW-SJ-005",
      poNo: "DEMO-FLOW-PO-030",
      customer: C.f,
      driver: "Demo Driver 1",
      shipDate: "2026-03-16",
      shipStatus: "Dalam perjalanan",
    },
  },
  {
    stageId: "flow-pengiriman",
    key: "kir-DEMO-FLOW-SJ-006",
    values: {
      sjNo: "DEMO-FLOW-SJ-006",
      poNo: "DEMO-FLOW-PO-031",
      customer: C.a,
      driver: "Demo Driver 2",
      shipDate: "2026-03-17",
      shipStatus: "Terkirim (SIM)",
    },
  },
];

const BY_STAGE = new Map<string, StageTableRow[]>();
for (const row of FLOW_DEMO_ROWS) {
  const list = BY_STAGE.get(row.stageId) ?? [];
  list.push({ key: row.key, values: row.values });
  BY_STAGE.set(row.stageId, list);
}

export function getProductionFlowDemoRows(stageId: string): StageTableRow[] {
  return BY_STAGE.get(stageId) ?? [];
}

export function getProductionFlowDemoStats() {
  const poNos = new Set<string>();
  const itemKeys = new Set<string>();
  for (const row of FLOW_DEMO_ROWS) {
    const po = row.values.poNo;
    if (po) poNos.add(po);
    if (row.values.itemName) itemKeys.add(row.key);
  }
  const perStage: Record<string, number> = {};
  for (const [id, rows] of BY_STAGE) perStage[id] = rows.length;
  return {
    poCount: poNos.size,
    itemRowCount: itemKeys.size,
    totalRows: FLOW_DEMO_ROWS.length,
    perStage,
  };
}
