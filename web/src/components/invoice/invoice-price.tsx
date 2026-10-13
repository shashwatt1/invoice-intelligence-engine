import { formatMoney, formatPerUnit, formatQuantity, pricePerSellingUnit } from "@/lib/invoice-price";

/**
 * The invoice's own figures for one line, labelled as such, plus — only when
 * it can be computed reliably — the calculated price per selling unit at the
 * given units per case. Display only.
 */
export function InvoicePrice({
  quantity,
  unitPrice,
  lineTotal,
  unitDiscount,
  unitsPerCase,
  unitsLabel,
}: {
  quantity: number | null | undefined;
  unitPrice: number | null | undefined;
  lineTotal: number | null | undefined;
  unitDiscount?: number | null;
  /** Units per case to divide by: the confirmed value, or the value being entered. */
  unitsPerCase?: number | null;
  /** How to describe that value ("confirmed", "as entered"). */
  unitsLabel?: string;
}) {
  const qty = formatQuantity(quantity);
  const price = formatMoney(unitPrice);
  const total = formatMoney(lineTotal);
  const discount = unitDiscount ? formatMoney(unitDiscount) : null;
  const perUnit = pricePerSellingUnit(unitPrice, unitsPerCase);
  return (
    <div className="t-meta space-y-0.5" data-testid="invoice-price">
      <div>
        <span className="font-medium">From invoice:</span>{" "}
        {qty !== null ? `qty ${qty}` : "qty —"}
        {" · "}
        {price !== null ? `${price} each` : "no unit price on the invoice"}
        {total !== null ? ` · line ${total}` : ""}
        {discount !== null ? ` · discount ${discount}/unit` : ""}
      </div>
      {perUnit !== null ? (
        <div data-testid="invoice-price-per-unit">
          ≈ {formatPerUnit(perUnit)} per selling unit at {unitsPerCase}/case
          <span className="opacity-70"> (calculated{unitsLabel ? `, ${unitsLabel}` : ""})</span>
        </div>
      ) : null}
    </div>
  );
}
