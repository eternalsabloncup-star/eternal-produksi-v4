import { afterEach, describe, expect, it } from "vitest";
import {
  clampTableZoom,
  markPinchGesture,
  nextZoomFromWheel,
  resetPinchSuppressForTests,
  scrollAfterZoom,
  shouldSuppressRowActivate,
  TABLE_ZOOM_MAX,
  TABLE_ZOOM_MIN,
  zoomFromPinchScale,
} from "./tablePinchZoom";

afterEach(() => {
  resetPinchSuppressForTests();
});

describe("tablePinchZoom", () => {
  it("clamps zoom to safe bounds", () => {
    expect(clampTableZoom(0.1)).toBe(TABLE_ZOOM_MIN);
    expect(clampTableZoom(9)).toBe(TABLE_ZOOM_MAX);
    expect(clampTableZoom(1.2)).toBe(1.2);
  });

  it("applies pinch scale from start zoom", () => {
    expect(zoomFromPinchScale(1, 1.5)).toBe(1.5);
    expect(zoomFromPinchScale(1, 0.5)).toBe(TABLE_ZOOM_MIN);
  });

  it("supports ctrl/meta wheel zoom and ignores plain wheel", () => {
    expect(nextZoomFromWheel(1, -100, true)).toBeGreaterThan(1);
    expect(nextZoomFromWheel(1, 100, true)).toBeLessThan(1);
    expect(nextZoomFromWheel(1, -100, false)).toBeNull();
  });

  it("preserves focal point when adjusting scroll after zoom", () => {
    const next = scrollAfterZoom({
      scrollLeft: 100,
      scrollTop: 40,
      focalX: 50,
      focalY: 20,
      prevZoom: 1,
      nextZoom: 2,
    });
    expect(next.scrollLeft).toBe(250);
    expect(next.scrollTop).toBe(100);
  });

  it("suppresses row activate during/after pinch", () => {
    expect(shouldSuppressRowActivate()).toBe(false);
    markPinchGesture(500);
    expect(shouldSuppressRowActivate()).toBe(true);
  });
});
