"""
tests/test_vendor_master.py — the governed Vendor Master, on the existing vendors table.

A vendor's id is its canonical identity. What invoices printed stays exactly
as printed. A vendor becomes confirmed only when a MANAGER or ADMIN confirms
it — naming the canonical vendor and saying why — and who decided is the
authenticated session. Nothing merges, re-keys or blocks processing.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.exceptions import ValidationError
from app.models.user import User, UserRole
from app.models.vendor import (
    DECISION_CONFIRM,
    DECISION_REOPEN,
    VENDOR_CONFIRMED,
    VENDOR_UNRESOLVED,
    Vendor,
)
from app.services import vendor_identity_service as service

ROOT = Path(__file__).resolve().parent.parent
BASIS = "Remit-to name and tax id match the distributor's statement."


def vendor(name="TESTANI DISTRIBUTORS INC.", status=VENDOR_UNRESOLVED, display_name=None, tax_id="12-3456789"):
    return Vendor(id=uuid.uuid4(), name=name, tax_id=tax_id, address="1 Mill Rd", phone=None, email=None,
                  identity_status=status, display_name=display_name)


class FakeRepository:
    def __init__(self, *vendors):
        self.vendors = {v.id: v for v in vendors}
        self.reviews = []

    async def get(self, vendor_id):
        return self.vendors.get(vendor_id)

    async def observed_names(self, vendor_id):
        return [{"name": "TESTANI DISTRIBUTORS INC.", "invoices": 3, "first_seen_at": None, "last_seen_at": None},
                {"name": "Testani Distributors", "invoices": 1, "first_seen_at": None, "last_seen_at": None}]

    async def observed_tax_ids(self, vendor_id):
        return [{"tax_id": "12-3456789", "invoices": 4}]

    async def confirmed_with_name(self, display_name, *, excluding):
        return next((v for v in self.vendors.values() if v.id != excluding and v.identity_status == VENDOR_CONFIRMED
                     and (v.display_name or "").lower() == display_name.lower()), None)

    async def add_review(self, review):
        self.reviews.append(review)
        return review


@pytest.fixture
def repo(monkeypatch):
    def bind(*vendors):
        repository = FakeRepository(*vendors)
        monkeypatch.setattr(service, "VendorRepository", lambda _s: repository)
        return repository
    return bind


# ---- the model ------------------------------------------------------------

class TestTheVendorRecord:
    def test_every_vendor_starts_unresolved_including_those_extraction_creates(self):
        column = Vendor.__table__.c.identity_status
        assert column.default.arg == VENDOR_UNRESOLVED and column.server_default.arg == VENDOR_UNRESOLVED
        assert not column.nullable

    def test_a_person_sees_the_confirmed_name_otherwise_the_observed_one(self):
        assert vendor().label == "TESTANI DISTRIBUTORS INC."
        assert vendor(status=VENDOR_CONFIRMED, display_name="Rocco J. Testani").label == "Rocco J. Testani"

    def test_the_pipeline_never_decides_vendor_identity(self):
        for path in ("app/services/persistence_service.py", "app/services/pipeline_service.py",
                     "app/services/reprocess_service.py", "app/repositories/vendor_repository.py"):
            source = (ROOT / path).read_text()
            assert "vendor_identity_service" not in source and "VENDOR_CONFIRMED)" not in source, path
        # get_or_create still matches exactly as before and never sets a canonical name or status.
        get_or_create = (ROOT / "app/repositories/vendor_repository.py").read_text().split("def _filtered")[0]
        assert "display_name" not in get_or_create and "identity_status" not in get_or_create


# ---- confirming -----------------------------------------------------------

class TestConfirm:
    @pytest.mark.parametrize(("display_name", "basis", "field"), [
        (None, BASIS, "display_name"), ("  ", BASIS, "display_name"),
        ("Rocco J. Testani", None, "basis"), ("Rocco J. Testani", "   ", "basis"),
    ])
    async def test_a_confirmation_needs_a_canonical_name_and_a_basis(self, repo, display_name, basis, field):
        v = vendor()
        repository = repo(v)
        with pytest.raises(ValidationError) as refused:
            await service.confirm(None, v.id, display_name=display_name, basis=basis, reviewer="barj")
        assert refused.value.detail["field"] == field
        assert v.identity_status == VENDOR_UNRESOLVED and v.display_name is None and repository.reviews == []

    async def test_confirming_records_who_what_why_and_the_evidence_and_keeps_the_observed_name(self, repo):
        v = vendor()
        account = uuid.uuid4()
        repository = repo(v)
        outcome = await service.confirm(None, v.id, display_name="  Rocco J. Testani ", basis=f"  {BASIS} ",
                                        reviewer="barj", reviewer_user_id=account, reviewer_role="MANAGER")
        assert (outcome.previous_status, outcome.new_status, outcome.display_name) == (
            VENDOR_UNRESOLVED, VENDOR_CONFIRMED, "Rocco J. Testani")
        assert (v.identity_status, v.display_name, v.name) == (
            VENDOR_CONFIRMED, "Rocco J. Testani", "TESTANI DISTRIBUTORS INC.")
        [entry] = repository.reviews
        assert (entry.decision, entry.previous_status, entry.new_status, entry.new_display_name) == (
            DECISION_CONFIRM, VENDOR_UNRESOLVED, VENDOR_CONFIRMED, "Rocco J. Testani")
        assert (entry.reviewer, entry.reviewer_user_id, entry.reviewer_role, entry.basis) == (
            "barj", account, "MANAGER", BASIS)
        assert entry.evidence_considered["observed_names"][1]["name"] == "Testani Distributors"
        assert entry.evidence_considered["invoices"] == 4

    async def test_a_repeated_confirmation_is_a_no_op_and_a_different_name_needs_a_reopen(self, repo):
        v = vendor(status=VENDOR_CONFIRMED, display_name="Rocco J. Testani")
        repository = repo(v)
        outcome = await service.confirm(None, v.id, display_name="Rocco J. Testani", basis=BASIS, reviewer="b")
        assert outcome.new_status == VENDOR_CONFIRMED and repository.reviews == []
        with pytest.raises(ValidationError, match="Reopen it"):
            await service.confirm(None, v.id, display_name="Testani Inc", basis=BASIS, reviewer="b")
        assert v.display_name == "Rocco J. Testani"

    async def test_two_vendors_are_never_confirmed_under_one_canonical_name(self, repo):
        existing = vendor(name="TESTANI", status=VENDOR_CONFIRMED, display_name="Rocco J. Testani")
        duplicate = vendor(name="TESTANI DIST", tax_id=None)
        repository = repo(existing, duplicate)
        with pytest.raises(ValidationError) as refused:
            await service.confirm(None, duplicate.id, display_name="rocco j. testani", basis=BASIS, reviewer="b")
        assert refused.value.detail["reason"] == "name_taken"
        assert duplicate.identity_status == VENDOR_UNRESOLVED and repository.reviews == []


class TestReopen:
    async def test_reopening_returns_to_unresolved_and_keeps_the_confirmation_in_history(self, repo):
        v = vendor()
        repository = repo(v)
        await service.confirm(None, v.id, display_name="Rocco J. Testani", basis=BASIS, reviewer="barj")
        with pytest.raises(ValidationError) as refused:
            await service.reopen(None, v.id, basis=" ", reviewer="prabh")
        assert refused.value.detail["field"] == "basis" and v.identity_status == VENDOR_CONFIRMED
        await service.reopen(None, v.id, basis="Two remit-to entities share this name.", reviewer="prabh",
                             reviewer_role="MANAGER")
        assert (v.identity_status, v.display_name, v.name) == (VENDOR_UNRESOLVED, None, "TESTANI DISTRIBUTORS INC.")
        assert [(r.decision, r.new_status) for r in repository.reviews] == [
            (DECISION_CONFIRM, VENDOR_CONFIRMED), (DECISION_REOPEN, VENDOR_UNRESOLVED)]
        assert repository.reviews[1].previous_display_name == "Rocco J. Testani"

    async def test_reopening_an_unresolved_vendor_changes_nothing(self, repo):
        v = vendor()
        repository = repo(v)
        await service.reopen(None, v.id, basis="x", reviewer="b")
        assert repository.reviews == [] and v.identity_status == VENDOR_UNRESOLVED


# ---- over the API ---------------------------------------------------------

class _Session:
    async def commit(self):
        return None


@pytest.fixture
def as_account(app, repo):
    from app.core.dependencies import require_authenticated_user
    from app.database.session import get_db

    async def fake_db():
        yield _Session()

    app.dependency_overrides[get_db] = fake_db

    def bind(role, *vendors):
        account = User(id=uuid.uuid4(), username=f"{role.lower()}-account", password_hash="x", role=role,
                       is_active=True)
        app.dependency_overrides[require_authenticated_user] = lambda: account
        return account, repo(*vendors)
    yield bind
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_authenticated_user, None)


class TestTheApiGovernance:
    @pytest.mark.parametrize("action", ["confirm", "reopen"])
    async def test_a_user_cannot_decide_vendor_identity(self, client, as_account, action):
        v = vendor(status=VENDOR_CONFIRMED if action == "reopen" else VENDOR_UNRESOLVED, display_name="X")
        _, repository = as_account(UserRole.USER.value, v)
        response = await client.post(f"/api/v1/vendors/{v.id}/{action}",
                                     json={"display_name": "Rocco J. Testani", "basis": BASIS})
        assert response.status_code == 403
        assert repository.reviews == []

    @pytest.mark.parametrize("role", [UserRole.MANAGER.value, UserRole.ADMIN.value])
    async def test_the_session_account_is_recorded_whatever_the_request_claims(self, client, as_account, role):
        v = vendor()
        account, repository = as_account(role, v)
        response = await client.post(f"/api/v1/vendors/{v.id}/confirm", json={
            "display_name": "Rocco J. Testani", "basis": BASIS,
            "reviewer": "someone-else", "confirmed_by": "someone-else", "reviewer_role": "ADMIN",
            "identity_status": "confirmed", "reviewer_user_id": str(uuid.uuid4()),
        })
        assert response.status_code == 200, response.text
        assert response.json()["data"]["new_status"] == VENDOR_CONFIRMED
        entry = repository.reviews[0]
        assert (entry.reviewer, entry.reviewer_user_id, entry.reviewer_role) == (account.username, account.id, role)

    async def test_a_confirmation_without_a_basis_is_refused_over_the_api(self, client, as_account):
        v = vendor()
        _, repository = as_account(UserRole.MANAGER.value, v)
        response = await client.post(f"/api/v1/vendors/{v.id}/confirm", json={"display_name": "Rocco J. Testani"})
        assert response.status_code == 422
        assert response.json()["error"]["detail"]["field"] == "basis"
        assert repository.reviews == [] and v.identity_status == VENDOR_UNRESOLVED


class TestTheApiReads:
    async def test_the_list_and_detail_show_canonical_identity_beside_the_observed_evidence(
        self, app, client, monkeypatch,
    ):
        import app.api.v1.vendors as module
        from app.core.dependencies import require_authenticated_user
        from app.database.session import get_db
        from app.models.store import Store, StoreIdentifier

        confirmed = vendor(status=VENDOR_CONFIRMED, display_name="Rocco J. Testani")
        seen = {}
        store = Store(id=uuid.uuid4(), display_name="PB Wolf", identity_status="unresolved",
                      identifiers=[StoreIdentifier(source_system="cstorepro", identifier_type="directory_name",
                                                   identifier_value="PB Wolf")])
        invoice = SimpleNamespace(id=uuid.uuid4(), document_id=uuid.uuid4(), invoice_number="3376587",
                                  invoice_date=date(2026, 9, 12), vendor_name="Testani Distributors",
                                  grand_total=Decimal("412.50"), store_id=store.id)
        when = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
        review = SimpleNamespace(decision=DECISION_CONFIRM, previous_status=VENDOR_UNRESOLVED,
                                 new_status=VENDOR_CONFIRMED, previous_display_name=None,
                                 new_display_name="Rocco J. Testani", reviewer="barj", reviewer_role="MANAGER",
                                 basis=BASIS, created_at=when)

        class Repository(FakeRepository):
            async def list_page(self, **kwargs):
                seen.update(kwargs)
                return [confirmed]

            async def count(self, **_):
                return 1

            async def invoice_stats(self, ids):
                return {confirmed.id: {"invoices": 4, "observed_names": 2, "last_seen_at": when}}

            async def recent_invoices(self, _id, limit=25):
                return [invoice]

            async def history(self, _id):
                return [review]

            async def discrepancies(self, _id):
                return [{"recorded_at": when, "invoice_id": invoice.id, "invoice_number": "3376587",
                         "observed_vendor_name": "TESTANI BROS", "observed_vendor_tax_id": None}]

        class Stores:
            def __init__(self, _session):
                pass

            async def labels(self, ids):
                return {store.id: store}

        async def no_db():
            yield None

        monkeypatch.setattr(module, "VendorRepository", lambda _s: Repository(confirmed))
        monkeypatch.setattr(module, "StoreRepository", Stores)
        app.dependency_overrides[get_db] = no_db
        app.dependency_overrides[require_authenticated_user] = lambda: User(
            id=uuid.uuid4(), username="vivek", password_hash="x", role="USER", is_active=True)
        try:
            listing = (await client.get("/api/v1/vendors",
                                        params={"identity_status": "confirmed", "search": " testani "})).json()
            detail = (await client.get(f"/api/v1/vendors/{confirmed.id}")).json()["data"]
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(require_authenticated_user, None)

        assert seen == {"page": 1, "page_size": 50, "identity_status": "confirmed", "search": "testani"}
        row = listing["items"][0]
        assert (row["label"], row["name"], row["identity_status"], row["invoices"], row["observed_names"]) == (
            "Rocco J. Testani", "TESTANI DISTRIBUTORS INC.", "confirmed", 4, 2)
        assert [n["name"] for n in detail["observed_name_list"]] == ["TESTANI DISTRIBUTORS INC.", "Testani Distributors"]
        assert detail["observed_tax_ids"] == [{"tax_id": "12-3456789", "invoices": 4}]
        [ref] = detail["recent_invoices"]
        assert (ref["invoice_number"], ref["observed_vendor_name"], ref["store"]["label"]) == (
            "3376587", "Testani Distributors", "PB Wolf (identity unconfirmed)")
        assert (detail["history"][0]["reviewer"], detail["history"][0]["reviewer_role"]) == ("barj", "MANAGER")
        [discrepancy] = detail["discrepancies"]
        assert (discrepancy["invoice_number"], discrepancy["observed_vendor_name"]) == ("3376587", "TESTANI BROS")


# ---- invoices and exports -------------------------------------------------

class TestInvoicesAreUntouched:
    def test_invoice_detail_shows_the_canonical_name_beside_the_observed_one(self):
        from app.schemas.processing import VendorData

        data = VendorData.model_validate(vendor(status=VENDOR_CONFIRMED, display_name="Rocco J. Testani"),
                                         from_attributes=True)
        assert (data.name, data.display_name, data.identity_status) == (
            "TESTANI DISTRIBUTORS INC.", "Rocco J. Testani", VENDOR_CONFIRMED)

    def test_exports_are_identical_whether_or_not_the_vendor_is_confirmed(self):
        from app.services.export_service import build_export_payload, build_items_csv, build_txt
        from tests.test_export_service import make_invoice

        invoice = make_invoice()
        before = (build_export_payload(invoice), build_txt(invoice), build_items_csv(invoice))
        invoice.vendor.identity_status = VENDOR_CONFIRMED
        invoice.vendor.display_name = "Acme Canonical Holdings"
        after = (build_export_payload(invoice), build_txt(invoice), build_items_csv(invoice))
        assert after == before
        assert after[0]["vendor"]["name"] == "Acme Distribution Co"


class TestTheMigration:
    def test_0027_is_additive_and_follows_the_deployed_head(self):
        path = ROOT / "alembic/versions/20260930_0027_vendor_identity.py"
        spec = importlib.util.spec_from_file_location("m0027", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert (module.revision, module.down_revision) == ("0027", "0026")
        upgrade = path.read_text().split("def upgrade")[1].split("def downgrade")[0]
        for destructive in ("drop_", "DELETE", "UPDATE", "execute("):
            assert destructive not in upgrade, destructive
        assert "server_default=sa.text(\"'unresolved'\")" in upgrade
