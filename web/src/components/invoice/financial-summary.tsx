import type { InvoiceDetail } from "@/api/types";
import { EditableTotal } from "@/components/invoice/corrections";
import { formatMoney } from "@/lib/format";

/**
 * The invoice's money, kept as separate concepts: content, discount,
 * deposits, delivery/fuel, tax, and the grand total. The right-hand
 * figures are computed from the persisted lines so a reviewer can see
 * at a glance whether the header and the lines agree.
 */
export function FinancialSummary({
  detail, by, readOnly = false,
}: { detail: InvoiceDetail; by: string; readOnly?: boolean }) {
  const products = detail.line_items.filter((i) => i.line_type === "product");
  const merchandise = products.reduce((sum, i) => sum + (i.unit_price ?? 0) * i.quantity, 0);
  const deposits = products.reduce((sum, i) => sum + (i.unit_deposit ?? 0) * i.quantity, 0);
  const discounts = products.reduce((sum, i) => sum + (i.unit_discount ?? 0) * i.quantity, 0);
  const ext = products.reduce((sum, i) => sum + (i.line_total ?? 0), 0);

  return (
    <div className="surface overflow-hidden" data-testid="financial-summary">
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] max-md:grid-cols-1">
        <div className="px-5 py-4">
          <div className="t-eyebrow mb-2">Printed totals</div>
          <EditableTotal detail={detail} field="subtotal" by={by} readOnly={readOnly} />
          <EditableTotal detail={detail} field="discount_amount" by={by} readOnly={readOnly} />
          <EditableTotal detail={detail} field="deposit_total" by={by} readOnly={readOnly} />
          <EditableTotal detail={detail} field="fuel_surcharge" by={by} readOnly={readOnly} />
          <EditableTotal detail={detail} field="tax_amount" by={by} readOnly={readOnly} />
          <div className="mt-2 border-t pt-2">
            <EditableTotal detail={detail} field="grand_total" by={by} emphasized readOnly={readOnly} />
          </div>
          {!readOnly ? <p className="t-meta mt-2">Click a figure to correct it; the extracted value stays in the history.</p> : null}
        </div>
        <div className="border-l bg-surface-2 px-5 py-4 max-md:border-t max-md:border-l-0">
          <div className="t-eyebrow mb-2">From the {products.length} product lines</div>
          <dl className="space-y-1.5 text-[0.82rem]">
            <div className="flex justify-between"><dt className="text-muted-foreground">Σ price × qty</dt><dd className="t-money">{formatMoney(merchandise)}</dd></div>
            <div className="flex justify-between"><dt className="text-muted-foreground">Σ deposit × qty</dt><dd className="t-money">{formatMoney(deposits)}</dd></div>
            <div className="flex justify-between"><dt className="text-muted-foreground">Σ discount × qty</dt><dd className="t-money">{formatMoney(discounts)}</dd></div>
            <div className="flex justify-between border-t pt-1.5"><dt className="text-muted-foreground">Σ printed line totals</dt><dd className="t-money">{formatMoney(ext)}</dd></div>
            <div className="flex justify-between"><dt className="text-muted-foreground">merchandise + deposits + delivery</dt>
              <dd className="t-money font-semibold">{formatMoney(merchandise + deposits + (detail.fuel_surcharge ?? 0))}</dd></div>
          </dl>
          <p className="t-meta mt-3 leading-snug">Merchandise is what the EDI carries as case cost; deposits and delivery stay outside PDI merchandise.</p>
        </div>
      </div>
    </div>
  );
}
