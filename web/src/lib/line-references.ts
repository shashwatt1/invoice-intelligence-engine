import type { LineItem, ValidationCheck } from "@/api/types";

/**
 * Row numbers and row references for one invoice's line items.
 *
 * A row's serial is its 1-based rank in canonical invoice order
 * (`sort_order`, the order the rows were printed, manual rows appended).
 * Every displayed row is numbered — products, charges, voided and
 * duplicate rows alike — and the number never depends on where the row
 * happens to be rendered, so a future filter, sort or page keeps it.
 *
 * Validation checks name a line as `line_items[<sort_order>]...`. A check
 * may only point at a row when its reference is well formed AND exactly
 * one row holds that sort_order. A missing, malformed, stale (no such
 * row) or ambiguous (several rows) reference resolves to nothing, and the
 * caller renders no link — never a guess.
 */

export interface RowRefs {
  /** The 1-based serial of a displayed row (by identity), or undefined for a row not in the list. */
  serialOf(item: LineItem): number | undefined;
  /** The serial of the one row holding this sort_order; null when no row or several rows do. */
  serialForSortOrder(sortOrder: number): number | null;
  /** True only when exactly one row holds this sort_order. */
  isUnique(sortOrder: number): boolean;
}

export function buildRowRefs(items: readonly LineItem[]): RowRefs {
  const ordered = items
    .map((item, position) => ({ item, position }))
    .sort((a, b) => a.item.sort_order - b.item.sort_order || a.position - b.position);

  const serials = new Map<LineItem, number>();
  const holders = new Map<number, number>();
  ordered.forEach(({ item }, rank) => {
    serials.set(item, rank + 1);
    holders.set(item.sort_order, (holders.get(item.sort_order) ?? 0) + 1);
  });

  const unique = new Map<number, number>();
  for (const [item, serial] of serials) {
    if (holders.get(item.sort_order) === 1) unique.set(item.sort_order, serial);
  }

  return {
    serialOf: (item) => serials.get(item),
    serialForSortOrder: (sortOrder) => unique.get(sortOrder) ?? null,
    isUnique: (sortOrder) => unique.has(sortOrder),
  };
}

// "line_items[31]" or "line_items[31].line_total"; no wildcard, sign or leading zero.
const LINE_FIELD = /^line_items\[(0|[1-9]\d*)\](?:\.|$)/;

/** The sort_order a check's `field` names, or null when it names no single line. */
export function parseLineReference(field: string | null | undefined): number | null {
  if (!field) return null;
  const match = LINE_FIELD.exec(field);
  if (!match) return null;
  const sortOrder = Number(match[1]);
  return Number.isSafeInteger(sortOrder) ? sortOrder : null;
}

export interface CheckTarget {
  sortOrder: number;
  serial: number;
}

/** The row a check reliably points at, or null (invoice-level, malformed, stale or ambiguous). */
export function checkTarget(check: Pick<ValidationCheck, "field">, refs: RowRefs): CheckTarget | null {
  const sortOrder = parseLineReference(check.field);
  if (sortOrder === null) return null;
  const serial = refs.serialForSortOrder(sortOrder);
  return serial === null ? null : { sortOrder, serial };
}

/** How the validation views turn a check into a row link. */
export interface RowLinks {
  targetOf(check: ValidationCheck): CheckTarget | null;
  goToRow(sortOrder: number): void;
}

/** DOM id of the table row for a sort_order (only ever set on a uniquely held one). */
export function rowDomId(sortOrder: number): string {
  return `line-row-${sortOrder}`;
}
