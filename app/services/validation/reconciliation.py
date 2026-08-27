"""
Reconciliation Engine — app/services/validation/reconciliation.py

Runs between normalization and validation. Where validation *judges* an
invoice ("these numbers don't add up"), reconciliation *repairs* it —
but only when arithmetic proves a single unambiguous answer.

Design decisions:
- Deterministic only. No AI, no heuristics, no "best guess". Every
  correction must be provable from figures already extracted from the
  document.
- Never fabricates. A value is only ever replaced by another value the
  document itself supplied; nothing is invented from nothing. When the
  arithmetic does not resolve cleanly, the values are left exactly as
  extracted and a WARNING check is raised for human review.
- Every correction is recorded: as a CheckResult in the validation
  report, and as a flag on the line item. The original extraction always
  survives untouched in invoices.raw_extraction_json.

The rules exist because of one observed production failure. On a real
7-line invoice the model read `unit_price` from the gross (pre-discount)
column while reading `line_total` from the net column, so
`quantity x unit_price != line_total` on every row and the line totals
summed to the invoice's printed "Total Sales" ($288.32) instead of its
"Total Content" ($263.86) — a 9.3% cost overstatement exactly equal to
the printed total discount.
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.normalized import NormalizedInvoice, NormalizedLineItem
from app.services.validation.report import CheckResult, CheckStatus

MONEY_EXP = Decimal("0.01")


def _within(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) <= tolerance


def _reconcile_line_item(
    item: NormalizedLineItem, tolerance: Decimal, checks: list[CheckResult]
) -> NormalizedLineItem:
    """
    Resolve a line whose quantity x unit_price does not match line_total.

    Two arithmetic explanations are tested, in order. Both are proved
    against figures already on the document — neither invents a value.
    """
    prefix = f"line_items[{item.sort_order}]"
    qty, unit_price, line_total = item.quantity, item.unit_price, item.line_total

    # Nothing to reconcile without all three, or when the identity holds.
    if qty is None or unit_price is None or line_total is None or qty == 0:
        return item
    if _within((qty * unit_price).quantize(MONEY_EXP), line_total, tolerance):
        return item

    # Rule A — gross price mistaken for net.
    # If subtracting the per-unit discount makes the line balance, the
    # extracted unit_price came from the gross/list column and the net
    # price is the one the document also printed. This is the exact
    # failure documented in the module docstring.
    if item.unit_discount is not None:
        net = (unit_price - item.unit_discount).quantize(MONEY_EXP)
        if _within((qty * net).quantize(MONEY_EXP), line_total, tolerance):
            checks.append(
                CheckResult(
                    name="UNIT_PRICE_RECONCILED",
                    status=CheckStatus.PASSED,
                    field=f"{prefix}.unit_price",
                    message=(
                        "Extracted unit price was the gross (pre-discount) figure; "
                        f"replaced with the net price {net} proved by "
                        "quantity x (unit_price - unit_discount) = line_total."
                    ),
                    expected=str(net),
                    actual=str(unit_price),
                )
            )
            return item.model_copy(
                update={"unit_price": net, "unit_price_reconciled": True}
            )

    # Rule B — deposit folded into the extended total.
    # Some layouts print line_total as (cost + deposit) x quantity. The
    # unit_price is then already correct and must NOT be altered; the
    # apparent mismatch is fully explained.
    if item.unit_deposit is not None:
        with_deposit = ((unit_price + item.unit_deposit) * qty).quantize(MONEY_EXP)
        if _within(with_deposit, line_total, tolerance):
            checks.append(
                CheckResult(
                    name="LINE_TOTAL_INCLUDES_DEPOSIT",
                    status=CheckStatus.PASSED,
                    field=f"{prefix}.line_total",
                    message=(
                        "Line total includes the container deposit "
                        f"({item.unit_deposit}/unit); unit price is correct as extracted."
                    ),
                )
            )
            return item

    # Rule C — a discount AND a deposit on the same line.
    # Rules A and B each explain one component; layouts that print both as
    # separate columns need them together:
    #
    #     EXT = (PRICE - DISC + DEP) x QTY
    #
    # Observed on Rocco J. Testani 228245, where this identity holds on all
    # 38 printed rows and is corroborated by four independent printed
    # controls (Cases 86, Total Deposit 80.10, Total Sales 2,053.02,
    # Invoice Total 2,058.02). Nineteen rows were left unresolved because
    # neither single-component rule could explain them, even though the
    # document had already supplied every figure needed to prove the net
    # cost. Ordered last so a line that either rule alone explains keeps
    # its existing, narrower interpretation.
    if item.unit_discount is not None and item.unit_deposit is not None:
        net = (unit_price - item.unit_discount).quantize(MONEY_EXP)
        expected = (qty * (net + item.unit_deposit)).quantize(MONEY_EXP)
        if _within(expected, line_total, tolerance):
            checks.append(
                CheckResult(
                    name="UNIT_PRICE_RECONCILED",
                    status=CheckStatus.PASSED,
                    field=f"{prefix}.unit_price",
                    message=(
                        "Extracted unit price was the gross (pre-discount) figure; "
                        f"replaced with the net price {net} proved by quantity x "
                        "(unit_price - unit_discount + unit_deposit) = line_total."
                    ),
                    expected=str(net),
                    actual=str(unit_price),
                )
            )
            checks.append(
                CheckResult(
                    name="LINE_TOTAL_INCLUDES_DEPOSIT",
                    status=CheckStatus.PASSED,
                    field=f"{prefix}.line_total",
                    message=(
                        "Line total also includes the container deposit "
                        f"({item.unit_deposit}/unit), which is not part of the "
                        "product cost."
                    ),
                )
            )
            return item.model_copy(
                update={"unit_price": net, "unit_price_reconciled": True}
            )

    # Unresolved: leave the extracted values untouched and route to review.
    checks.append(
        CheckResult(
            name="LINE_ITEM_UNRECONCILED",
            status=CheckStatus.WARNING,
            field=prefix,
            message=(
                "quantity x unit_price does not match the line total and no "
                "discount or deposit on the document explains the difference. "
                "Values left exactly as extracted for human review."
            ),
            expected=str((qty * unit_price).quantize(MONEY_EXP)),
            actual=str(line_total),
        )
    )
    return item


def reconcile_invoice(
    invoice: NormalizedInvoice, tolerance: Decimal
) -> tuple[NormalizedInvoice, list[CheckResult]]:
    """
    Deterministically repair what arithmetic can prove, flag what it can't.

    Returns the (possibly corrected) invoice and the checks describing
    every correction made or declined.
    """
    checks: list[CheckResult] = []

    items = tuple(
        _reconcile_line_item(item, tolerance, checks) for item in invoice.line_items
    )
    reconciled = invoice.model_copy(update={"line_items": items})

    # Invoice-level cross-check: the corrected line totals should now sum
    # to the printed net subtotal. This is the check that would have
    # caught the gross/net error even if no per-line discount had been
    # extracted, so it is worth reporting either way.
    line_totals = [i.line_total for i in items if i.line_total is not None]
    if reconciled.subtotal is not None and line_totals:
        computed = sum(line_totals, Decimal("0.00")).quantize(MONEY_EXP)
        # Layouts that fold the deposit into each extended total make the
        # line totals sum to subtotal + deposits, not to the subtotal. That
        # is the document being internally consistent, not an error, so
        # accept it rather than sending a correct invoice to review.
        deposits = reconciled.deposit_total or Decimal("0")
        if _within(computed, reconciled.subtotal, tolerance) or _within(
            computed, (reconciled.subtotal + deposits).quantize(MONEY_EXP), tolerance
        ):
            checks.append(
                CheckResult(
                    name="SUBTOTAL_RECONCILED",
                    status=CheckStatus.PASSED,
                    field="subtotal",
                    message="Line totals sum to the printed subtotal.",
                )
            )
        else:
            checks.append(
                CheckResult(
                    name="SUBTOTAL_UNRECONCILED",
                    status=CheckStatus.WARNING,
                    field="subtotal",
                    message=(
                        "Line totals do not sum to the printed subtotal. If the "
                        "difference equals the invoice discount, the wrong price "
                        "column was read for one or more lines."
                    ),
                    expected=str(reconciled.subtotal),
                    actual=str(computed),
                )
            )

    return reconciled, checks
