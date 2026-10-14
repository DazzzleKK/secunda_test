"""Stateless domain services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from payments.domain.value_objects import Money


def build_request_fingerprint(
    money: Money,
    description: str,
    metadata: Mapping[str, Any],
    webhook_url: str,
) -> str:
    """Build a sha256 fingerprint of the request fields that must stay unchanged.

    The payload is canonicalised (sorted keys, no insignificant whitespace) so that
    key order inside `metadata` does not change the fingerprint. The result is used
    to tell an idempotent replay from a conflicting reuse of the same key.
    """
    canonical = json.dumps(
        {
            "amount": money.as_string(),
            "currency": money.currency.value,
            "description": description,
            "metadata": dict(metadata),
            "webhook_url": webhook_url,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
