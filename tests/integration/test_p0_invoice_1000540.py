"""
tests/integration/test_p0_invoice_1000540.py — the first real invoice
PDI accepted, frozen.

A.L. George / Onondaga Bev invoice 1000540 (27 Aug 2026, four lines, no
discounts, per-line deposits) was processed end to end, exported, and
imported into PDI without manual editing on 15 Sep 2026. PDI read every
item exactly as emitted:

    UPC          qty  units/case  case cost   PDI showed
    07199048024    4      12        14.50      14.50 (prev. 15.10)
    07199047712    6      12         9.00       9.00
    08066095757    1       2        31.05      31.05
    08066095680    1       1        21.65      21.65

and displayed Invoice Total = Σ(case cost x qty) = 164.70 — the B-record
economics — while the AMOUNT header carried the supplier's 172.80
(164.70 + 8.10 deposits). That file is the golden below, byte for byte.

The extraction that produced it had read unit_price from the NET column
(PRICE + DEP): 15.10 / 9.60 / 32.25 / 22.55. Validation passed because
EXT = NET x qty balances; the four prices were corrected by hand before
export. Rule D now proves the deposit is folded in from the invoice's
own totals and removes it, so the uncorrected extraction yields the
same golden with no manual correction.
"""

from __future__ import annotations

import hashlib

from app.schemas.extraction import ExtractedInvoice, ExtractedLineItem, ExtractedVendor
from app.services.pdi_audit import audit_pdi_export
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import approve_all_pending, requires_db
from tests.integration.fakes import FakeStructuring
from tests.integration.test_api_db import api_client, process_file  # noqa: F401 — fixture reuse
from tests.pdf_builder import build_pdf

pytestmark = requires_db

STORE = "86357232"

# The file PDI accepted on 15 Sep 2026. 5 CRLF records, 323 bytes.
GOLDEN = (
    b"AMOUNT 1000540   082726+000017280\r\n"
    b"B07199048024KEYSTONE LIGHT 12/24 CAN 00000000145001000012+000400000000\r\n"
    b"B07199047712KEYSTONE ICE 12/24 CAN   00000000090001000012+000600000000\r\n"
    b"B08066095757MODEL ESPECIAL 2/12/12 NR00000000310501000002+000100000000\r\n"
    b"B08066095680CORONA 18/12 NR          00000000216501000001+000100000000\r\n"
)
GOLDEN_SHA256 = "72b6bfe5a9544a48966fa8951c85a366d24e44946c47431813c554163386fe15"
UNITS = {"07199048024": 12, "07199047712": 12, "08066095757": 2, "08066095680": 1}


def line(description, code, pack, qty, unit_price, deposit, line_total):
    return ExtractedLineItem(
        description=description, product_code=code, pack_size=pack, quantity=qty,
        unit_price=unit_price, unit_discount=0, unit_deposit=deposit, line_total=line_total,
    )


def extraction(*, as_read: bool) -> ExtractedInvoice:
    """
    The invoice as the model read it (as_read=True: unit_price from the
    NET column, deposit included) or as the operator corrected it
    (unit_price = the printed PRICE). Everything else identical to the
    real extraction.
    """
    prices = (15.10, 9.60, 32.25, 22.55) if as_read else (14.50, 9.00, 31.05, 21.65)
    return ExtractedInvoice(
        vendor=ExtractedVendor(name="ONONDAGA BEVERAGE", address="7655 EDGECOMB DRIVE, LIVERPOOL, NY 13088"),
        invoice_number="1000540", invoice_date="2026-08-27", due_date="2026-08-27",
        currency="USD", payment_terms="C.O.D. CASH/CHECK",
        subtotal=164.70, tax_amount=0, discount_amount=0, deposit_total=8.10,
        fuel_surcharge=0, grand_total=172.80,
        line_items=[
            line("KEYSTONE LIGHT 12/24 CAN", "071990480240", "12/24 CAN", 4, prices[0], 0.60, 60.40),
            line("KEYSTONE ICE 12/24 CAN", "071990477127", "12/24 CAN", 6, prices[1], 0.60, 57.60),
            line("MODEL ESPECIAL 2/12/12 NR", "080660957579", "2/12/12 NR", 1, prices[2], 1.20, 32.25),
            line("CORONA 18/12 NR", "080660956800", "18/12 NR", 1, prices[3], 0.90, 22.55),
        ],
        confidence=1.0,
    )


async def run_p0(api_client, app, *, as_read: bool) -> tuple[dict, bytes]:  # noqa: F811
    """Process → confirm the four units → approve → export. Returns (detail, edi bytes)."""
    from app.api.v1.invoices import get_pipeline

    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
        structuring_service=FakeStructuring(extraction(as_read=as_read))
    )
    accepted = await process_file(
        api_client, content=build_pdf(["A.L. GEORGE / ONONDAGA BEV invoice 1000540 " + "pad " * 300]),
        filename="IMG_6473.pdf", store=STORE,
    )
    status = (await api_client.get(accepted["status_url"])).json()["data"]
    assert status["status"] == "COMPLETED", status
    invoice_id = status["invoice_id"]

    r = await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings", json={
        "mappings": [{"item_code": code, "units_per_case": units} for code, units in UNITS.items()]
    })
    assert r.status_code == 200, r.text
    return invoice_id


