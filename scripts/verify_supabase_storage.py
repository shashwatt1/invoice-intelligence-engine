#!/usr/bin/env python
"""
Pilot storage smoke test — scripts/verify_supabase_storage.py

    python scripts/verify_supabase_storage.py

Run this ONCE real pilot credentials exist (SUPABASE_URL,
SUPABASE_SERVICE_KEY, SUPABASE_BUCKET_NAME set — in the environment or
.env) to prove the configured Supabase Storage bucket actually accepts
writes/reads/deletes from this application, end to end, against the
real API. This is deliberately NOT part of the automated test suite —
tests/test_storage_service.py already proves the request shape offline
against a mocked transport; this script is the one step that needs a
real network call and real credentials, so it stays manual.

Writes a small throwaway object, reads it back, confirms the bytes
match, then deletes it and confirms the delete stuck. Exits non-zero
and prints exactly what failed if anything does — never claims success
it didn't observe.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import StorageError  # noqa: E402
from app.services.storage_service import get_storage_service  # noqa: E402

PROBE_CONTENT = b"invoice-intelligence pilot storage smoke test"


async def main() -> None:
    settings = get_settings()
    if settings.storage_backend != "supabase":
        print(
            f"STORAGE_BACKEND is {settings.storage_backend!r}, not 'supabase'. "
            "Set STORAGE_BACKEND=supabase (and SUPABASE_URL / SUPABASE_SERVICE_KEY / "
            "SUPABASE_BUCKET_NAME) before running this check.",
            file=sys.stderr,
        )
        sys.exit(1)

    probe_uuid = str(uuid.uuid4())
    print(f"Bucket:   {settings.supabase_bucket_name}")
    print(f"Project:  {settings.supabase_url}")
    print(f"Probe id: {probe_uuid}")

    storage = get_storage_service()

    try:
        print("1. save()  ...", end=" ", flush=True)
        key = await storage.save(PROBE_CONTENT, probe_uuid, "smoke-test.txt", organization_id="_smoke_test")
        print(f"OK — object key: {key}")

        print("2. read()  ...", end=" ", flush=True)
        content = await storage.read(key)
        if content != PROBE_CONTENT:
            print("FAILED — bytes read back do not match what was written.", file=sys.stderr)
            sys.exit(1)
        print("OK — bytes match")

        print("3. delete()...", end=" ", flush=True)
        await storage.delete(key)
        print("OK")

        print("4. confirm delete stuck ...", end=" ", flush=True)
        try:
            await storage.read(key)
        except StorageError:
            print("OK — object is gone")
        else:
            print("FAILED — object was still readable after delete().", file=sys.stderr)
            sys.exit(1)

    except StorageError as exc:
        print(f"FAILED — {exc.message} ({exc.detail})", file=sys.stderr)
        sys.exit(1)

    print("\nPASS — Supabase Storage is reachable and read/write/delete all work.")


if __name__ == "__main__":
    asyncio.run(main())
