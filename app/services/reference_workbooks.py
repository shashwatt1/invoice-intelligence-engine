"""
Reference workbook discovery — app/services/reference_workbooks.py

Which files an import run should read, and which it should not.

A reference folder accumulates copies: a browser re-download lands as
"Item_Sales_Summary_… (1).xlsx", a colleague's upload has a different
name again. The importers key rows by filename, so a copy is not a
harmless no-op — it is a second, separately-named source saying the
same thing, which double-counts as agreement. Files are therefore
identified by content (SHA-256), and only the first of identical files
in a run is read.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

ITEM_SALES_GLOB = "Item_Sales*.xlsx"
_COPY_SUFFIX = re.compile(r" \(\d+\)\.xlsx$", re.IGNORECASE)


@dataclass(frozen=True)
class DiscoveredWorkbooks:
    selected: list[Path]
    # (skipped copy, the file it duplicates)
    duplicates: list[tuple[Path, Path]] = field(default_factory=list)


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_workbooks(targets: list[str | Path], *, pattern: str = ITEM_SALES_GLOB) -> DiscoveredWorkbooks:
    """
    Expand files and directories into the workbooks to import.

    A directory contributes only files matching `pattern` (the catalogue
    exports; Beer Inventory.xlsx and period exports live in the same
    folder and belong to other importers). Excel lock files (~$…) are
    skipped. Two paths with identical bytes count once, whatever they
    are called.
    """
    candidates: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            # A browser's "name (1).xlsx" copy sorts before "name.xlsx"
            # (space < dot); read the canonical name first so a duplicate
            # is skipped under the copy's name, not the original's.
            candidates.extend(sorted(
                (p for p in path.glob(pattern) if not p.name.startswith("~$")),
                key=lambda p: (bool(_COPY_SUFFIX.search(p.name)), p.name),
            ))
        elif path.is_file():
            candidates.append(path)
        else:
            raise FileNotFoundError(f"Not found: {target}")

    selected: list[Path] = []
    seen: dict[str, Path] = {}
    duplicates: list[tuple[Path, Path]] = []
    for path in candidates:
        if any(path.resolve() == s.resolve() for s in selected):
            continue                                  # the same path given twice
        digest = content_hash(path)
        if digest in seen:
            duplicates.append((path, seen[digest]))
            continue
        seen[digest] = path
        selected.append(path)
    return DiscoveredWorkbooks(selected=selected, duplicates=duplicates)
