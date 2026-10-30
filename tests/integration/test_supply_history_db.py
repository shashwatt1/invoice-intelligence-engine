"""
Supply history across granted stores: vendor, store and delivery-date filters combine
with AND; the delivery date is the posted receipt's received_on, never the invoice date
or the processing time; stores without a grant are never listed.
"""

from __future__ import annotations

import uuid

from app.api.v1.invoices import get_pipeline
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import PHYSICAL_STORE, SOURCE_ONLY_STORE, requires_db, store_id
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.integration.test_api_db import api_client, process_file  # noqa: F401
from tests.integration.test_inventory_db import (  # noqa: F401
    ALE,
    LAGER,
    approve,
    approve_submission,
    grant,
    received,
    submit,
)
from tests.pdf_builder import build_pdf

pytestmark = requires_db
URL = "/api/v1/inventory/supply"


async def _invoice(api_client, app, *, vendor, tax_id, number, store, invoice_date):  # noqa: F811
    app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(structuring_service=FakeStructuring(
        extracted_invoice(vendor={"name": vendor, "tax_id": tax_id}, invoice_number=number,
                          invoice_date=invoice_date)))
    await process_file(api_client, content=build_pdf([f"supply {number} " + "pad " * 300]),
                       filename=f"{number}.pdf", store=store)


async def _numbers(api_client, **params):  # noqa: F811
    r = await api_client.get(URL, params={k: v for k, v in params.items() if v is not None})
    assert r.status_code == 200, r.text
    return sorted(i["invoice_number"] for i in r.json()["items"])


class TestSupplyHistory:
    async def test_vendor_store_and_delivery_date_filters_combine(self, api_client, app, db_session, received):  # noqa: F811
        rcm = str(received.store_id)
        # Received on 2026-10-09: the fixture invoice INV-001 (vendor Acme) at the fixture store.
        await approve(api_client, db_session, received.invoice_id, LAGER, 12)
        await approve(api_client, db_session, received.invoice_id, ALE, 6)
        preview = await submit(api_client, received.invoice_id)
        r = await approve_submission(api_client, received.invoice_id, preview, received_on="2026-10-09")
        assert r.status_code == 200, r.text
        # Not received: another Acme invoice at a second granted store, and a Globex one at the first.
        await grant(db_session, "ADMIN", store_id(PHYSICAL_STORE))
        await _invoice(api_client, app, vendor="Acme Corp", tax_id="GB123", number="ACME-2", store=PHYSICAL_STORE,
                       invoice_date="2026-10-09")
        await _invoice(api_client, app, vendor="Globex GmbH", tax_id="DE999", number="GLX-1", store="86357232",
                       invoice_date="2026-10-09")

        options = (await api_client.get(f"{URL}/options")).json()["data"]
        vendors = {v["label"]: v for v in options["vendors"]}
        acme, globex = vendors["Acme Corp"]["id"], vendors["Globex GmbH"]["id"]
        assert len(vendors["Acme Corp"]["store_ids"]) == 2 and vendors["Globex GmbH"]["store_ids"] == [rcm]

        assert await _numbers(api_client) == ["ACME-2", "GLX-1", "INV-001"]
        assert await _numbers(api_client, vendor_id=acme) == ["ACME-2", "INV-001"]          # across stores
        assert await _numbers(api_client, store_id=rcm) == ["GLX-1", "INV-001"]             # one store
        assert await _numbers(api_client, vendor_id=acme, store_id=rcm) == ["INV-001"]      # both
        assert await _numbers(api_client, vendor_id=globex, store_id=str(store_id(PHYSICAL_STORE))) == []

        # Delivery date: the receipt's date, inclusive boundaries; the invoice date (also 2026-10-09
        # for the others) never matches by itself.
        assert await _numbers(api_client, delivered_from="2026-10-09", delivered_to="2026-10-09") == ["INV-001"]
        assert await _numbers(api_client, delivered_from="2026-10-01", delivered_to="2026-10-09") == ["INV-001"]
        assert await _numbers(api_client, delivered_from="2026-10-10") == []
        assert await _numbers(api_client, delivery="missing") == ["ACME-2", "GLX-1"]
        assert await _numbers(api_client, vendor_id=acme, delivered_to="2026-10-31") == ["INV-001"]

        rows = (await api_client.get(URL, params={"store_id": rcm})).json()["items"]
        by = {row["invoice_number"]: row for row in rows}
        assert by["INV-001"]["delivered_on"] == "2026-10-09" and by["GLX-1"]["delivered_on"] is None
        assert by["INV-001"]["store"]["id"] == rcm

    async def test_invalid_filters_are_refused(self, api_client, db_session, received):  # noqa: F811
        bad = await api_client.get(URL, params={"delivered_from": "2026-10-10", "delivered_to": "2026-10-01"})
        assert bad.status_code == 422
        both = await api_client.get(URL, params={"delivery": "missing", "delivered_from": "2026-10-01"})
        assert both.status_code == 422
        assert (await api_client.get(URL, params={"delivered_from": "not-a-date"})).status_code == 422
        assert (await api_client.get(URL, params={"vendor_id": str(uuid.uuid4())})).status_code == 404
        # An unknown store and a source identity are the same 404 as everywhere in inventory; an administrator
        # needs no grant for an active physical store.
        assert (await api_client.get(URL, params={"store_id": str(uuid.uuid4())})).status_code == 404
        assert (await api_client.get(URL, params={"store_id": str(store_id(SOURCE_ONLY_STORE))})).status_code == 404
        assert (await api_client.get(URL, params={"store_id": str(store_id(PHYSICAL_STORE))})).status_code == 200