async def export(api_client, db_session, invoice_id):  # noqa: F811
    assert await approve_all_pending(db_session, reviewed_by="data-team:shashwat") == 4
    detail = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
    assert detail["pdi_export_allowed"] is True, detail["pdi_export_blocked_reason"]
    r = await api_client.get(f"/api/v1/invoices/{invoice_id}/export", params={"format": "pdi"})
    assert r.status_code == 200
    return detail, r.content


def assert_golden(detail: dict, edi: bytes) -> None:
    assert edi == GOLDEN
    assert len(edi) == 323 and edi.count(b"\r\n") == 5
    assert hashlib.sha256(edi).hexdigest() == GOLDEN_SHA256
    costs = {r["item_code"]: r for r in detail["case_mappings"]}
    assert {c: costs[c]["units_per_case"] for c in UNITS} == UNITS
    prices = [it["unit_price"] for it in detail["line_items"]]
    assert prices == [14.50, 9.00, 31.05, 21.65]
    assert detail["subtotal"] == 164.70 and detail["grand_total"] == 172.80


class TestTheFilePdiAccepted:
    async def test_the_corrected_extraction_reproduces_the_golden(self, api_client, app, db_session):  # noqa: F811
        invoice_id = await run_p0(api_client, app, as_read=False)
        detail, edi = await export(api_client, db_session, invoice_id)
        assert_golden(detail, edi)
        assert detail["store"]["source_codes"] == [STORE]

    async def test_the_audit_is_clean_and_states_the_header_gap(self, api_client, app, db_session):  # noqa: F811
        from app.repositories.invoice_repository import InvoiceRepository

        invoice_id = await run_p0(api_client, app, as_read=False)
        detail, edi = await export(api_client, db_session, invoice_id)
        invoice = await InvoiceRepository(db_session).get_detail(invoice_id)
        audit = audit_pdi_export(edi.decode(), invoice)
        assert audit.ok and sum(c.passed for c in audit.checks) == 31 and len(audit.checks) == 31
        # G2, as PDI showed it: the header carries the supplier total, the
        # detail economics carry the merchandise total; deposits are outside.
        amount = int(edi.split(b"\r\n")[0][-9:]) / 100
        detail_sum = sum(it["unit_price"] * it["quantity"] for it in detail["line_items"])
        assert amount == 172.80 and round(detail_sum, 2) == 164.70
        assert round(amount - detail_sum, 2) == 8.10 == detail["line_items"][0]["unit_deposit"] * 4 \
            + detail["line_items"][1]["unit_deposit"] * 6 + 1.20 + 0.90


class TestRuleDMakesTheCorrectionUnnecessary:
    async def test_the_extraction_as_read_yields_the_golden_with_no_manual_correction(
        self, api_client, app, db_session  # noqa: F811
    ):
        invoice_id = await run_p0(api_client, app, as_read=True)
        detail, edi = await export(api_client, db_session, invoice_id)
        assert_golden(detail, edi)
        # nobody edited a line: reconciliation proved the deposit and removed it
        assert all(it["corrected_fields"] == [] for it in detail["line_items"])
        assert all(it["unit_deposit"] in (0.6, 1.2, 0.9) for it in detail["line_items"])
        proofs = [c for c in detail["validation_report"]["checks"] if c["name"] == "UNIT_PRICE_INCLUDED_DEPOSIT"]
        assert len(proofs) == 4 and all(c["status"] == "PASSED" for c in proofs)
        assert detail["status"] == "VALIDATED"
        assert detail["validation_report"]["summary"]["failed"] == 0

    async def test_a_half_proved_deposit_goes_to_review_with_prices_untouched(self, api_client, app, db_session):  # noqa: F811
        from app.api.v1.invoices import get_pipeline

        broken = extraction(as_read=True)
        items = list(broken.line_items)
        items[3] = items[3].model_copy(update={"unit_deposit": 0.50})     # printed 0.90; misread
        broken = broken.model_copy(update={"line_items": items})
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(broken)
        )
        accepted = await process_file(api_client, content=build_pdf(["half proof " + "pad " * 300]),
                                      filename="half.pdf", store=STORE)
        status = (await api_client.get(accepted["status_url"])).json()["data"]
        detail = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert detail["status"] == "REVIEW_REQUIRED"
        assert [it["unit_price"] for it in detail["line_items"]] == [15.10, 9.60, 32.25, 22.55]
        assert any(c["name"] == "UNIT_PRICE_MAY_INCLUDE_DEPOSIT" and c["status"] == "FAILED"
                   for c in detail["validation_report"]["checks"])
        assert detail["pdi_export_allowed"] is False      # unmapped; and REVIEW_REQUIRED would need confirmation
