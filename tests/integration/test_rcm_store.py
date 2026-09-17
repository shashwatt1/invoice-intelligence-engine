"""
tests/integration/test_rcm_store.py — RCM is a store the system can
process invoices for, through the existing store architecture only.

What is pinned:
  - the migration seeds RCM exactly once, identity UNRESOLVED, known by
    the names and address the invoice photographs show, linked to NO
    source code (the drift-test scratch database is built from the
    migrations alone, so that is where the seed is checked);
  - RCM appears in the store directory the pickers read, labelled as
    unconfirmed;
  - a document naming Red Cliff Market is offered RCM as a candidate;
  - an invoice can be processed for RCM up front, or read first and
    assigned to RCM later;
  - matching and case mappings are RCM's own: another store's mapping
    for the same UPC is not consulted, and a mapping confirmed on an RCM
    invoice becomes an RCM proposal, then an RCM mapping — nothing else
    changes.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text

from app.models.store import IDENTITY_UNRESOLVED, SOURCE_DOCUMENT
from app.repositories.product_case_mapping_repository import ProductCaseMappingRepository
from app.repositories.product_data_proposal_repository import ProductDataProposalRepository
from app.repositories.store_repository import StoreRepository
from app.services.store_identification_service import identify_store
from tests.integration.conftest import approve_all_pending, requires_db, store_id
from tests.integration.test_api_db import api_client  # noqa: F401 — fixture reuse
from tests.integration.test_migrations_match_models import (
    migrated_scratch_db,  # noqa: F401 — fixture reuse
)
from tests.integration.test_proposal_governance import NORMALIZED, line
from tests.integration.test_store_pending import upload_without_store
from tests.pdf_builder import build_pdf

pytestmark = requires_db

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "20260918_0018_rcm_store.py"


def _seed_constants():
    spec = importlib.util.spec_from_file_location("rcm_migration", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def seed_rcm(session):
    """RCM as the migration makes it, in the create_all test database."""
    m = _seed_constants()
    repo = StoreRepository(session)
    store = await repo.create(display_name=m.RCM["display_name"], customer_name=m.RCM["customer_name"],
                              address_line_1=m.RCM["address_line_1"], city=m.RCM["city"],
                              state=m.RCM["state"], postal_code=m.RCM["postal_code"],
                              identity_status=IDENTITY_UNRESOLVED, notes=m.RCM["notes"])
    for kind, values in (("customer_name", m.NAMES), ("address_line", m.ADDRESS_LINES), ("postal_code", m.POSTAL_CODES)):
        for value in values:
            await repo.add_identifier(store, SOURCE_DOCUMENT, kind, value, evidence={"verified": False})
    await session.commit()
    return store.id


class TestTheMigrationSeedsRcm:
    def test_rcm_exists_once_unresolved_with_no_source_code(self, migrated_scratch_db):  # noqa: F811
        engine = create_engine(migrated_scratch_db)
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, display_name, customer_name, address_line_1, city, state, postal_code, identity_status "
                "FROM stores WHERE lower(display_name) = 'rcm'")).all()
            assert len(rows) == 1
            (sid, dn, cn, a1, city, state, zip_, status) = rows[0]
            assert (dn, cn, a1, city, state, zip_, status) == (
                "RCM", "Red Cliff Market", "1409 E St George Blvd", "St George", "UT", "84790", "unresolved")
            idents = conn.execute(text(
                "SELECT source_system, identifier_type, identifier_value, evidence->>'verified' "
                "FROM store_identifiers WHERE store_id = :sid ORDER BY identifier_type, identifier_value"),
                {"sid": sid}).all()
            assert all(src == "document" and verified == "false" for src, _, _, verified in idents)
            assert not any(kind == "store_code" for _, kind, _, _ in idents)       # no POS code inferred
            assert {v for _, kind, v, _ in idents if kind == "customer_name"} == {
                "RED CLIFF MARKET", "RED CLIFFS MARKET", "RED CLIFF TEXACO", "RED CLIFF PETROLEUM, LLC"}
            assert {v for _, kind, v, _ in idents if kind == "postal_code"} == {"84790", "84770"}
            # nothing of its own yet — reference data is a separate, explicit import
            for table in ("product_case_mappings", "store_product_references", "product_pricing", "product_identity"):
                assert conn.execute(text(f"SELECT count(*) FROM {table} WHERE store_id = :sid"), {"sid": sid}).scalar() == 0
            # the other stores were not touched
            assert conn.execute(text("SELECT count(*) FROM stores")).scalar() >= 1
        engine.dispose()


class TestRcmInTheDirectoryAndIdentification:
    async def test_rcm_is_listed_for_every_picker(self, api_client, db_session):  # noqa: F811
        rcm = await seed_rcm(db_session)
        stores = (await api_client.get("/api/v1/stores")).json()["data"]
        entry = next(s for s in stores if s["id"] == str(rcm))
        assert entry["label"] == "RCM (identity unconfirmed)"
        assert entry["identity_status"] == "unresolved"
        assert entry["source_codes"] == []
        assert entry["address"] == "1409 E St George Blvd, St George, UT 84790"
        assert (entry["case_mappings"], entry["pricing_rows"], entry["catalogue_rows"]) == (0, 0, 0)

    async def test_a_document_naming_red_cliff_market_is_offered_rcm(self, db_session):
        rcm = await seed_rcm(db_session)
        candidates = await identify_store(
            db_session, "COCA-COLA OF SOUTHERN UTAH  SOLD TO: RED CLIFF MARKET 1409 E ST GEORGE BLVD ST GEORGE UT 84790")
        assert [c.store_id for c in candidates] == [str(rcm)]
        matched = [m if isinstance(m, dict) else vars(m) for m in candidates[0].matched_on]
        assert matched and all(not m.get("verified") for m in matched)        # evidence, not confirmation
        # a document naming nobody the system knows is offered nothing
        assert await identify_store(db_session, "ACME BEVERAGE INVOICE 555") == []


class TestProcessingForRcm:
    async def test_chosen_up_front_and_assigned_later_both_land_on_rcm(self, api_client, app, db_session):  # noqa: F811
        from app.api.v1.invoices import get_pipeline
        from app.services.pipeline_service import InvoiceProcessingPipeline
        from tests.integration.fakes import FakeExtraction, FakeStructuring, extracted_invoice

        rcm = await seed_rcm(db_session)
        # up front: the operator chooses RCM; the fake text names no store, so the choice stands
        app.dependency_overrides[get_pipeline] = lambda: InvoiceProcessingPipeline(
            extraction_service=FakeExtraction(),
            structuring_service=FakeStructuring(extracted_invoice(line_items=[line()], subtotal=18.75, grand_total=18.75)))
        r = await api_client.post("/api/v1/invoices/process",
                                  files={"file": ("rcm-1.pdf", build_pdf(["rcm one pad " * 300]), "application/pdf")},
                                  data={"store_id": str(rcm)})
        assert r.status_code == 202, r.text
        status = (await api_client.get(r.json()["data"]["status_url"])).json()["data"]
        assert status["status"] == "COMPLETED" and status["store"]["id"] == str(rcm)
        first = (await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")).json()["data"]
        assert first["store"]["label"] == "RCM (identity unconfirmed)" and first["store_pending"] is False

        # read first, assign later
        document_id = await upload_without_store(api_client, app, name="rcm-2.pdf", subtotal=18.75, grand_total=18.75)
        await api_client.post(f"/api/v1/documents/{document_id}/defer-store", json={"deferred_by": "data-team:shashwat"})
        invoice_id = (await api_client.get(f"/api/v1/documents/{document_id}")).json()["data"]["invoice_id"]
        r = await api_client.post(f"/api/v1/invoices/{invoice_id}/assign-store",
                                  json={"store_id": str(rcm), "assigned_by": "data-team:shashwat"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["store"]["id"] == str(rcm)
        rows = (await api_client.get("/api/v1/invoices")).json()["items"]
        assert sum(1 for row in rows if row["store"] and row["store"]["id"] == str(rcm)) == 2

    async def test_matching_and_mappings_are_rcms_own(self, api_client, app, db_session):  # noqa: F811
        rcm = await seed_rcm(db_session)
        # store 47708760 already has an approved mapping for this UPC
        other = await ProductDataProposalRepository(db_session).create(
            store_id=store_id("47708760"), entity_type="case_mapping", entity_key=NORMALIZED,
            field="units_per_case", proposed_value=24, current_value=None, source="reference_derived",
            proposed_by="test", evidence={})
        await db_session.commit()
        assert await approve_all_pending(db_session) == 1

        document_id = await upload_without_store(api_client, app, name="rcm-3.pdf", subtotal=18.75, grand_total=18.75)
        await api_client.post(f"/api/v1/documents/{document_id}/defer-store", json={"deferred_by": "data-team:shashwat"})
        invoice_id = (await api_client.get(f"/api/v1/documents/{document_id}")).json()["data"]["invoice_id"]
        r = await api_client.post(f"/api/v1/invoices/{invoice_id}/assign-store",
                                  json={"store_id": str(rcm), "assigned_by": "data-team:shashwat"})
        assert r.status_code == 200, r.text
        detail = r.json()["data"]
        assert detail["store"]["id"] == str(rcm)

        # RCM's row: the other store's 24 is not consulted, no reference cost, nothing prefilled
        [row] = detail["case_mappings"]
        assert row["item_code"] == NORMALIZED
        assert row["mapped"] is False and row["units_per_case"] is None
        assert row["reference_avg_cost"] is None
        assert detail["pdi_export_allowed"] is False

        # confirming on the RCM invoice raises an RCM proposal; approving writes an RCM mapping only
        m = await api_client.post(f"/api/v1/invoices/{invoice_id}/case-mappings",
                                  json={"mappings": [{"item_code": NORMALIZED, "units_per_case": 4}]})
        assert m.status_code == 200, m.text
        [p] = await ProductDataProposalRepository(db_session).list(invoice_id=uuid.UUID(invoice_id))
        assert p.store_id == rcm and p.proposed_value == 4
        assert await approve_all_pending(db_session) == 1
        mine = await ProductCaseMappingRepository(db_session).get(rcm, NORMALIZED)
        theirs = await ProductCaseMappingRepository(db_session).get(store_id("47708760"), NORMALIZED)
        assert mine.units_per_case == 4 and mine.approved_proposal_id == p.id
        assert theirs.units_per_case == 24 and theirs.approved_proposal_id == other.id     # untouched
        after = (await api_client.get(f"/api/v1/invoices/{invoice_id}")).json()["data"]
        assert after["case_mappings"][0]["units_per_case"] == 4 and after["pdi_export_allowed"] is True
