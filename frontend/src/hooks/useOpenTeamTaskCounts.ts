import { useCallback, useEffect, useMemo, useState } from "react";
import type { TeamTaskRoleId } from "../app/nav";
import {
  emptyOpenTaskCounts,
  fetchOpenTeamTaskCountsFromApi,
  type OpenTaskCountsStatus,
} from "../api/openTaskCounts";
import { subscribeProductionAlloc } from "../demo/productionHeadAlloc";
import { getOpenTeamTaskCounts } from "../demo/teamTasks";
import { subscribeTeamTaskWork } from "../demo/teamTaskWork";
import { useProductionApiPanels } from "../sim/previewMode";

export const TEAM_TASKS_CHANGED_EVENT = "eternal:tasks-changed";

export type UseOpenTeamTaskCountsResult = {
  counts: Record<TeamTaskRoleId, number>;
  status: OpenTaskCountsStatus;
  error?: string;
  refresh: () => void;
  /** true bila angka dari task engine Mongo / simulasi API, bukan fixture DEMO. */
  apiMode: boolean;
};

export function notifyTeamTasksChanged(): void {
  window.dispatchEvent(new CustomEvent(TEAM_TASKS_CHANGED_EVENT));
}

/**
 * Badge Tugas Tim: sumber sama dengan panel (`useProductionApiPanels`).
 * API atau simulasi DEV → task engine per-role done=false; DEMO → fixture lokal.
 */
export function useOpenTeamTaskCounts(): UseOpenTeamTaskCountsResult {
  const apiMode = useProductionApiPanels();

  const [demoTick, setDemoTick] = useState(0);
  const [apiCounts, setApiCounts] = useState(emptyOpenTaskCounts);
  const [status, setStatus] = useState<OpenTaskCountsStatus>(
    apiMode ? "loading" : "ready",
  );
  const [error, setError] = useState<string | undefined>();
  const [reloadKey, setReloadKey] = useState(0);

  const refresh = useCallback(() => {
    setReloadKey((k) => k + 1);
  }, []);

  useEffect(() => {
    if (apiMode) return;
    const unsubAlloc = subscribeProductionAlloc(() => setDemoTick((t) => t + 1));
    const unsubWork = subscribeTeamTaskWork(() => setDemoTick((t) => t + 1));
    return () => {
      unsubAlloc();
      unsubWork();
    };
  }, [apiMode]);

  useEffect(() => {
    if (!apiMode) {
      setStatus("ready");
      setError(undefined);
      return;
    }
    let cancelled = false;
    setStatus("loading");
    setError(undefined);
    void (async () => {
      const result = await fetchOpenTeamTaskCountsFromApi();
      if (cancelled) return;
      setApiCounts(result.counts);
      setStatus(result.status);
      setError(result.error);
    })();
    return () => {
      cancelled = true;
    };
  }, [apiMode, reloadKey]);

  useEffect(() => {
    if (!apiMode) return;
    function onInvalidate() {
      setReloadKey((k) => k + 1);
    }
    window.addEventListener("eternal:po-accepted", onInvalidate);
    window.addEventListener(TEAM_TASKS_CHANGED_EVENT, onInvalidate);
    return () => {
      window.removeEventListener("eternal:po-accepted", onInvalidate);
      window.removeEventListener(TEAM_TASKS_CHANGED_EVENT, onInvalidate);
    };
  }, [apiMode]);

  const demoCounts = useMemo(() => {
    void demoTick;
    return getOpenTeamTaskCounts();
  }, [demoTick]);

  if (!apiMode) {
    return {
      counts: demoCounts,
      status: "ready",
      refresh,
      apiMode: false,
    };
  }

  return {
    counts: apiCounts,
    status,
    error,
    refresh,
    apiMode: true,
  };
}
