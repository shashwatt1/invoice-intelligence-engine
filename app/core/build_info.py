"""
Build information — app/core/build_info.py

Which code is running: the git SHA the deployment was built from. Render sets
RENDER_GIT_COMMIT for every deploy; GIT_SHA overrides it anywhere else. Empty
when neither is set (e.g. local development) — never guessed.
"""

from __future__ import annotations

import os


def git_sha() -> str | None:
    return (os.environ.get("GIT_SHA") or os.environ.get("RENDER_GIT_COMMIT") or "").strip() or None
