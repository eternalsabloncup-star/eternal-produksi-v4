import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MobileFilePicker } from "./MobileFilePicker";

describe("MobileFilePicker", () => {
  it("keeps native file input with accessible label and trigger", () => {
    const html = renderToStaticMarkup(
      createElement(MobileFilePicker, {
        ariaLabel: "File sampel",
        accept: "image/png",
        testId: "sample-file-picker",
        buttonLabel: "Pilih File",
      }),
    );
    expect(html).toContain('type="file"');
    expect(html).toContain("File sampel");
    expect(html).toContain("Pilih File");
    expect(html).toContain("Belum ada file dipilih");
    expect(html).toContain("sample-file-picker");
  });
});
