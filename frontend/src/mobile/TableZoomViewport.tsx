import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  markPinchGesture,
  nextZoomFromWheel,
  scrollAfterZoom,
  TABLE_ZOOM_DEFAULT,
  zoomFromPinchScale,
} from "./tablePinchZoom";

type Props = {
  children: ReactNode;
  /** Elemen scroll (biasanya .ep-tblscroll). */
  scrollParent: HTMLElement | null;
};

/**
 * Zoom transform pada grid tabel saja.
 * Chrome (nav/header/modal) di luar komponen ini tidak ikut scale.
 */
export function TableZoomViewport({ children, scrollParent }: Props) {
  const [zoom, setZoom] = useState(TABLE_ZOOM_DEFAULT);
  const innerRef = useRef<HTMLDivElement | null>(null);
  const sizerRef = useRef<HTMLDivElement | null>(null);
  const pinchRef = useRef<{
    startDist: number;
    startZoom: number;
  } | null>(null);
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;

  const applyZoom = useCallback(
    (next: number, focal?: { x: number; y: number }) => {
      const prev = zoomRef.current;
      if (next === prev) return;
      const parent = scrollParent;
      if (parent && focal) {
        const adj = scrollAfterZoom({
          scrollLeft: parent.scrollLeft,
          scrollTop: parent.scrollTop,
          focalX: focal.x,
          focalY: focal.y,
          prevZoom: prev,
          nextZoom: next,
        });
        setZoom(next);
        requestAnimationFrame(() => {
          // Clamp setelah sizer sync agar viewport tidak “kosong” pasca zoom-out.
          const maxL = Math.max(0, parent.scrollWidth - parent.clientWidth);
          const maxT = Math.max(0, parent.scrollHeight - parent.clientHeight);
          parent.scrollLeft = Math.min(Math.max(0, adj.scrollLeft), maxL);
          parent.scrollTop = Math.min(Math.max(0, adj.scrollTop), maxT);
        });
      } else {
        setZoom(next);
      }
    },
    [scrollParent],
  );

  const syncSizer = useCallback(() => {
    const inner = innerRef.current;
    const sizer = sizerRef.current;
    if (!inner || !sizer) return;
    const w = inner.scrollWidth;
    const h = inner.scrollHeight;
    sizer.style.width = `${Math.ceil(w * zoom)}px`;
    sizer.style.height = `${Math.ceil(h * zoom)}px`;
  }, [zoom]);

  useLayoutEffect(() => {
    syncSizer();
  }, [syncSizer, children]);

  useEffect(() => {
    const inner = innerRef.current;
    if (!inner || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => syncSizer());
    ro.observe(inner);
    return () => ro.disconnect();
  }, [syncSizer]);

  useEffect(() => {
    const parent = scrollParent;
    if (!parent) return;

    function onWheel(e: WheelEvent) {
      const next = nextZoomFromWheel(zoomRef.current, e.deltaY, e.ctrlKey || e.metaKey);
      if (next == null) return;
      e.preventDefault();
      const rect = parent!.getBoundingClientRect();
      applyZoom(next, {
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
      });
    }

    function distance(a: Touch, b: Touch): number {
      const dx = a.clientX - b.clientX;
      const dy = a.clientY - b.clientY;
      return Math.hypot(dx, dy);
    }

    function onTouchStart(e: TouchEvent) {
      if (e.touches.length === 2) {
        pinchRef.current = {
          startDist: distance(e.touches[0], e.touches[1]),
          startZoom: zoomRef.current,
        };
        markPinchGesture();
      }
    }

    function onTouchMove(e: TouchEvent) {
      const pinch = pinchRef.current;
      if (!pinch || e.touches.length !== 2) return;
      e.preventDefault();
      markPinchGesture();
      const dist = distance(e.touches[0], e.touches[1]);
      if (pinch.startDist <= 0) return;
      const scale = dist / pinch.startDist;
      const next = zoomFromPinchScale(pinch.startZoom, scale);
      const rect = parent!.getBoundingClientRect();
      const midX =
        (e.touches[0].clientX + e.touches[1].clientX) / 2 - rect.left;
      const midY =
        (e.touches[0].clientY + e.touches[1].clientY) / 2 - rect.top;
      applyZoom(next, { x: midX, y: midY });
    }

    function onTouchEnd(e: TouchEvent) {
      if (e.touches.length < 2) {
        if (pinchRef.current) markPinchGesture();
        pinchRef.current = null;
      }
    }

    parent.addEventListener("wheel", onWheel, { passive: false });
    parent.addEventListener("touchstart", onTouchStart, { passive: true });
    parent.addEventListener("touchmove", onTouchMove, { passive: false });
    parent.addEventListener("touchend", onTouchEnd);
    parent.addEventListener("touchcancel", onTouchEnd);
    return () => {
      parent.removeEventListener("wheel", onWheel);
      parent.removeEventListener("touchstart", onTouchStart);
      parent.removeEventListener("touchmove", onTouchMove);
      parent.removeEventListener("touchend", onTouchEnd);
      parent.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [scrollParent, applyZoom]);

  return (
    <div
      className="ep-m-zoom-sizer"
      ref={sizerRef}
      data-testid="table-zoom-sizer"
      data-zoom={zoom.toFixed(3)}
    >
      <div
        className="ep-m-zoom-inner"
        ref={innerRef}
        style={{
          transform: `scale(${zoom})`,
          transformOrigin: "0 0",
        }}
        data-testid="table-zoom-inner"
      >
        {children}
      </div>
    </div>
  );
}
