import type { InventoryItemStatus, ReceiptLine } from "@/api/types";
import type { Tone } from "@/lib/status";

/**
 * Store inventory presentation rules. The server decides every figure; this
 * only names them. A product with no audited opening count has no on-hand
 * figure at all — "Opening stock required" — never a zero or a guess.
 */

export const ITEM_STATUS: Record<InventoryItemStatus, { label: string; tone: Tone; meaning: string }> = {
  OPENING_STOCK_REQUIRED: {
    label: "Opening stock required",
    tone: "warning",
    meaning: "No audited opening count yet, so there is no on-hand figure. Receipts are still recorded.",
  },
  OUT_OF_STOCK: { label: "Out of stock", tone: "danger", meaning: "Opening count plus recorded movements is zero or less." },
  IN_STOCK: { label: "In stock", tone: "success", meaning: "Opening count plus recorded movements." },
};

const RECEIVABLE_REASONS: Record<string, string> = {
  invoice_not_validated: "The invoice is not validated yet",
  store_pending: "The invoice's store is not identified",
  store_record_missing: "The invoice's store record is missing",
  store_is_not_physical: "The invoice is assigned to a source identity, not a physical store",
  document_withdrawn: "The document was stopped or binned",
};

export function receivableReason(code: string): string {
  return RECEIVABLE_REASONS[code] ?? code.replaceAll("_", " ");
}

export const MOVEMENT_LABEL: Record<string, string> = {
  OPENING_COUNT: "Opening count",
  RECEIPT: "Receipt",
  SALE: "Sale",
  SALE_RETURN: "Sale return",
  RETURN_TO_VENDOR: "Return to vendor",
  TRANSFER_IN: "Transfer in",
  TRANSFER_OUT: "Transfer out",
  ADJUSTMENT: "Adjustment",
};

/** "1,234" for a whole number of units, else the decimal as sent. */
export function formatUnits(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  return Number.isInteger(n) ? n.toLocaleString("en-US") : n.toLocaleString("en-US", { maximumFractionDigits: 4 });
}

export interface LineEntry {
  accepted: string;
  reason: string;
}

/**
 * What is wrong with one receipt line as entered, or null. Mirrors the
 * server (which decides): a whole number of at least 0, and a reason when
 * it differs from what the invoice and its approved units per case expect.
 */
export function lineProblem(line: ReceiptLine, entry: LineEntry): string | null {
  const text = entry.accepted.trim();
  if (text === "") return "Enter the units accepted";
  const accepted = Number(text);
  if (!Number.isFinite(accepted)) return "Enter a number";
  if (accepted < 0) return "Cannot be negative";
  if (!Number.isInteger(accepted)) return "Whole sellable units only";
  if (accepted !== Number(line.expected_units) && entry.reason.trim() === "") return "Differs from expected: give a reason";
  return null;
}

export function isDiscrepancy(line: ReceiptLine, entry: LineEntry): boolean {
  const accepted = Number(entry.accepted.trim());
  return entry.accepted.trim() !== "" && Number.isFinite(accepted) && accepted !== Number(line.expected_units);
}

/** Today in the browser's own calendar, as YYYY-MM-DD (a business date, not an instant). */
export function todayIso(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/**
 * "code, quantity" lines typed or pasted for an opening count. Blank lines
 * are skipped; anything unreadable is reported by line number rather than
 * dropped, so a count is never silently shortened.
 */
export function parseCountLines(text: string): { entries: { item_code: string; quantity: number }[]; errors: string[] } {
  const entries: { item_code: string; quantity: number }[] = [];
  const errors: string[] = [];
  text.split(/\r?\n/).forEach((raw, index) => {
    const line = raw.trim();
    if (!line) return;
    const parts = line.split(/[,\t;]+|\s{2,}|\s(?=\S+$)/).map((p) => p.trim()).filter(Boolean);
    const quantity = Number(parts[1]);
    if (parts.length !== 2 || !/^\d{6,14}$/.test(parts[0]) || !Number.isInteger(quantity) || quantity < 0) {
      errors.push(`Line ${index + 1}: expected "item code, whole quantity" — got "${line}"`);
      return;
    }
    entries.push({ item_code: parts[0], quantity });
  });
  return { entries, errors };
}
