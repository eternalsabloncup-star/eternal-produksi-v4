import { NavLink } from "react-router-dom";
import { MOBILE_OPS_TABS } from "./mobileNav";

type Props = {
  openTaskBadge?: string | null;
};

/** Navigasi bawah fixed — tidak ikut zoom tabel. */
export function MobileBottomNav({ openTaskBadge }: Props) {
  return (
    <nav className="ep-m-bottomnav" aria-label="Navigasi operasional mobile">
      {MOBILE_OPS_TABS.map((tab) => (
        <NavLink
          key={tab.id}
          to={tab.path}
          className={({ isActive }) =>
            `ep-m-tab${isActive ? " is-active" : ""}`
          }
          end={tab.id === "tasks"}
        >
          <span className="ep-m-tab-label">{tab.short}</span>
          <span className="ep-m-tab-full">{tab.label}</span>
          {tab.id === "tasks" && openTaskBadge ? (
            <span className="ep-m-tab-badge" aria-label={`${openTaskBadge} tugas terbuka`}>
              {openTaskBadge}
            </span>
          ) : null}
        </NavLink>
      ))}
    </nav>
  );
}
