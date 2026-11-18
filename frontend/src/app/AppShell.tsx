import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import {
  getInboxPoSource,
  resolveInboxPoSourceMode,
} from "../demo/DemoPoSource";
import { Header } from "./Header";
import { navByPath } from "./nav";
import { Sidebar } from "./Sidebar";
import "../styles/shell.css";

const COLLAPSE_KEY = "epv4.sidebar.collapsed";

export function AppShell() {
  const location = useLocation();
  const current = navByPath(location.pathname);
  const sourceMode = resolveInboxPoSourceMode(
    import.meta.env.VITE_INBOX_PO_SOURCE,
  );
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [inboxBadge, setInboxBadge] = useState(0);

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    } catch {
      // ignore
    }
  }, [collapsed]);

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
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((v) => !v)}
        inboxBadge={inboxBadge}
      />
      <div className="ep-main">
        <Header current={current} />
        <main className="ep-container">
          <Outlet />
          <p className="ep-footerhint">
            {sourceMode === "api"
              ? "Eternal Production V4 · Inbox Accurate read-only dari Mongo V4 (bukan DEMO)."
              : "Eternal Production V4 · UI shell · Data bertanda DEMO bersifat simulasi browser dan tidak menulis MongoDB."}
          </p>
        </main>
      </div>
    </div>
  );
}
