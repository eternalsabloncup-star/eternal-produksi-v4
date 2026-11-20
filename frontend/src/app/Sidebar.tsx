import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import { NavLink, useLocation } from "react-router-dom";
import { formatOpenTaskBadge } from "../api/openTaskCounts";
import { useOpenTeamTaskCounts } from "../hooks/useOpenTeamTaskCounts";
import type { ShellDataMode } from "../demo/resolveInboxMode";
import { shellSidefootCopy } from "../demo/resolveInboxMode";
import { NavIcon } from "./icons";
import {
  APP_NAV,
  TEAM_TASKS_SUMMARY,
  TEAM_TASK_ROLES,
  isTasksSectionPath,
} from "./nav";

type Props = {
  inboxBadge?: number;
  dataMode: ShellDataMode;
};

export function Sidebar({ inboxBadge, dataMode }: Props) {
  const foot = shellSidefootCopy(dataMode);
  const location = useLocation();
  const tasksActive = isTasksSectionPath(location.pathname);
  const [flyoutOpen, setFlyoutOpen] = useState(false);
  const {
    counts: openTaskCounts,
    status: openTaskStatus,
    error: openTaskError,
  } = useOpenTeamTaskCounts();
  const [flyoutPos, setFlyoutPos] = useState<CSSProperties>({
    position: "fixed",
    top: 0,
    left: 0,
    visibility: "hidden",
  });

  const tasksBtnRef = useRef<HTMLButtonElement | null>(null);
  const flyoutRef = useRef<HTMLDivElement | null>(null);

  const closeFlyout = useCallback(() => setFlyoutOpen(false), []);

  const updateFlyoutPos = useCallback(() => {
    const btn = tasksBtnRef.current;
    const panel = flyoutRef.current;
    if (!btn || !panel) return;

    const rect = btn.getBoundingClientRect();
    const margin = 8;
    const gap = 8;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(236, vw - margin * 2);
    const measured = panel.offsetHeight || 280;
    const maxHeight = Math.max(160, vh - margin * 2);

    let left = rect.right + gap;
    if (left + width > vw - margin) {
      left = Math.max(margin, rect.left - gap - width);
    }

    let top = rect.top;
    const used = Math.min(measured, maxHeight);
    if (top + used > vh - margin) {
      top = Math.max(margin, vh - margin - used);
    }
    if (top < margin) top = margin;

    setFlyoutPos({
      position: "fixed",
      top,
      left,
      width,
      maxHeight,
      visibility: "visible",
      zIndex: 60,
    });
  }, []);

  useLayoutEffect(() => {
    if (!flyoutOpen) return;
    updateFlyoutPos();
    const raf = window.requestAnimationFrame(() => updateFlyoutPos());
    return () => window.cancelAnimationFrame(raf);
  }, [flyoutOpen, updateFlyoutPos]);

  useEffect(() => {
    if (!flyoutOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") closeFlyout();
    }
    function onPointer(e: MouseEvent) {
      const t = e.target as Node;
      if (flyoutRef.current?.contains(t) || tasksBtnRef.current?.contains(t)) {
        return;
      }
      closeFlyout();
    }
    function onReposition() {
      updateFlyoutPos();
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    window.addEventListener("resize", onReposition);
    document.addEventListener("scroll", onReposition, true);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
      window.removeEventListener("resize", onReposition);
      document.removeEventListener("scroll", onReposition, true);
    };
  }, [flyoutOpen, closeFlyout, updateFlyoutPos]);

  useEffect(() => {
    if (!tasksActive) closeFlyout();
  }, [location.pathname, tasksActive, closeFlyout]);

  const flyout =
    flyoutOpen && typeof document !== "undefined"
      ? createPortal(
          <div
            ref={flyoutRef}
            className="ep-nav-flyout"
            style={flyoutPos}
            role="menu"
            aria-label="Submenu Tugas Tim"
          >
            <div className="ep-nav-flyout-title">Tugas Tim</div>
            <div className="ep-nav-flyout-scroll">
              <NavLink
                to={TEAM_TASKS_SUMMARY.path}
                end
                role="menuitem"
                className={({ isActive }) =>
                  isActive ? "ep-nav-flyout-item active" : "ep-nav-flyout-item"
                }
                onClick={closeFlyout}
                title={TEAM_TASKS_SUMMARY.label}
              >
                <span
                  className="ep-nav-ibox ep-nav-ibox-sm"
                  style={{
                    background: TEAM_TASKS_SUMMARY.box,
                    color: TEAM_TASKS_SUMMARY.color,
                  }}
                  aria-hidden
                >
                  <NavIcon name={TEAM_TASKS_SUMMARY.icon} size={14} />
                </span>
                <span>{TEAM_TASKS_SUMMARY.label}</span>
              </NavLink>
              {TEAM_TASK_ROLES.map((role) => {
                const openCount = openTaskCounts[role.id] ?? 0;
                const badge = formatOpenTaskBadge(openCount, openTaskStatus);
                const badgeLabel =
                  openTaskStatus === "error"
                    ? openTaskError || "Jumlah tugas tidak tersedia"
                    : openTaskStatus === "loading"
                      ? "Memuat jumlah tugas…"
                      : openTaskStatus === "truncated"
                        ? `Sekurang-kurangnya ${openCount} belum selesai (batas baca tercapai)`
                        : `${openCount} tugas aktif (Belum + Sebagian)`;
                return (
                  <NavLink
                    key={role.id}
                    to={role.path}
                    role="menuitem"
                    className={({ isActive }) =>
                      isActive
                        ? "ep-nav-flyout-item active"
                        : "ep-nav-flyout-item"
                    }
                    onClick={closeFlyout}
                    title={role.label}
                  >
                    <span
                      className="ep-nav-ibox ep-nav-ibox-sm"
                      style={{ background: role.box, color: role.color }}
                      aria-hidden
                    >
                      <NavIcon name={role.icon} size={14} />
                    </span>
                    <span className="ep-nav-flyout-label">{role.label}</span>
                    {badge ? (
                      <span
                        className={`ep-nav-flyout-badge${
                          badge.length <= 2 ? " is-round" : " is-pill"
                        }${openTaskStatus === "error" ? " is-alert" : ""}`}
                        aria-label={badgeLabel}
                        title={badgeLabel}
                        data-testid={`task-badge-${role.id}`}
                        data-status={openTaskStatus}
                      >
                        {badge}
                      </span>
                    ) : null}
                  </NavLink>
                );
              })}
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <aside className="ep-sidebar ep-sidebar-rail" id="sidebar">
      <div className="ep-brand" title="Eternal Production V4">
        <span className="ep-brandmark">E</span>
      </div>
      <nav className="ep-nav" aria-label="Navigasi utama">
        {APP_NAV.map((item) => {
          const iconBox = (
            <span
              className="ep-nav-ibox"
              style={{ background: item.box, color: item.fg }}
              aria-hidden
            >
              <NavIcon name={item.icon} size={20} />
            </span>
          );

          if (item.id === "tasks") {
            return (
              <button
                key={item.id}
                ref={tasksBtnRef}
                type="button"
                className={`ep-navitem${tasksActive ? " active" : ""}${flyoutOpen ? " flyout-open" : ""}`}
                title={item.label}
                aria-label={item.label}
                aria-expanded={flyoutOpen}
                aria-haspopup="menu"
                onClick={() => setFlyoutOpen((v) => !v)}
              >
                {iconBox}
              </button>
            );
          }

          return (
            <NavLink
              key={item.id}
              to={item.path}
              className={({ isActive }) => (isActive ? "active" : undefined)}
              title={item.label}
              aria-label={item.label}
              onClick={closeFlyout}
            >
              {iconBox}
              {item.id === "inbox" && inboxBadge != null && inboxBadge > 0 ? (
                <span
                  className={`ep-nav-badge${inboxBadge < 100 ? " is-round" : " is-pill"}`}
                  aria-label={`${inboxBadge} PO masuk`}
                >
                  {inboxBadge}
                </span>
              ) : null}
            </NavLink>
          );
        })}
      </nav>
      <div
        className="ep-sidefoot"
        title={`${foot.title} — ${foot.sub}`}
        data-testid="shell-sidefoot"
        data-mode={dataMode}
      >
        <span className="ep-sidefoot-ico" aria-hidden>
          ⬡
        </span>
      </div>
      {flyout}
    </aside>
  );
}
