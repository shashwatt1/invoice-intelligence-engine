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
        body = response.json()["error"]
        assert body["error_code"] == "ERR_VALIDATION_FAILED"
        assert "store_number is required" in body["message"]
        assert "no default store" in body["message"].lower()
        assert body["detail"] == {"field": "store_number", "reason": "missing"}

    async def test_a_blank_store_is_missing_not_a_store(self, client):
        response = await client.post(
            "/api/v1/invoices/process",
            files={"file": ("ok.pdf", b"%PDF-1.4 " + b"x" * 2048, "application/pdf")},
            data={"store_number": "   "},
        )
        assert response.status_code == 422
        assert response.json()["error"]["detail"]["reason"] == "missing"

    async def test_an_invalid_store_is_422_with_the_value_named(self, client):
        for bad in ("store-A", "4770 8760", "47708760x", "9" * 33):
            response = await client.post(
                "/api/v1/invoices/process",
                files={"file": ("ok.pdf", b"%PDF-1.4 " + b"x" * 2048, "application/pdf")},
                data={"store_number": bad},
            )
            assert response.status_code == 422, bad
            body = response.json()["error"]
            assert body["detail"] == {"field": "store_number", "reason": "invalid", "value": bad}
            assert "not a store number" in body["message"]

    async def test_the_store_is_checked_before_the_file_is_touched(self, client, monkeypatch):
        # A refused store must leave nothing behind: the upload service is
        # never reached, so no file is written and no document is created.
        from app.api.v1 import invoices as invoices_module

        called = []
        original = invoices_module.UploadService.handle_upload

        async def spy(self, file):
            called.append(file.filename)
            return await original(self, file)

        monkeypatch.setattr(invoices_module.UploadService, "handle_upload", spy)
        response = await client.post(
            "/api/v1/invoices/process",
            files={"file": ("ok.pdf", b"%PDF-1.4 " + b"x" * 2048, "application/pdf")},
        )
        assert response.status_code == 422
        assert called == []

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
