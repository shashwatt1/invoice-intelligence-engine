"""
tests/test_reference_workbooks.py — which files an import run reads.

A reference folder collects copies ("… (1).xlsx", a colleague's upload).
The importers key rows by filename, so a copy is a second source saying
the same thing — it must be recognised by content and read once.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.reference_workbooks import discover_workbooks


def _write(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


class TestDirectoryMode:
    def test_only_item_sales_exports_are_taken(self, tmp_path):
        _write(tmp_path / "Item_Sales_Summary_x.xlsx", b"a")
        _write(tmp_path / "Mckinley-07-24_to_07-26.xlsx", b"b")
        _write(tmp_path / "Beer Inventory.xlsx", b"c")
        _write(tmp_path / "~$Item_Sales_Summary_x.xlsx", b"lock")
        found = discover_workbooks([tmp_path])
        assert [p.name for p in found.selected] == ["Item_Sales_Summary_x.xlsx"]
        assert found.duplicates == []

    def test_a_browser_copy_is_skipped_under_the_copys_name(self, tmp_path):
        canonical = _write(tmp_path / "Item_Sales_Summary_2026-09-14T15_45_30.014Z.xlsx", b"same bytes")
        copy = _write(tmp_path / "Item_Sales_Summary_2026-09-14T15_45_30.014Z (1).xlsx", b"same bytes")
        other = _write(tmp_path / "Item_Sales_Summary_2026-09-14T15_56_01.537Z.xlsx", b"different")
        found = discover_workbooks([tmp_path])
        assert found.selected == [canonical, other]          # canonical name survives
        assert found.duplicates == [(copy, canonical)]

    def test_different_content_with_similar_names_is_two_files(self, tmp_path):
        a = _write(tmp_path / "Item_Sales_Summary_a.xlsx", b"one")
        b = _write(tmp_path / "Item_Sales_Summary_b.xlsx", b"two")
        assert discover_workbooks([tmp_path]).selected == [a, b]


class TestExplicitFiles:
    def test_the_same_content_given_twice_is_read_once(self, tmp_path):
        a = _write(tmp_path / "one.xlsx", b"x")
        b = _write(tmp_path / "two.xlsx", b"x")
        found = discover_workbooks([a, b])
        assert found.selected == [a] and found.duplicates == [(b, a)]

    def test_the_same_path_given_twice_is_read_once(self, tmp_path):
        a = _write(tmp_path / "one.xlsx", b"x")
        assert discover_workbooks([a, str(a)]).selected == [a]

    def test_a_missing_target_is_an_error(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            discover_workbooks([tmp_path / "nope.xlsx"])
