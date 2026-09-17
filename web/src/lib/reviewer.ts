/**
 * The reviewer's name, remembered per browser.
 *
 * There is no login. The name is typed once and recorded on every decision
 * exactly as the CLI's --by would record it — a name on the record, not a
 * proof of identity. localStorage is a convenience only; every read and
 * write is guarded because it can be absent or throw.
 */
const REVIEWER_KEY = "data-review.reviewer";

export function rememberedReviewer(): string {
  try {
    return localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

export function rememberReviewer(name: string): void {
  try {
    localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    /* per-browser convenience only */
  }
}
