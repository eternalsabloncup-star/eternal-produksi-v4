import { useCallback, useEffect, useState } from "react";
import {
  fetchProductionFlow,
  type FlowNode,
  type ProductionFlow,
} from "../api/analytics";
import { DesignWorkflowPanel } from "./DesignWorkflowPanel";
import { PaymentWorkflowPanel } from "./PaymentWorkflowPanel";

const DESIGN_NODE_IDS = new Set(["flow-design", "flow-approval-design"]);
const PAYMENT_NODE_IDS = new Set(["flow-payment", "flow-approval-payment"]);

function statusLabel(status: FlowNode["status"]): string {
  if (status === "selesai") return "Selesai";
  if (status === "berjalan") return "Berjalan";
  if (status === "tidak_berlaku") return "Tidak Berlaku";
  return "Menunggu";
}

/** Alur Produksi 15 node — status dari state backend, bukan posisi visual. */
export function ProductionFlowApiPanel() {
  const [flows, setFlows] = useState<ProductionFlow[]>([]);
  const [activePo, setActivePo] = useState<string | null>(null);
  const [activeNode, setActiveNode] = useState<FlowNode | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchProductionFlow();
      setFlows(res.items);
      setActivePo((prev) => prev ?? res.items[0]?.accurate_po_id ?? null);
    } catch (err) {
      setFlows([]);
      setError(err instanceof Error ? err.message : "Gagal memuat Alur Produksi.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const flow = flows.find((f) => f.accurate_po_id === activePo) || flows[0] || null;

  return (
    <div data-testid="production-flow-api-panel">
      <div className="ep-filterbar">
        <label htmlFor="flow-po">PO</label>
        <select
          id="flow-po"
          value={flow?.accurate_po_id || ""}
          onChange={(e) => {
            setActivePo(e.target.value);
            setActiveNode(null);
          }}
        >
          {flows.map((f) => (
            <option key={f.accurate_po_id} value={f.accurate_po_id}>
              {f.po_no}
            </option>
          ))}
        </select>
        <button type="button" className="ep-btn" onClick={() => void load()}>
          Muat ulang
        </button>
        {flow && !flow.has_print_items ? (
          <span className="ep-pill gray">PO seluruh item Non-Cetak</span>
        ) : null}
      </div>

      {error ? <p className="ep-error">{error}</p> : null}

      {loading ? (
        <p>Memuat Alur Produksi dari MongoDB V4…</p>
      ) : !flow ? (
        <p className="ep-empty">Belum ada PO Accurate di MongoDB V4.</p>
      ) : (
        <>
          <div className="ep-tblscroll">
            <table className="ep-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Tahap</th>
                  <th>Status</th>
                  <th>Keterangan</th>
                  <th>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {flow.nodes.map((node, index) => (
                  <tr
                    key={node.id}
                    className={node.status === "tidak_berlaku" ? "ep-row-muted" : undefined}
                    onClick={() => setActiveNode(node)}
                  >
                    <td>{index + 1}</td>
                    <td>{node.label}</td>
                    <td>{statusLabel(node.status)}</td>
                    <td>{node.detail}</td>
                    <td>
                      <button
                        type="button"
                        className="ep-btn"
                        onClick={(e) => {
                          e.stopPropagation();
                          setActiveNode(node);
                        }}
                      >
                        Rincian
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {activeNode ? (
            <div
              className="ep-modalshade ep-modalshade-center"
              onClick={(e) => {
                if (e.target === e.currentTarget) setActiveNode(null);
              }}
            >
              <div className="ep-modal" role="dialog" aria-modal="true">
                <header className="ep-modalhead">
                  <strong>
                    {activeNode.label} · {flow.po_no}
                  </strong>
                  <button
                    type="button"
                    className="ep-btn"
                    onClick={() => setActiveNode(null)}
                  >
                    Tutup
                  </button>
                </header>
                <div className="ep-modalbody">
                  <dl className="ep-kv">
                    <dt>Status</dt>
                    <dd>{statusLabel(activeNode.status)}</dd>
                    <dt>Keterangan</dt>
                    <dd>{activeNode.detail}</dd>
                    <dt>Sumber</dt>
                    <dd>State operasional MongoDB V4</dd>
                  </dl>
                  {DESIGN_NODE_IDS.has(activeNode.id) ? (
                    <DesignWorkflowPanel
                      accuratePoId={flow.accurate_po_id}
                      poNo={flow.po_no}
                      onChanged={() => void load()}
                    />
                  ) : null}
                  {PAYMENT_NODE_IDS.has(activeNode.id) ? (
                    <PaymentWorkflowPanel
                      accuratePoId={flow.accurate_po_id}
                      poNo={flow.po_no}
                      onChanged={() => void load()}
                    />
                  ) : null}
                </div>
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
