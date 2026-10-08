"""
EDI export ledger — app/services/edi_export_ledger.py

Records a delivered PDI file exactly as it left the system (app/models/
edi_export.py). Called by the export endpoint after the file is built and
before it is returned; writes one row and never updates one.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.build_info import git_sha
from app.core.config import get_settings
from app.models.edi_export import FORMAT_PDI_FIXED_WIDTH, EdiExport
from app.services.product_master.commercial_resolution import CommercialResolution

# Bumped deliberately whenever the PDI byte contract (export_service) changes.
PDI_FORMAT_VERSION = "1"


def resolution_snapshot(resolution: CommercialResolution) -> dict[str, Any]:
    """Per item code, what supplied its units — enough to explain every B record later."""
    return {
        "enabled": resolution.enabled,
        "lines": {
            code: {"path": r.path, "units": r.units_per_case, "mapping_id": r.mapping_id,
                   "scope": r.scope_label, "legacy_units": r.legacy_units, "notes": list(r.notes)}
            for code, r in sorted(resolution.lines.items())
        },
    }


async def _alembic_revision(session: AsyncSession) -> str | None:
    try:
        rows = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    except Exception:  # noqa: BLE001 — the revision is context, never a reason to refuse an export
        return None
    return ",".join(sorted(rows)) or None


async def record_pdi_export(session: AsyncSession, invoice, content: str, resolution: CommercialResolution,
                            user) -> EdiExport:
    """Append the ledger row for one delivered PDI file. Flushed in the caller's transaction."""
    raw = content.encode("utf-8")
    previous = (await session.execute(
        select(func.max(EdiExport.export_number)).where(EdiExport.invoice_id == invoice.id)
    )).scalar()
    settings = get_settings()
    row = EdiExport(
        invoice_id=invoice.id, document_id=invoice.document_id, invoice_number=invoice.invoice_number,
        export_number=(previous or 0) + 1, format=FORMAT_PDI_FIXED_WIDTH, format_version=PDI_FORMAT_VERSION,
        exported_by_user_id=user.id, exported_by=user.username, exported_by_role=user.role,
        sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw), content=content,
        resolution_snapshot=resolution_snapshot(resolution),
        build={
            "git_sha": git_sha(),
            "alembic_revision": await _alembic_revision(session),
            "product_master_commercial_resolution": settings.product_master_commercial_resolution,
            "commercial_source_identity": settings.commercial_source_identity or None,
        },
    )
    session.add(row)
    await session.flush()
    return row
