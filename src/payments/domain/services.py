"""Stateless domain services."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Final

from payments.domain.exceptions import InvalidMetadataError
from payments.domain.value_objects import Money

MAX_METADATA_BYTES: Final[int] = 8 * 1024


def canonicalize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return a frozen, JSON-normalised copy of the metadata.

    The copy is what the entity stores and what the fingerprint is computed from,
    so a later mutation of the caller's object can neither change the stored
    payload nor invalidate the fingerprint. Size and serialisability are checked
    here, in the domain, and not only in the API schema.
    """
    if not isinstance(metadata, Mapping):
        raise InvalidMetadataError(reason="metadata must be a JSON object")
    try:
        encoded = json.dumps(dict(metadata), sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise InvalidMetadataError(
            reason="metadata must be JSON serialisable",
        ) from exc
    size = len(encoded.encode("utf-8"))
    if size > MAX_METADATA_BYTES:
        raise InvalidMetadataError(
            size_bytes=size,
            limit_bytes=MAX_METADATA_BYTES,
        )
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise InvalidMetadataError(reason="metadata must be a JSON object")
    return decoded


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


__all__ = ["MAX_METADATA_BYTES", "build_request_fingerprint", "canonicalize_metadata"]
