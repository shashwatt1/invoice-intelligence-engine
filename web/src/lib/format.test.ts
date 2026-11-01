import { afterEach, describe, expect, it, vi } from "vitest";

import { formatDate, formatPlainDate } from "@/lib/format";

describe("formatPlainDate", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each(["America/Los_Angeles", "UTC", "Pacific/Auckland"])("shows the same day in %s", (zone) => {
    vi.stubEnv("TZ", zone);
    expect(formatPlainDate("2026-10-22")).toBe("22 Oct 2026");
    expect(formatPlainDate("2026-01-01")).toBe("01 Jan 2026");
  });

  it("is needed: formatDate moves a plain day back in the Americas", () => {
    vi.stubEnv("TZ", "America/Los_Angeles");
    expect(formatDate("2026-10-22")).toBe("21 Oct 2026");
  });

  it("shows a dash when there is no date and returns unreadable text as it is", () => {
    expect(formatPlainDate(null)).toBe("—");
    expect(formatPlainDate("")).toBe("—");
    expect(formatPlainDate("soon")).toBe("soon");
  });
});
