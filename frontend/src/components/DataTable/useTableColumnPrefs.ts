import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../../auth";
import {
  deleteTablePreferences,
  fetchTablePreferences,
  saveTablePreferences,
} from "./tablePrefsApi";
import type { TableColumnPrefs } from "./types";

const MIN_W = 72;
const MAX_W = 800;
const SAVE_DEBOUNCE_MS = 450;

function mergeOrder(saved: string[] | undefined, columnIds: string[]): string[] {
  const known = new Set(columnIds);
  const next = (saved || []).filter((id) => known.has(id));
  for (const id of columnIds) {
    if (!next.includes(id)) next.push(id);
  }
  return next;
}

function clampWidths(
  widths: Record<string, number> | undefined,
  columnIds: string[],
): Record<string, number> {
  const known = new Set(columnIds);
  const out: Record<string, number> = {};
  if (!widths) return out;
  for (const [id, w] of Object.entries(widths)) {
    if (!known.has(id)) continue;
    const n = Number(w);
    if (!Number.isFinite(n)) continue;
    out[id] = Math.min(MAX_W, Math.max(MIN_W, Math.round(n)));
  }
  return out;
}

/**
 * Preferensi tampilan kolom per pengguna via API MongoDB (session auth).
 * Fallback lokal hanya untuk tampilan sebelum load selesai — tidak dianggap tersimpan.
 */
export function useTableColumnPrefs(
  tableId: string,
  columnIds: string[],
  defaultHidden: string[] = [],
): {
  order: string[];
  widths: Record<string, number>;
  hidden: Set<string>;
  loading: boolean;
  saveError: string | null;
  saveState: "idle" | "saving" | "saved" | "error";
  setOrder: (order: string[]) => void;
  setWidth: (columnId: string, width: number) => void;
  setHidden: (hidden: Set<string>) => void;
  resetPrefs: () => Promise<void>;
} {
  const { user } = useAuth();
  const columnKey = columnIds.join("|");
  const [order, setOrderState] = useState<string[]>(columnIds);
  const [widths, setWidths] = useState<Record<string, number>>({});
  const [hidden, setHiddenState] = useState<Set<string>>(() => new Set());
  const [loading, setLoading] = useState(true);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");

  const latestRef = useRef<TableColumnPrefs>({
    version: 1,
    order: columnIds,
    widths: {},
    hidden: defaultHidden,
  });
  const timerRef = useRef<number | null>(null);
  const userId = user?.id;

  const applyLocal = useCallback(
    (prefs: Partial<TableColumnPrefs>) => {
      const nextOrder = mergeOrder(prefs.order, columnIds);
      const nextWidths = clampWidths(prefs.widths, columnIds);
      const known = new Set(columnIds);
      const nextHidden = (prefs.hidden || []).filter((id) => known.has(id));
      setOrderState(nextOrder);
      setWidths(nextWidths);
      setHiddenState(new Set(nextHidden));
      latestRef.current = {
        version: prefs.version ?? 1,
        order: nextOrder,
        widths: nextWidths,
        hidden: nextHidden,
      };
    },
    [columnIds],
  );

  // Load when user or table changes
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSaveError(null);
    setSaveState("idle");
    applyLocal({
      order: columnIds,
      widths: {},
      hidden: defaultHidden,
      version: 1,
    });

    if (!userId) {
      setLoading(false);
      return;
    }

    (async () => {
      try {
        const dto = await fetchTablePreferences(tableId);
        if (cancelled) return;
        const hasSavedHidden = (dto.hidden_columns || []).length > 0;
        applyLocal({
          version: dto.version,
          order: dto.column_order,
          widths: dto.column_widths,
          hidden: hasSavedHidden ? dto.hidden_columns : defaultHidden,
        });
      } catch (err) {
        if (cancelled) return;
        setSaveError(
          err instanceof Error
            ? err.message
            : "Gagal memuat preferensi tabel.",
        );
        setSaveState("error");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
      if (timerRef.current != null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, tableId, columnKey]);

  const scheduleSave = useCallback(() => {
    if (!userId) return;
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(async () => {
      timerRef.current = null;
      const snap = latestRef.current;
      setSaveState("saving");
      setSaveError(null);
      try {
        await saveTablePreferences(tableId, {
          version: snap.version,
          column_order: snap.order,
          column_widths: snap.widths,
          hidden_columns: snap.hidden,
        });
        setSaveState("saved");
      } catch (err) {
        setSaveState("error");
        setSaveError(
          err instanceof Error
            ? err.message
            : "Gagal menyimpan preferensi tabel.",
        );
      }
    }, SAVE_DEBOUNCE_MS);
  }, [tableId, userId]);

  const setOrder = useCallback(
    (next: string[]) => {
      setOrderState(next);
      latestRef.current = { ...latestRef.current, order: next };
      scheduleSave();
    },
    [scheduleSave],
  );

  const setWidth = useCallback(
    (columnId: string, width: number) => {
      const clamped = Math.min(MAX_W, Math.max(MIN_W, Math.round(width)));
      setWidths((prev) => {
        const next = { ...prev, [columnId]: clamped };
        latestRef.current = { ...latestRef.current, widths: next };
        return next;
      });
      scheduleSave();
    },
    [scheduleSave],
  );

  const setHidden = useCallback(
    (nextSet: Set<string>) => {
      const next = Array.from(nextSet);
      setHiddenState(new Set(next));
      latestRef.current = { ...latestRef.current, hidden: next };
      scheduleSave();
    },
    [scheduleSave],
  );

  const resetPrefs = useCallback(async () => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    applyLocal({
      order: columnIds,
      widths: {},
      hidden: defaultHidden,
      version: 1,
    });
    if (!userId) return;
    setSaveState("saving");
    setSaveError(null);
    try {
      await deleteTablePreferences(tableId);
      setSaveState("saved");
    } catch (err) {
      setSaveState("error");
      setSaveError(
        err instanceof Error ? err.message : "Gagal mereset preferensi tabel.",
      );
    }
  }, [applyLocal, columnIds, defaultHidden, tableId, userId]);

  return {
    order,
    widths,
    hidden,
    loading,
    saveError,
    saveState,
    setOrder,
    setWidth,
    setHidden,
    resetPrefs,
  };
}
