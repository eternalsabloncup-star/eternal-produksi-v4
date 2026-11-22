/**
 * Helper canvas tanda tangan pengiriman — mapping pointer ↔ bitmap
 * aman saat CSS width ≠ canvas.width (viewport sempit).
 */

export const SIGNATURE_CANVAS_HEIGHT = 140;

/** Koordinat bitmap dari pointer client — skala ke ukuran internal canvas. */
export function canvasPointerPos(
  canvas: HTMLCanvasElement,
  clientX: number,
  clientY: number,
): { x: number; y: number } {
  const rect = canvas.getBoundingClientRect();
  const rw = rect.width || 1;
  const rh = rect.height || 1;
  return {
    x: ((clientX - rect.left) / rw) * canvas.width,
    y: ((clientY - rect.top) / rh) * canvas.height,
  };
}

/**
 * Samakan lebar bitmap canvas dengan lebar wrapper (CSS px).
 * Mengembalikan true jika ukuran berubah (bitmap di-reset browser).
 */
export function syncSignatureCanvasSize(
  canvas: HTMLCanvasElement,
  wrapWidthPx: number,
  heightPx: number = SIGNATURE_CANVAS_HEIGHT,
): boolean {
  const w = Math.max(1, Math.floor(wrapWidthPx));
  const h = Math.max(1, Math.floor(heightPx));
  if (canvas.width === w && canvas.height === h) return false;
  canvas.width = w;
  canvas.height = h;
  return true;
}
