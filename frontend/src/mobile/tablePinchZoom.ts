/**
 * Pure helpers untuk pinch/Ctrl-wheel zoom tabel.
 * Transform scale + sizer — bukan browser page zoom.
 */

export const TABLE_ZOOM_MIN = 0.7;
export const TABLE_ZOOM_MAX = 1.85;
export const TABLE_ZOOM_DEFAULT = 1;
export const TABLE_ZOOM_STEP = 0.08;

/** Suppress row-activate singkat setelah pinch agar tidak klik baris. */
let suppressRowActivateUntil = 0;

export function markPinchGesture(ms = 420): void {
  suppressRowActivateUntil = Date.now() + ms;
}

export function shouldSuppressRowActivate(now = Date.now()): boolean {
  return now < suppressRowActivateUntil;
}

/** Test helper — reset suppress window. */
export function resetPinchSuppressForTests(): void {
  suppressRowActivateUntil = 0;
}

export function clampTableZoom(z: number): number {
  if (!Number.isFinite(z)) return TABLE_ZOOM_DEFAULT;
  return Math.min(TABLE_ZOOM_MAX, Math.max(TABLE_ZOOM_MIN, z));
}

export function nextZoomFromWheel(
  current: number,
  deltaY: number,
  ctrlOrMeta: boolean,
): number | null {
  if (!ctrlOrMeta) return null;
  const direction = deltaY > 0 ? -1 : 1;
  return clampTableZoom(current + direction * TABLE_ZOOM_STEP);
}

export function zoomFromPinchScale(
  startZoom: number,
  scale: number,
): number {
  return clampTableZoom(startZoom * scale);
}

/**
 * Pertahankan titik fokus viewport saat zoom berubah
 * (scrollLeft/Top relatif ke titik di konten).
 */
export function scrollAfterZoom(opts: {
  scrollLeft: number;
  scrollTop: number;
  focalX: number;
  focalY: number;
  prevZoom: number;
  nextZoom: number;
}): { scrollLeft: number; scrollTop: number } {
  const { scrollLeft, scrollTop, focalX, focalY, prevZoom, nextZoom } = opts;
  if (prevZoom <= 0 || nextZoom <= 0 || prevZoom === nextZoom) {
    return { scrollLeft, scrollTop };
  }
  const contentX = (scrollLeft + focalX) / prevZoom;
  const contentY = (scrollTop + focalY) / prevZoom;
  return {
    scrollLeft: Math.max(0, contentX * nextZoom - focalX),
    scrollTop: Math.max(0, contentY * nextZoom - focalY),
  };
}
