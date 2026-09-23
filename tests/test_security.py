"""
tests/test_security.py — password hashing and JWT session tokens (no DB).

app.core.security has no database dependency, so its guarantees —
hashing never stores plaintext, a token round-trips to the right user
id, role rank orders the three roles correctly — are proven directly.
DB-backed behavior (login, account creation, role/is_active enforcement)
is covered in tests/integration/test_auth.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from jose import jwt

from app.core.config import get_settings
from app.core.security import (
    ACCESS_TOKEN_COOKIE_NAME,
    ALGORITHM,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.user import ROLE_RANK, UserRole


class TestPasswordHashing:
    def test_hash_is_never_the_plaintext(self):
        assert hash_password("correct horse battery staple") != "correct horse battery staple"

    def test_hash_looks_like_bcrypt(self):
        assert hash_password("hunter2").startswith("$2b$")

    def test_same_password_hashes_differently_each_time(self):
        # A fresh salt per call — two hashes of the same password must
        # never be byte-identical, or a leaked hash table would reveal
        # which accounts share a password.
        assert hash_password("hunter2") != hash_password("hunter2")

    def test_verify_accepts_the_correct_password(self):
        h = hash_password("hunter2")
        assert verify_password("hunter2", h) is True

    def test_verify_rejects_the_wrong_password(self):
        h = hash_password("hunter2")
        assert verify_password("wrong", h) is False

    def test_verify_fails_closed_on_a_malformed_hash(self):
        assert verify_password("hunter2", "not-a-real-bcrypt-hash") is False


class TestAccessToken:
    def test_token_round_trips_to_the_same_user_id(self):
        user_id = uuid.uuid4()
        token = create_access_token(user_id)
        assert decode_access_token(token) == user_id

    def test_cookie_name_is_stable(self):
        assert ACCESS_TOKEN_COOKIE_NAME == "access_token"

    def test_garbage_token_decodes_to_none(self):
        assert decode_access_token("not-a-jwt-at-all") is None

    def test_expired_token_decodes_to_none(self):
        settings = get_settings()
        expired = jwt.encode(
            {"sub": str(uuid.uuid4()), "iat": datetime.now(UTC) - timedelta(hours=2),
             "exp": datetime.now(UTC) - timedelta(hours=1)},
            settings.secret_key, algorithm=ALGORITHM,
        )
        assert decode_access_token(expired) is None

    def test_token_signed_with_a_different_secret_is_rejected(self):
        forged = jwt.encode(
            {"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(hours=1)},
            "a-completely-different-secret", algorithm=ALGORITHM,
        )
        assert decode_access_token(forged) is None

    def test_a_token_with_no_sub_claim_decodes_to_none(self):
        settings = get_settings()
        no_sub = jwt.encode(
            {"exp": datetime.now(UTC) + timedelta(hours=1)}, settings.secret_key, algorithm=ALGORITHM,
        )
        assert decode_access_token(no_sub) is None

    def test_the_token_never_carries_a_role_claim(self):
        # Role must be re-read from the database every request, never
        # cached in the token — decode the raw JWT and check its claims.
        settings = get_settings()
        token = create_access_token(uuid.uuid4())
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        assert "role" not in payload
        assert set(payload.keys()) == {"sub", "iat", "exp"}


class TestRoleRank:
    def test_admin_outranks_manager_outranks_user(self):
        assert ROLE_RANK[UserRole.ADMIN.value] > ROLE_RANK[UserRole.MANAGER.value] > ROLE_RANK[UserRole.USER.value]

    def test_exactly_three_roles_exist(self):
        assert {r.value for r in UserRole} == {"USER", "MANAGER", "ADMIN"}
        assert "DEVELOPER" not in {r.value for r in UserRole}
