"""HMAC signature of webhook bodies.

The receiver can verify that a webhook really came from this service and that the
body was not modified in flight: `HMAC-SHA256(secret, raw_body)` in hex.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final

SIGNATURE_HEADER: Final[str] = "X-Webhook-Signature"
REQUEST_ID_HEADER: Final[str] = "X-Webhook-Id"
ATTEMPT_HEADER: Final[str] = "X-Webhook-Attempt"


def sign_payload(secret: str, body: bytes) -> str:
    """Return the hex HMAC-SHA256 of the request body."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """Check a signature in constant time (used by tests and the demo sink)."""
    return hmac.compare_digest(sign_payload(secret, body), signature)


__all__ = [
    "ATTEMPT_HEADER",
    "REQUEST_ID_HEADER",
    "SIGNATURE_HEADER",
    "sign_payload",
    "verify_signature",
]
