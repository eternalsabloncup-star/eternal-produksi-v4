import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export type KelolaMenuItem = {
  id: string;
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  /** Visual hint for destructive actions — still requires caller confirmations. */
  tone?: "default" | "danger";
};

type Props = {
  /** Accessible name for the trigger (include row context). */
  ariaLabel: string;
  items: KelolaMenuItem[];
};

type MenuPos = { top: number; left: number; minWidth: number };

/**
 * Compact row action menu — opens on click, never auto-runs an action.
 * Portal + fixed position so overflow:auto table wraps do not clip the menu.
 */
export function SettingsKelolaMenu({ ariaLabel, items }: Props) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<MenuPos | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const menuId = useId();

  function placeMenu() {
    const el = triggerRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const minWidth = Math.max(168, r.width);
    let left = r.right - minWidth;
    left = Math.max(8, Math.min(left, window.innerWidth - minWidth - 8));
    let top = r.bottom + 4;
    const estimatedH = 8 + items.length * 34;
    if (top + estimatedH > window.innerHeight - 8 && r.top > estimatedH + 8) {
      top = r.top - estimatedH - 4;
    }
    setPos({ top, left, minWidth });
  }

  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    placeMenu();
  }, [open, items.length]);

  useEffect(() => {
    if (!open) return;
    function onDocPointer(ev: MouseEvent) {
      const t = ev.target as Node;
      if (rootRef.current?.contains(t)) return;
      if (menuRef.current?.contains(t)) return;
      setOpen(false);
    }
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") setOpen(false);
    }
    function onReposition() {
      placeMenu();
    }
    document.addEventListener("mousedown", onDocPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onReposition);
    window.addEventListener("scroll", onReposition, true);
    return () => {
      document.removeEventListener("mousedown", onDocPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onReposition);
      window.removeEventListener("scroll", onReposition, true);
    };
  }, [open, items.length]);

  const menu =
    open && pos
      ? createPortal(
          <ul
            id={menuId}
            ref={menuRef}
            className="ep-settings-kelola-menu is-portal"
            role="menu"
            style={{ top: pos.top, left: pos.left, minWidth: pos.minWidth }}
          >
            {items.map((item) => (
              <li key={item.id} role="none">
                <button
                  type="button"
                  role="menuitem"
                  className={`ep-settings-kelola-item${item.tone === "danger" ? " is-danger" : ""}`}
                  disabled={item.disabled}
                  onClick={() => {
                    if (item.disabled) return;
                    setOpen(false);
                    item.onSelect();
                  }}
                >
                  {item.label}
                </button>
              </li>
            ))}
          </ul>,
          document.body,
        )
      : null;

  return (
    <div className={`ep-settings-kelola${open ? " is-open" : ""}`} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="ep-btn ep-settings-kelola-trigger"
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((v) => !v)}
      >
        Kelola
        <span className="ep-settings-kelola-caret" aria-hidden>
          ▾
        </span>
      </button>
      {menu}
    </div>
  );
}
