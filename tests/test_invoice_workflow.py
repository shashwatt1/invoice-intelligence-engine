"""
tests/test_invoice_workflow.py — invoice reference link and Retailed status (pure parts).

The database end (permissions, the ledger, staleness after changes, locking,
no inventory or mapping side effects) is tests/integration/test_invoice_workflow_db.py.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.exceptions import ValidationError
from app.services.invoice_workflow_service import (
    MAX_REFERENCE_URL,
    STATUS_NOT_RETAILED,
    STATUS_RETAILED,
    STATUS_RETAILED_STALE,
    pdi_sha256,
    status_for,
    validate_reference_url,
)


class TestReferenceUrl:
    @pytest.mark.parametrize("url", [
        "https://chat.whatsapp.com/abc123",
        "https://drive.google.com/file/d/xyz/view?usp=sharing",
        "https://teams.microsoft.com/l/message/19:abc@thread.v2/1700000000000",
        "  https://example.com/invoice/50185  ",
    ])
    def test_any_https_host_is_accepted(self, url):
        assert validate_reference_url(url) == url.strip()

    @pytest.mark.parametrize("given, stored", [
        ("HTTPS://Example.com/Path?Q=1", "https://Example.com/Path?Q=1"),
        ("Https://example.com/a", "https://example.com/a"),
        ("hTtPs://example.com", "https://example.com"),
    ])
    def test_the_scheme_is_stored_in_lower_case_matching_the_database_check(self, given, stored):
        assert validate_reference_url(given) == stored
        assert stored.startswith("https://")             # what ck_invoices_reference_url_https requires

    @pytest.mark.parametrize("url, message", [
        ("http://example.com/x", "Only https:// links"),
        ("javascript:alert(1)", "Only https:// links"),
        ("data:text/html,<script>alert(1)</script>", "Only https:// links"),
        ("ftp://example.com/x", "Only https:// links"),
        ("//example.com/x", "Only https:// links"),
        ("example.com/x", "Only https:// links"),
        ("https:///path-only", "has no host"),
        ("https://user:secret@example.com/x", "username or password"),
        ("https://example.com/a b", "spaces or control characters"),
        ("https://example.com/\x00", "spaces or control characters"),
        ("https://example.com/" + "a" * MAX_REFERENCE_URL, "longer than"),
    ])
    def test_unsafe_or_malformed_links_are_refused(self, url, message):
        with pytest.raises(ValidationError, match=message):
            validate_reference_url(url)

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_clears_the_link(self, value):
        assert validate_reference_url(value) is None


class TestRetailedStatus:
    def _invoice(self, sha=None):
        return SimpleNamespace(retailed_at=datetime.now(UTC) if sha else None, retailed_pdi_sha256=sha)

    def test_not_marked(self):
        assert status_for(self._invoice(), "abc") == STATUS_NOT_RETAILED

    def test_current_only_while_the_file_is_unchanged(self):
        assert status_for(self._invoice("abc"), "abc") == STATUS_RETAILED
        assert status_for(self._invoice("abc"), "def") == STATUS_RETAILED_STALE
        assert status_for(self._invoice("abc"), None) == STATUS_RETAILED_STALE   # cannot be exported now

    def test_the_hash_is_the_ledgers(self):
        content = "H00000001...\r\nB0123...\r\n"
        assert pdi_sha256(content) == hashlib.sha256(content.encode("utf-8")).hexdigest()
