import { NavLink } from "react-router-dom";
import { APP_NAV } from "./nav";

type Props = {
  collapsed: boolean;
  onToggle: () => void;
  inboxBadge?: number;
};

export function Sidebar({ collapsed, onToggle, inboxBadge }: Props) {
  return (
    <aside className={`ep-sidebar${collapsed ? " compact" : ""}`} id="sidebar">
      <button
        type="button"
        className="ep-sidebar-edge"
        onClick={onToggle}
        title={collapsed ? "Perluas sidebar" : "Ciutkan sidebar"}
        aria-label={collapsed ? "Perluas sidebar" : "Ciutkan sidebar"}
      >
        {collapsed ? "❯" : "❮"}
      </button>
      <div className="ep-brand">
        <span className="ep-brandmark">E</span>
        <div className="ep-brandtext">
          <h1>
            ETERNAL <span style={{ fontWeight: 400, color: "#b9d1ff" }}>V4</span>
          </h1>
          <small>PRODUCTION OPERATIONS</small>
        </div>
      </div>
      <div className="ep-navtitle">Workspace</div>
      <nav className="ep-nav" aria-label="Navigasi utama">
        {APP_NAV.map((item) => (
          <NavLink
            key={item.id}
            to={item.path}
            className={({ isActive }) => (isActive ? "active" : undefined)}
            title={item.label}
          >
            <span className="ico">{item.icon}</span>
            <span className="ep-navlabel">{item.label}</span>
            {item.id === "inbox" && inboxBadge != null && inboxBadge > 0 ? (
              <span className="counter">{inboxBadge}</span>
            ) : null}
          </NavLink>
        ))}
      </nav>
      <div className="ep-sidefoot">
        <span className="ep-sidefoot-ico" style={{ fontSize: 17 }}>
          ⬡
        </span>
        <span>
          UI Shell Mode
          <br />
          <small style={{ color: "#7690ab" }}>Demo · tidak menulis MongoDB</small>
        </span>
      </div>
    </aside>
  );
}
