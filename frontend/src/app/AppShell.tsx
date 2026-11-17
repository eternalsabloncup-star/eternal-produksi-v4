import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { getDemoPoSource } from "../demo/DemoPoSource";
import { Header } from "./Header";
import { navByPath } from "./nav";
import { Sidebar } from "./Sidebar";
import "../styles/shell.css";

const COLLAPSE_KEY = "epv4.sidebar.collapsed";

export function AppShell() {
  const location = useLocation();
  const current = navByPath(location.pathname);
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
      const m = await getDemoPoSource().metrics();
      if (!cancelled) setInboxBadge(m.unsched ?? 0);
    })();
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

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
            Eternal Production V4 · UI shell · Data bertanda DEMO bersifat simulasi
            browser dan tidak menulis MongoDB.
          </p>
        </main>
      </div>
    </div>
  );
}
