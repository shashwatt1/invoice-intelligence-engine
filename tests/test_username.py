"""
tests/test_username.py — username normalization and validation (no DB).

app.models.user.normalize_username/validate_username have no database
dependency, so the exact identity rule ("Shashwatt1", "shashwatt1" and
"SHASHWATT1" are the same account) is proven directly here. DB-backed
uniqueness enforcement (the functional index, duplicate rejection at
the API) is covered in tests/integration/test_auth.py.
"""

from __future__ import annotations

import pytest

from app.models.user import InvalidUsernameError, normalize_username, validate_username


class TestNormalizeUsername:
    """Login-side normalization: strip + lowercase only, never raises."""

    def test_mixed_case_variants_normalize_to_the_same_value(self):
        assert normalize_username("Shashwatt1") == normalize_username("shashwatt1") == normalize_username("SHASHWATT1")

    def test_strips_surrounding_whitespace(self):
        assert normalize_username("  vivek  ") == "vivek"

    def test_never_raises_even_on_garbage_input(self):
        # Login must fail closed as "incorrect username or password", not
        # as a distinguishable validation error — normalize_username is
        # deliberately permissive; the lookup that follows just won't match.
        assert normalize_username("") == ""
        assert normalize_username("!!!not-a-valid-name!!!") == "!!!not-a-valid-name!!!"


class TestValidateUsername:
    """Account-creation-side validation: strict format, raises on violation."""

    def test_accepts_and_normalizes_a_well_formed_username(self):
        assert validate_username("Barj") == "barj"
        assert validate_username("prabh") == "prabh"
        assert validate_username("shashwatt1") == "shashwatt1"

    def test_accepts_dots_underscores_and_hyphens_in_the_middle(self):
        assert validate_username("data.team_1") == "data.team_1"
        assert validate_username("a-b-c") == "a-b-c"

    def test_rejects_too_short(self):
        with pytest.raises(InvalidUsernameError):
            validate_username("ab")

    def test_rejects_too_long(self):
        with pytest.raises(InvalidUsernameError):
            validate_username("a" * 65)

    def test_rejects_email_like_input(self):
        with pytest.raises(InvalidUsernameError):
            validate_username("someone@example.com")

    def test_rejects_spaces(self):
        with pytest.raises(InvalidUsernameError):
            validate_username("bad user")

    def test_rejects_leading_or_trailing_separator(self):
        with pytest.raises(InvalidUsernameError):
            validate_username(".vivek")
        with pytest.raises(InvalidUsernameError):
            validate_username("vivek-")

    def test_rejects_empty_string(self):
        with pytest.raises(InvalidUsernameError):
            validate_username("")

    def test_normalizes_before_validating(self):
        assert validate_username("  ShashWatt1  ") == "shashwatt1"
