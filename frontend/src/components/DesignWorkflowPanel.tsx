import { useCallback, useEffect, useRef, useState } from "react";
import {
  confirmDesignVersion,
  createDesignApprovalLink,
  customerApprovalPath,
  fetchDesignState,
  submitDesignFile,
  type DesignStateResponse,
  WorkflowApiError,
} from "../api/workflow";

type Props = {
  accuratePoId: string;
  poNo?: string;
  /** Dipanggil setelah mutasi sukses agar parent bisa refresh flow/tasks. */
  onChanged?: () => void;
};

function gateLabel(state: string | undefined): string {
  if (state === "open") return "Terbuka";
  if (state === "review") return "Perlu Review";
  if (state === "closed") return "Tertutup";
  return state || "—";
}

/** Panel Design API: upload → konfirmasi Designer → buat link customer. */
export function DesignWorkflowPanel({ accuratePoId, poNo, onChanged }: Props) {
  const [state, setState] = useState<DesignStateResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [approvalUrl, setApprovalUrl] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setState(await fetchDesignState(accuratePoId));
    } catch (err) {
      setState(null);
      setError(
        err instanceof WorkflowApiError
          ? err.message
          : err instanceof Error
            ? err.message
            : "Gagal memuat status desain.",
      );
    } finally {
      setLoading(false);
    }
  }, [accuratePoId]);

  useEffect(() => {
    void load();
    setApprovalUrl(null);
    setNotice(null);
  }, [load]);

  async function onUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setError("Pilih file desain terlebih dahulu.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await submitDesignFile(accuratePoId, file);
      setNotice(`Desain versi ${res.submission.version} diunggah.`);
      if (fileRef.current) fileRef.current.value = "";
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload desain gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirm() {
    const version = state?.latest?.version;
    if (!version) {
      setError("Belum ada file desain untuk dikonfirmasi.");
      return;
    }
    if (state?.latest?.confirmed_at) {
      setError("Versi terbaru sudah dikonfirmasi.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await confirmDesignVersion(accuratePoId, version);
      setNotice(`Desain versi ${version} dikonfirmasi Designer.`);
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Konfirmasi desain gagal.");
    } finally {
      setBusy(false);
    }
  }

  async function onCreateLink() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await createDesignApprovalLink(accuratePoId);
      const path = customerApprovalPath(res.token);
      setApprovalUrl(path);
      setNotice(
        `Link approval dibuat untuk desain v${res.link.design_version}. Salin URL sekarang — token hanya ditampilkan sekali.`,
      );
      await load();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal membuat link approval.");
    } finally {
      setBusy(false);
    }
  }

  async function copyLink() {
    if (!approvalUrl) return;
    const absolute =
      typeof window !== "undefined"
        ? `${window.location.origin}${approvalUrl}`
        : approvalUrl;
    try {
      await navigator.clipboard.writeText(absolute);
      setNotice("URL approval disalin ke clipboard.");
    } catch {
      setNotice(`Salin manual: ${absolute}`);
    }
  }

  const latest = state?.latest;
  const designGate = state?.gates?.design?.state;
  const canConfirm = Boolean(latest && !latest.confirmed_at);
  const canLink = Boolean(latest?.confirmed_at) && !state?.approval;

  return (
    <div className="ep-workflow-panel" data-testid="design-workflow-panel">
      <p className="ep-sectionlabel">
        Design · {poNo || accuratePoId}
      </p>

      {loading ? <p>Memuat status desain…</p> : null}
      {error ? (
        <p className="ep-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? <p className="ep-notice">{notice}</p> : null}

      {!loading && state ? (
        <>
          <dl className="ep-kv">
            <dt>Gate Design</dt>
            <dd>{gateLabel(designGate)}</dd>
            <dt>Versi terbaru</dt>
            <dd>
              {latest
                ? `v${latest.version} · ${latest.filename || "file"}${
                    latest.confirmed_at ? " · dikonfirmasi" : " · menunggu konfirmasi"
                  }`
                : "Belum ada submission"}
            </dd>
            <dt>Approval customer</dt>
            <dd>
              {state.approval
                ? `Disetujui ${state.approval.signer_name || ""} (${state.approval.approved_at || ""})`
                : "Belum ada"}
            </dd>
          </dl>

          {state.submissions.length > 0 ? (
            <div className="ep-tblscroll">
              <table className="ep-table">
                <thead>
                  <tr>
                    <th>Versi</th>
                    <th>File</th>
                    <th>Diunggah</th>
                    <th>Dikonfirmasi</th>
                  </tr>
                </thead>
                <tbody>
                  {state.submissions.map((s) => (
                    <tr key={s.version}>
                      <td>{s.version}</td>
                      <td>{s.filename || "—"}</td>
                      <td>{s.submitted_by || "—"}</td>
                      <td>{s.confirmed_at ? s.confirmed_by || "ya" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="ep-empty">Belum ada file desain untuk PO ini.</p>
          )}

          <div className="ep-filterbar">
            <input
              ref={fileRef}
              type="file"
              aria-label="File desain"
              accept="application/pdf,image/png,image/jpeg,image/webp"
            />
            <button
              type="button"
              className="ep-btn primary"
              disabled={busy}
              onClick={() => void onUpload()}
            >
              {busy ? "Mengunggah…" : "Unggah Desain"}
            </button>
            <button
              type="button"
              className="ep-btn"
              disabled={busy || !canConfirm}
              title={
                canConfirm
                  ? "Konfirmasi versi terbaru"
                  : "Unggah file baru atau versi sudah dikonfirmasi"
              }
              onClick={() => void onConfirm()}
            >
              Konfirmasi Designer
            </button>
            <button
              type="button"
              className="ep-btn"
              disabled={busy || !canLink}
              title={
                canLink
                  ? "Buat link approval customer"
                  : "Perlu konfirmasi Designer dan belum ada approval"
              }
              onClick={() => void onCreateLink()}
            >
              Buat Link Approval
            </button>
            <button type="button" className="ep-btn" disabled={busy} onClick={() => void load()}>
              Muat ulang
            </button>
          </div>

          {approvalUrl ? (
            <div className="ep-filterbar" data-testid="design-approval-url">
              <code>{approvalUrl}</code>
              <button type="button" className="ep-btn primary" onClick={() => void copyLink()}>
                Salin URL
              </button>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
