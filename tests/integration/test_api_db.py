"""
tests/integration/test_api_db.py — End-to-end API tests against real Postgres.

Drives the actual ASGI app through httpx: upload → background pipeline →
status polling → invoice detail → history → dashboard. Extraction runs
for real (digital PDF via pdfplumber); only the LLM stage is faked.

Note on "background": with ASGITransport the response is returned after
FastAPI's background tasks complete, so polling observes the terminal
state deterministically. Live progress is exercised against the real
server in manual verification.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AIStructuringError
from app.services.pipeline_service import InvoiceProcessingPipeline
from tests.integration.conftest import requires_db
from tests.integration.fakes import FakeStructuring, extracted_invoice
from tests.pdf_builder import build_pdf

pytestmark = requires_db

# Digital PDF > 1 KB (upload minimum) with a realistic text layer.
INVOICE_PDF = build_pdf(
    ["INVOICE INV-001  Acme Corp  Total: 18.90  " + "reference padding " * 80]
)


@pytest_asyncio.fixture
async def api_client(app, db_engine, db_session, monkeypatch):
    """ASGI client wired to the test database and a fake-LLM pipeline."""
    from app.api.v1 import invoices as invoices_module
    from app.api.v1.invoices import get_pipeline
    from app.database.session import get_db

    factory = async_sessionmaker(bind=db_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    pipeline = InvoiceProcessingPipeline(structuring_service=FakeStructuring())

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_pipeline] = lambda: pipeline
    monkeypatch.setattr(invoices_module, "get_session_factory", lambda: factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


async def process_file(
    api_client, content: bytes = INVOICE_PDF, filename: str = "acme-invoice.pdf"
) -> dict:
    response = await api_client.post(
        "/api/v1/invoices/process",
        files={"file": (filename, content, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


class TestProcessAndStatus:
    async def test_process_returns_202_with_status_url(self, api_client):
        data = await process_file(api_client)
        assert data["status"] == "UPLOADED"
        assert data["status_url"] == f"/api/v1/documents/{data['document_id']}"

    async def test_status_reaches_completed_with_full_timeline(self, api_client):
        accepted = await process_file(api_client)

        response = await api_client.get(accepted["status_url"])
        assert response.status_code == 200
        status = response.json()["data"]

        assert status["status"] == "COMPLETED"
        assert status["is_terminal"] is True
        assert status["invoice_id"] is not None
        assert status["error"] is None
        assert status["source_type"] == "digital_pdf"
        assert [s["stage"] for s in status["stages"]] == [
            "UPLOAD", "TEXT_EXTRACTION", "AI_STRUCTURING", "VALIDATION", "PERSISTENCE",
        ]
        assert all(s["status"] == "SUCCESS" for s in status["stages"])

    async def test_duplicate_upload_returns_409(self, api_client):
        await process_file(api_client)
        response = await api_client.post(
            "/api/v1/invoices/process",
            files={"file": ("copy.pdf", INVOICE_PDF, "application/pdf")},
        )
        assert response.status_code == 409
        body = response.json()
        assert body["success"] is False
        assert body["error"]["error_code"] == "ERR_DUPLICATE_DOCUMENT"

    async def test_failed_pipeline_reports_error_in_status(self, api_client, app):
        from app.api.v1.invoices import get_pipeline

        failing = InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(error=AIStructuringError(message="model down"))
        )
        app.dependency_overrides[get_pipeline] = lambda: failing

        accepted = await process_file(api_client)
        status = (await api_client.get(accepted["status_url"])).json()["data"]

        assert status["status"] == "FAILED"
        assert status["is_terminal"] is True
        assert status["invoice_id"] is None
        assert status["error"]["stage"] == "AI_STRUCTURING"
        assert status["error"]["error_code"] == "ERR_AI_STRUCTURING_FAILED"

    async def test_unknown_document_is_404(self, api_client):
        response = await api_client.get(f"/api/v1/documents/{uuid.uuid4()}")
        assert response.status_code == 404
        assert response.json()["error"]["error_code"] == "ERR_NOT_FOUND"


class TestInvoiceDetail:
    async def test_detail_contains_everything_the_view_needs(self, api_client):
        accepted = await process_file(api_client)
        status = (await api_client.get(accepted["status_url"])).json()["data"]

        response = await api_client.get(f"/api/v1/invoices/{status['invoice_id']}")
        assert response.status_code == 200
        detail = response.json()["data"]

        assert detail["invoice_number"] == "INV-001"
        assert detail["grand_total"] == 18.9
        assert detail["currency"] == "USD"
        assert detail["status"] == "VALIDATED"
        assert detail["document_status"] == "COMPLETED"
        assert detail["composite_confidence"] >= 0.85
        assert detail["extraction_model"] == "gpt-4o-mini"

        assert detail["vendor"]["name"] == "Acme Corp"
        [item] = detail["line_items"]
        assert item["unit_price"] == 9.45  # derived by validation

        assert detail["validation_report"]["decision"] == "VALIDATED"
        assert detail["llm_metadata"]["total_tokens"] == 1450
        assert detail["llm_metadata"]["prompt_version"] == "v1"

        db = detail["database"]
        assert db["vendor_saved"] and db["invoice_saved"]
        assert db["items_saved"] == 1
        assert db["logs_saved"] == 5
        assert db["duplicate_check_passed"] is True
        assert db["processing_duration_ms"] >= 0

        assert "INVOICE INV-001" in detail["ocr_text"]
        assert detail["raw_extraction"]["id"] == "chatcmpl-fake"

    async def test_unknown_invoice_is_404(self, api_client):
        response = await api_client.get(f"/api/v1/invoices/{uuid.uuid4()}")
        assert response.status_code == 404


class TestHistoryAndDashboard:
    async def test_history_lists_search_and_filters(self, api_client, app):
        from app.api.v1.invoices import get_pipeline

        await process_file(api_client, filename="acme-january.pdf")
        other = InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(
                extracted_invoice(
                    vendor={"name": "Globex GmbH", "tax_id": "DE999"},
                    invoice_number="GLX-77",
                )
            )
        )
        app.dependency_overrides[get_pipeline] = lambda: other
        await process_file(
            api_client,
            content=build_pdf(["Globex invoice " + "pad " * 300]),
            filename="globex-february.pdf",
        )

        body = (await api_client.get("/api/v1/invoices")).json()
        assert body["total"] == 2
        assert {row["invoice_number"] for row in body["items"]} == {"INV-001", "GLX-77"}

        searched = (await api_client.get("/api/v1/invoices", params={"search": "globex"})).json()
        assert searched["total"] == 1
        assert searched["items"][0]["vendor_name"] == "Globex GmbH"

        filtered = (
            await api_client.get("/api/v1/invoices", params={"status": "COMPLETED"})
        ).json()
        assert filtered["total"] == 2

        paged = (
            await api_client.get("/api/v1/invoices", params={"page_size": 1, "page": 2})
        ).json()
        assert paged["total"] == 2 and len(paged["items"]) == 1

    async def test_dashboard_summary_aggregates(self, api_client, app):
        from app.api.v1.invoices import get_pipeline

        await process_file(api_client)
        failing = InvoiceProcessingPipeline(
            structuring_service=FakeStructuring(error=AIStructuringError(message="down"))
        )
        app.dependency_overrides[get_pipeline] = lambda: failing
        await process_file(
            api_client, content=build_pdf(["failing doc " + "pad " * 300]), filename="bad.pdf"
        )

        data = (await api_client.get("/api/v1/dashboard/summary")).json()["data"]
        assert data["total_documents"] == 2
        assert data["completed"] == 1
        assert data["failed"] == 1
        assert data["success_rate"] == pytest.approx(0.5)
        assert data["average_confidence"] >= 0.85
        assert data["total_tokens"] == 1450
        assert data["total_estimated_cost_usd"] == pytest.approx(0.00033)
        assert data["status_breakdown"] == {"COMPLETED": 1, "FAILED": 1}
        assert len(data["recent"]) == 2
        assert data["recent"][0]["filename"] == "bad.pdf"  # newest first
