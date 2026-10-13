import { describe, expect, it } from "vitest";

import { referenceUrlProblem, safeHref } from "./reference-link";

describe("original invoice link", () => {
  it("accepts https links from any host, and empty (which clears it)", () => {
    for (const url of ["https://chat.whatsapp.com/abc", "https://drive.google.com/file/d/x/view", "HTTPS://Example.com/a", "", "  "]) {
      expect(referenceUrlProblem(url)).toBeNull();
    }
  });

  it("refuses anything that is not a safe https link", () => {
    expect(referenceUrlProblem("http://example.com")).toMatch(/Only https/);
    expect(referenceUrlProblem("javascript:alert(1)")).toMatch(/Only https/);
    expect(referenceUrlProblem("data:text/html,x")).toMatch(/Only https/);
    expect(referenceUrlProblem("https://u:p@example.com/x")).toMatch(/username or password/);
    expect(referenceUrlProblem("https://example.com/a b")).toMatch(/spaces or control/);
    expect(referenceUrlProblem("https://example.com/" + "a".repeat(2100))).toMatch(/longer than/);
  });

  it("never renders anything but an https URL as a link", () => {
    expect(safeHref("https://example.com/x")).toBe("https://example.com/x");
    for (const url of ["javascript:alert(1)", "http://example.com", "", null, undefined]) {
      expect(safeHref(url)).toBeNull();
    }
  });
});
