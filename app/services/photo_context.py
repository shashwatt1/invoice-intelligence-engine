"""
Multi-photo OCR context — app/services/photo_context.py

One invoice, several overlapping photographs. Each photo is OCR'd on its
own; this module turns the per-photo results into the ONE text the model
reads, with each photo's text under a clearly labelled section so the
model can see where photos overlap and return each physical row once.

    --- PHOTO 1 of 3 (IMG_001.jpg) ---
    ...text of photo 1...

    --- PHOTO 2 of 3 (IMG_002.jpg) ---
    ...

Deliberately no row reconstruction, no bounding boxes, no table grid:
the model remains responsible for reading columnar / interleaved OCR and
for reconciling the overlap. A single photo produces its text unchanged —
no header — so every single-file run reads exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.ocr.base import OCRResult

PHOTO_HEADER = "--- PHOTO {k} of {n} ({name}) ---"


@dataclass(frozen=True)
class PhotoOCR:
    """One photo's OCR outcome, with the provenance the page row keeps."""

    page_number: int            # 1-based, operator order
    filename: str
    result: OCRResult


def photo_header(page_number: int, page_count: int, filename: str) -> str:
    return PHOTO_HEADER.format(k=page_number, n=page_count, name=filename)


def combine_photos(photos: list[PhotoOCR]) -> OCRResult:
    """
    The combined OCRResult the structuring stage receives.

    - one photo: its OCRResult, text untouched;
    - several: page-separated text, confidence weighted by each photo's
      text length (a near-empty photo should not drag the mean down),
      page_count summed, source_type 'ocr' if any photo needed OCR.
    """
    if not photos:
        raise ValueError("combine_photos needs at least one photo.")
    photos = sorted(photos, key=lambda p: p.page_number)
    if len(photos) == 1:
        return photos[0].result

    n = len(photos)
    sections = [
        f"{photo_header(p.page_number, n, p.filename)}\n{p.result.full_text.strip()}"
        for p in photos
    ]
    weights = [max(len(p.result.full_text.strip()), 1) for p in photos]
    mean_confidence = (
        sum(p.result.mean_confidence * w for p, w in zip(photos, weights, strict=True)) / sum(weights)
    )
    return OCRResult(
        full_text="\n\n".join(sections),
        source_type="ocr" if any(p.result.source_type == "ocr" for p in photos) else "digital_pdf",
        tokens=[t for p in photos for t in p.result.tokens],
        page_count=sum(p.result.page_count or 1 for p in photos),
        mean_confidence=round(mean_confidence, 4),
        duration_ms=sum(p.result.duration_ms for p in photos),
    )
