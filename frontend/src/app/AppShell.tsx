import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import {
  getInboxPoSource,
  resolveInboxPoSourceMode,
  resolveShellDataMode,
  shellModeFooterHint,
} from "../demo/DemoPoSource";
import { ApiSimulationBanner } from "../sim/ApiSimulationBanner";
import { Header } from "./Header";
import { navByPath } from "./nav";
import { Sidebar } from "./Sidebar";
import "../styles/shell.css";

export function AppShell() {
  const location = useLocation();
  const current = navByPath(location.pathname);
  const sourceMode = resolveInboxPoSourceMode(
    import.meta.env.VITE_INBOX_PO_SOURCE,
  );
  const dataMode = resolveShellDataMode(location.pathname, sourceMode);
  const [inboxBadge, setInboxBadge] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const m = await getInboxPoSource().metrics();
        if (!cancelled) setInboxBadge(Number(m.total) || 0);
      } catch {
        if (!cancelled) setInboxBadge(0);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [location.pathname, sourceMode]);

  return (
    <div className="ep-app">
      <Sidebar inboxBadge={inboxBadge} dataMode={dataMode} />
      <div className="ep-main">
        <Header current={current} dataMode={dataMode} />
        <main className="ep-container">
          <ApiSimulationBanner />
          <Outlet />
          <p
            className="ep-footerhint"
            data-testid="shell-footer"
            data-mode={dataMode}
          >
            {shellModeFooterHint(dataMode)}
          </p>
        </main>
      </div>
    </div>
  );
}
