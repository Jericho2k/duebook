"""HMAC-SHA256 signatures over raw request bodies, in both directions.

A helpdesk webhook proves it is the helpdesk by signing what it sends, and
duebook proves the same to whatever receives its alerts. One scheme serves
both:

    X-Duebook-Signature: sha256=<hex of HMAC-SHA256(secret, raw body)>

The signature covers the bytes on the wire, not a re-serialisation of them:
two JSON encoders rarely agree byte for byte, so verifying a re-encoded body
would reject honest senders. Comparison is constant-time, so the check does not
leak how much of a forged signature was right.
"""

from __future__ import annotations

import hashlib
import hmac

HEADER = "X-Duebook-Signature"
PREFIX = "sha256="


def sign(secret: str, body: bytes) -> str:
    """The header value for ``body``."""
    return PREFIX + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str | None) -> bool:
    """True when ``header`` is the signature of ``body`` under ``secret``."""
    if not header:
        return False
    # Bytes, not str: compare_digest refuses non-ASCII strings, and a header
    # holds whatever the sender put there.
    return hmac.compare_digest(sign(secret, body).encode(), header.strip().encode("utf-8"))
