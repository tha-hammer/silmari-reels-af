"""Plan 3 — real-bucket ObjectStorage smoke test (@pytest.mark.integration).

NOT part of the default run. Requires a live REEL_BUCKET_* bucket. Fail-closed:
if REEL_BUCKET_NAME is set but the bucket is unreachable, the test ERRORS (red) —
it never skips-to-green (closure §4 rule 6). It only skips when no bucket is
configured at all (nothing to smoke).
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_real_bucket_put_exists_presign_roundtrip():
    if not os.getenv("REEL_BUCKET_NAME"):
        pytest.skip("no REEL_BUCKET_NAME configured — nothing to smoke")

    import urllib.request

    from storage import ObjectStorage

    store = ObjectStorage()  # real boto3 client from REEL_BUCKET_* env
    org = uuid.uuid4()
    payload = b"integration-smoke-bytes"
    ref = store.put(org, f"smoke/{uuid.uuid4().hex}.bin", payload)

    assert ref.startswith(f"{org}/")
    assert store.exists(ref) is True

    url = store.presigned_url(ref, ttl=120)
    with urllib.request.urlopen(url) as resp:  # noqa: S310 (trusted presigned URL)
        assert resp.read() == payload
