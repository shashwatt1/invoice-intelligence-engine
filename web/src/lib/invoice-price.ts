/**
 * Invoice prices shown while mapping a product. Source values are exactly what
 * the invoice line carries; the only derived figure is the price per selling
 * unit, and it is offered only when both inputs are valid. Nothing here is
 * written anywhere or reaches an EDI.
 */

/** "$12.34" for a finite number, null otherwise (a missing price is never shown as $0.00). */
export function formatMoney(value: number | null | undefined): string | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

/** The quantity as invoiced, without trailing zeros ("2", "1.5"). */
export function formatQuantity(value: number | null | undefined): string | null {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  return String(Number(value.toFixed(4)));
}

/**
 * Invoice price per invoiced unit divided by units per case: the cost of one
 * selling unit. Null unless the price is a positive number and the units a
 * whole number of at least one.
 */
export function pricePerSellingUnit(unitPrice: number | null | undefined, unitsPerCase: number | null | undefined): number | null {
  if (unitPrice === null || unitPrice === undefined || !Number.isFinite(unitPrice) || unitPrice <= 0) return null;
  if (unitsPerCase === null || unitsPerCase === undefined || !Number.isInteger(unitsPerCase) || unitsPerCase < 1) return null;
  return unitPrice / unitsPerCase;
}

/** Four decimals below $1 (a can at $0.4167), two from $1 up. */
export function formatPerUnit(value: number): string {
  return `$${value.toFixed(value < 1 ? 4 : 2)}`;
}
