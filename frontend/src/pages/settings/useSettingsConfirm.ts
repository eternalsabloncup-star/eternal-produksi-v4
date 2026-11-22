import { useCallback, useRef, useState } from "react";
import type { SettingsConfirmRequest } from "./SettingsConfirmDialog";

export type SettingsConfirmResult = { ok: boolean; reason: string; extra: string };

/**
 * Promise-based confirm for Settings — mengganti window.confirm/prompt.
 */
export function useSettingsConfirm() {
  const [request, setRequest] = useState<SettingsConfirmRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const resolverRef = useRef<((r: SettingsConfirmResult) => void) | null>(null);

  const close = useCallback((result: SettingsConfirmResult) => {
    const resolve = resolverRef.current;
    resolverRef.current = null;
    setRequest(null);
    setBusy(false);
    resolve?.(result);
  }, []);

  const ask = useCallback((req: SettingsConfirmRequest) => {
    return new Promise<SettingsConfirmResult>((resolve) => {
      resolverRef.current = resolve;
      setRequest(req);
    });
  }, []);

  const onCancel = useCallback(() => {
    if (busy) return;
    close({ ok: false, reason: "", extra: "" });
  }, [busy, close]);

  const onConfirm = useCallback(
    (reason: string, extra: string) => {
      close({ ok: true, reason, extra });
    },
    [close],
  );

  return {
    open: Boolean(request),
    request,
    busy,
    setBusy,
    ask,
    onCancel,
    onConfirm,
  };
}
