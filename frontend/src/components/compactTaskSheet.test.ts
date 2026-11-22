import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  CollapsibleTaskSection,
  WorkSheetKvGroup,
  WorkSheetSegment,
  WorkSheetStackRow,
} from "./compactTaskSheet";

describe("CollapsibleTaskSection", () => {
  it("renders collapsed by default without body", () => {
    const html = renderToStaticMarkup(
      createElement(CollapsibleTaskSection, {
        title: "Riwayat",
        testId: "hist",
        children: createElement("p", null, "secret"),
      }),
    );
    expect(html).toContain("Riwayat");
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain("secret");
  });

  it("renders body when defaultOpen", () => {
    const html = renderToStaticMarkup(
      createElement(CollapsibleTaskSection, {
        title: "Riwayat",
        defaultOpen: true,
        children: createElement("p", null, "visible-history"),
      }),
    );
    expect(html).toContain("visible-history");
    expect(html).toContain('aria-expanded="true"');
  });
});

describe("WorkSheetSegment", () => {
  it("marks active option", () => {
    const html = renderToStaticMarkup(
      createElement(WorkSheetSegment, {
        value: "gagal",
        onChange: () => undefined,
        options: [
          { id: "berhasil", label: "Berhasil" },
          { id: "gagal", label: "Gagal" },
        ],
      }),
    );
    expect(html).toContain("is-active");
    expect(html).toContain('aria-selected="true"');
  });
});

describe("WorkSheetStackRow", () => {
  it("renders label, colon, and value columns", () => {
    const html = renderToStaticMarkup(
      createElement(WorkSheetStackRow, {
        label: "Qty SJ",
        value: "2.000",
      }),
    );
    expect(html).toContain("Qty SJ");
    expect(html).toContain("ep-ws-stack-colon");
    expect(html).toContain("2.000");
  });
});

describe("WorkSheetKvGroup", () => {
  it("wraps stack rows for shared column tracks", () => {
    const html = renderToStaticMarkup(
      createElement(WorkSheetKvGroup, {
        children: createElement(WorkSheetStackRow, {
          label: "Nama Barang",
          value: "Cup",
        }),
      }),
    );
    expect(html).toContain("ep-ws-kv-group");
    expect(html).toContain("Nama Barang");
  });
});
