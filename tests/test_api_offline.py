"""
tests/test_api_offline.py — API tests that need no database.

Upload validation runs before any DB access, so rejection paths and the
OpenAPI surface are verifiable in the offline suite.
"""

from __future__ import annotations


class TestProcessValidation:
    async def test_unsupported_file_type_is_415(self, client):
        response = await client.post(
            "/api/v1/invoices/process",
            files={"file": ("notes.txt", b"x" * 2048, "text/plain")},
            data={"store_number": "47708760"},
        )
        assert response.status_code == 415
        body = response.json()
        assert body["success"] is False
        assert body["error"]["error_code"] == "ERR_UNSUPPORTED_FORMAT"

    async def test_empty_file_is_422(self, client):
        response = await client.post(
            "/api/v1/invoices/process",
            files={"file": ("tiny.pdf", b"%PDF", "application/pdf")},
            data={"store_number": "47708760"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["error_code"] == "ERR_EMPTY_FILE"

    async def test_missing_file_is_422(self, client):
        response = await client.post("/api/v1/invoices/process", data={"store_number": "47708760"})
        assert response.status_code == 422

    async def test_missing_store_is_422_not_a_default(self, client):
        # There is no global store to fall back on: an invoice without a
        # store would meet the wrong reference data and the wrong mappings.
        response = await client.post(
            "/api/v1/invoices/process",
            files={"file": ("ok.pdf", b"%PDF-1.4 " + b"x" * 2048, "application/pdf")},
        )
        assert response.status_code == 422
        assert any(e["loc"][-1] == "store_number" for e in response.json()["error"]["detail"])

    async def test_a_non_numeric_store_is_422(self, client):
        response = await client.post(
            "/api/v1/invoices/process",
            files={"file": ("ok.pdf", b"%PDF-1.4 " + b"x" * 2048, "application/pdf")},
            data={"store_number": "store-A"},
        )
        assert response.status_code == 422


class TestOpenAPISurface:
    async def test_new_endpoints_are_documented(self, client):
        spec = (await client.get("/openapi.json")).json()
        paths = spec["paths"]
        assert "/api/v1/invoices/process" in paths
        assert "/api/v1/invoices" in paths
        assert "/api/v1/invoices/{invoice_id}" in paths
        assert "/api/v1/documents/{document_id}" in paths
        assert "/api/v1/dashboard/summary" in paths
        # Envelope reuse: process returns the standard APIResponse shape
        schema_ref = paths["/api/v1/invoices/process"]["post"]["responses"]["202"]
        assert "APIResponse" in str(schema_ref)
