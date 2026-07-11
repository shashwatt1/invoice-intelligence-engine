"""
tests/pdf_builder.py — Synthetic PDF fixture generator.

Builds small, structurally valid PDFs entirely in memory so extraction
tests need no binary fixture files and no PDF-writing dependency.

- build_pdf(["INVOICE #123"])      → one-page digital PDF with a text layer
- build_pdf([None])                → one-page "scanned-style" PDF (no text layer)
- build_pdf(["page 1", "page 2"])  → multi-page digital PDF

The generated files include a correct xref table with byte offsets, which
pdfminer (pdfplumber's parser) requires.
"""

from __future__ import annotations


def _escape(text: str) -> str:
    """Escape characters that are special inside a PDF literal string."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_pdf(page_texts: list[str | None]) -> bytes:
    """
    Build a valid single- or multi-page PDF.

    Args:
        page_texts: One entry per page. A string becomes an embedded text
            layer (digital PDF); None produces a blank page (mimics a
            scanned document with no text layer).
    """
    # Object layout: 1=Catalog, 2=Pages, 3=Font, then (Page, Contents) pairs.
    objects: list[bytes] = []
    n_pages = len(page_texts)

    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for i, text in enumerate(page_texts):
        contents_num = 4 + 2 * i + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {contents_num} 0 R >>"
            ).encode()
        )
        stream = (
            f"BT /F1 12 Tf 72 720 Td ({_escape(text)}) Tj ET".encode() if text else b""
        )
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_pos = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)
