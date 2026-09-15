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
        )
        assert response.status_code == 415
        body = response.json()
        assert body["success"] is False
        assert body["error"]["error_code"] == "ERR_UNSUPPORTED_FORMAT"

    async def test_empty_file_is_422(self, client):
        response = await client.post(
            "/api/v1/invoices/process",
            files={"file": ("tiny.pdf", b"%PDF", "application/pdf")},
        )
        assert response.status_code == 422
        assert response.json()["error"]["error_code"] == "ERR_EMPTY_FILE"

    async def test_missing_file_is_422(self, client):
        response = await client.post("/api/v1/invoices/process")
        assert response.status_code == 422

    async def test_a_store_id_that_is_not_a_store_id_is_refused_before_the_file_is_touched(
        self, client, monkeypatch
    ):
        # A source code, a name, a typo: none is a Store id. Refused with the
        # field named, and the upload service is never reached — nothing is
        # stored. (An unknown-but-well-formed id needs the store master and
        # is covered by the integration tests.)
        from app.api.v1 import invoices as invoices_module

        called = []
        original = invoices_module.UploadService.handle_upload

        async def spy(self, file):
            called.append(file.filename)
            return await original(self, file)

        monkeypatch.setattr(invoices_module.UploadService, "handle_upload", spy)
        for bad in ("47708760", "store-A", "not-a-uuid"):
            response = await client.post(
                "/api/v1/invoices/process",
                files={"file": ("ok.pdf", b"%PDF-1.4 " + b"x" * 2048, "application/pdf")},
                data={"store_id": bad},
            )
            assert response.status_code == 422, bad
            body = response.json()["error"]
            assert body["detail"] == {"field": "store_id", "reason": "invalid", "value": bad}
            assert "Choose a store from the directory" in body["message"]
        assert called == []


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
