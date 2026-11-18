import { NavLink } from "react-router-dom";
import type { ShellDataMode } from "../demo/resolveInboxMode";
import { shellSidefootCopy } from "../demo/resolveInboxMode";
import { NavIcon } from "./icons";
import { APP_NAV } from "./nav";

type Props = {
  inboxBadge?: number;
  dataMode: ShellDataMode;
};

/** Sidebar rail hijau — menu Tahap 01 saja (tanpa flyout / modul Tahap 02). */
export function Sidebar({ inboxBadge, dataMode }: Props) {
  const foot = shellSidefootCopy(dataMode);
  return (
    <aside className="ep-sidebar ep-sidebar-rail" id="sidebar">
      <div className="ep-brand" title="Eternal Production V4">
        <span className="ep-brandmark">E</span>
      </div>
      <nav className="ep-nav" aria-label="Navigasi utama">
        {APP_NAV.map((item) => (
          <NavLink
            key={item.id}
            to={item.path}
            className={({ isActive }) => (isActive ? "active" : undefined)}
            title={item.label}
            aria-label={item.label}
          >
            <span
              className="ep-nav-ibox"
              style={{ background: item.box, color: item.fg }}
              aria-hidden
            >
              <NavIcon name={item.icon} size={20} />
            </span>
            {item.id === "inbox" && inboxBadge != null && inboxBadge > 0 ? (
              <span
                className={`ep-nav-badge${inboxBadge < 100 ? " is-round" : " is-pill"}`}
                aria-label={`${inboxBadge} PO masuk`}
              >
                {inboxBadge}
              </span>
            ) : null}
          </NavLink>
        ))}
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
    </aside>
  );
}
