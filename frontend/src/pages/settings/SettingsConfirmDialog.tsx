import { useEffect, useId, useRef, type FormEvent, type ReactNode } from "react";

export type SettingsConfirmRequest = {
  title: string;
  message: string;
  /** Optional detail bullets */
  details?: string[];
  tone?: "default" | "danger";
  confirmLabel?: string;
  cancelLabel?: string;
  /** If set, user must type/fill reason before confirm */
  requireReason?: boolean;
  reasonLabel?: string;
  reasonPlaceholder?: string;
  /** Optional extra text field (e.g. kata sandi baru / kode peran) */
  extraField?: {
    label: string;
    placeholder?: string;
    required?: boolean;
    minLength?: number;
    inputType?: "text" | "password";
    defaultValue?: string;
  };
};

type Props = {
  open: boolean;
  request: SettingsConfirmRequest | null;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: (reason: string, extra: string) => void;
  children?: ReactNode;
};

/** Dialog konfirmasi Pengaturan — ganti window.confirm/prompt. */
export function SettingsConfirmDialog({
  open,
  request,
  busy,
  onCancel,
  onConfirm,
}: Props) {
  const titleId = useId();
  const reasonRef = useRef<HTMLInputElement>(null);
  const extraRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open || !request) return;
    const t = window.setTimeout(() => {
      if (request.extraField) extraRef.current?.focus();
      else if (request.requireReason) reasonRef.current?.focus();
      else confirmRef.current?.focus();
    }, 0);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, request, busy, onCancel]);

  if (!open || !request) return null;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const reason = reasonRef.current?.value?.trim() || "";
    const extra = extraRef.current?.value || "";
    if (request!.requireReason && !reason) return;
    if (request!.extraField?.required && !extra.trim()) return;
    if (
      request!.extraField?.minLength &&
      extra.trim().length < request!.extraField.minLength
    ) {
      return;
    }
    onConfirm(reason, extra.trim());
  }

  const danger = request.tone === "danger";

  return (
    <div
      className="ep-modalshade ep-modalshade-center"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <form
        className="ep-modal ep-settings-confirm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onSubmit={submit}
      >
        <header className="ep-modal-head">
          <div className="ep-modal-head-main">
            <h2 id={titleId} className="ep-settings-confirm-title">
              {request.title}
            </h2>
          </div>
          <button
            type="button"
            className="ep-x"
            aria-label="Tutup"
            disabled={busy}
            onClick={onCancel}
          >
            ×
          </button>
        </header>
        <div className="ep-modal-body">
          <p className="ep-settings-confirm-message">{request.message}</p>
          {request.details && request.details.length > 0 ? (
            <ul className="ep-settings-confirm-details">
              {request.details.map((d) => (
                <li key={d}>{d}</li>
              ))}
            </ul>
          ) : null}
          {request.extraField ? (
            <label className="ep-settings-field">
              <span className="ep-settings-field-label">{request.extraField.label}</span>
              <input
                ref={extraRef}
                type={request.extraField.inputType || "text"}
                required={request.extraField.required !== false}
                minLength={request.extraField.minLength}
                disabled={busy}
                defaultValue={request.extraField.defaultValue || ""}
                placeholder={request.extraField.placeholder}
                autoComplete={
                  request.extraField.inputType === "password" ? "new-password" : "off"
                }
              />
            </label>
          ) : null}
          {request.requireReason ? (
            <label className="ep-settings-field">
              <span className="ep-settings-field-label">
                {request.reasonLabel || "Alasan (wajib)"}
              </span>
              <input
                ref={reasonRef}
                required
                disabled={busy}
                placeholder={request.reasonPlaceholder || "Tuliskan alasan…"}
                aria-required="true"
              />
            </label>
          ) : null}
        </div>
        <footer className="ep-modal-foot">
          <div className="ep-modal-foot-actions">
            <button type="button" className="ep-btn" disabled={busy} onClick={onCancel}>
              {request.cancelLabel || "Batalkan"}
            </button>
            <button
              ref={confirmRef}
              type="submit"
              className={`ep-btn primary${danger ? " is-danger" : ""}${busy ? " is-busy" : ""}`}
              disabled={busy}
            >
              {busy ? "Memproses…" : request.confirmLabel || "Lanjutkan"}
            </button>
          </div>
        </footer>
      </form>
    </div>
  );
}
