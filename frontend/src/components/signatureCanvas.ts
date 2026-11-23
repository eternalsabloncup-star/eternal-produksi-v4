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
 * Mengembalikan true jika ukuran berubah.
 * Preserve existing strokes when resizing (browser clears on width/height set).
 */
export function syncSignatureCanvasSize(
  canvas: HTMLCanvasElement,
  wrapWidthPx: number,
  heightPx: number = SIGNATURE_CANVAS_HEIGHT,
): boolean {
  const w = Math.max(1, Math.floor(wrapWidthPx));
  const h = Math.max(1, Math.floor(heightPx));
  if (canvas.width === w && canvas.height === h) return false;
  let snapshot: ImageData | null = null;
  try {
    const ctx = canvas.getContext("2d");
    if (ctx && canvas.width > 0 && canvas.height > 0) {
      snapshot = ctx.getImageData(0, 0, canvas.width, canvas.height);
    }
  } catch {
    snapshot = null;
  }
  const prevW = canvas.width;
  const prevH = canvas.height;
  canvas.width = w;
  canvas.height = h;
  if (snapshot) {
    try {
      const ctx = canvas.getContext("2d");
      if (ctx) {
        const tmp = document.createElement("canvas");
        tmp.width = prevW;
        tmp.height = prevH;
        const tctx = tmp.getContext("2d");
        if (tctx) {
          tctx.putImageData(snapshot, 0, 0);
          ctx.drawImage(tmp, 0, 0, prevW, prevH, 0, 0, w, h);
        }
      }
    } catch {
      /* leave blank if restore fails */
    }
  }
  return true;
}
