import { describe, expect, it } from "vitest";
import {
  SIGNATURE_CANVAS_HEIGHT,
  canvasPointerPos,
  syncSignatureCanvasSize,
} from "./signatureCanvas";

function fakeCanvas(attrs: {
  width: number;
  height: number;
  rectWidth: number;
  rectHeight: number;
  left?: number;
  top?: number;
}): HTMLCanvasElement {
  const left = attrs.left ?? 10;
  const top = attrs.top ?? 20;
  return {
    width: attrs.width,
    height: attrs.height,
    getBoundingClientRect: () =>
      ({
        left,
        top,
        width: attrs.rectWidth,
        height: attrs.rectHeight,
        right: left + attrs.rectWidth,
        bottom: top + attrs.rectHeight,
        x: left,
        y: top,
        toJSON: () => ({}),
      }) as DOMRect,
  } as HTMLCanvasElement;
}

describe("canvasPointerPos", () => {
  it("maps 1:1 when CSS size matches bitmap", () => {
    const c = fakeCanvas({
      width: 300,
      height: 140,
      rectWidth: 300,
      rectHeight: 140,
    });
    expect(canvasPointerPos(c, 10 + 150, 20 + 70)).toEqual({ x: 150, y: 70 });
  });

  it("scales when CSS is narrower than bitmap (overflow case)", () => {
    const c = fakeCanvas({
      width: 360,
      height: 140,
      rectWidth: 280,
      rectHeight: 140,
    });
    const p = canvasPointerPos(c, 10 + 140, 20 + 70);
    expect(p.x).toBeCloseTo((140 / 280) * 360, 5);
    expect(p.y).toBeCloseTo(70, 5);
  });

  it("scales when CSS matches responsive bitmap width", () => {
    const c = fakeCanvas({
      width: 288,
      height: SIGNATURE_CANVAS_HEIGHT,
      rectWidth: 288,
      rectHeight: SIGNATURE_CANVAS_HEIGHT,
    });
    expect(canvasPointerPos(c, 10 + 288, 20)).toEqual({
      x: 288,
      y: 0,
    });
  });
});

describe("syncSignatureCanvasSize", () => {
  it("resizes bitmap to wrap width and reports change", () => {
    const c = { width: 360, height: 140 } as HTMLCanvasElement;
    expect(syncSignatureCanvasSize(c, 288)).toBe(true);
    expect(c.width).toBe(288);
    expect(c.height).toBe(SIGNATURE_CANVAS_HEIGHT);
    expect(syncSignatureCanvasSize(c, 288)).toBe(false);
  });

  it("clamps to at least 1px", () => {
    const c = { width: 10, height: 10 } as HTMLCanvasElement;
    syncSignatureCanvasSize(c, 0.4, 0.2);
    expect(c.width).toBe(1);
    expect(c.height).toBe(1);
  });
});
