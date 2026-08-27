"""ULIDs — the ID format for every doc in the system (specs README, global conventions).

48-bit millisecond timestamp + 80 bits of randomness, Crockford base32, 26 chars. They sort
lexicographically by creation time, which is why Firestore doc IDs use them (and why media
IDs are *client*-generated: the phone mints the ID before the bytes exist, so upload intent
and object path agree without a round trip).

Hand-rolled rather than a dependency: it is 30 lines and every service imports it.
"""

from __future__ import annotations

import os
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O, U
_DECODE = {c: i for i, c in enumerate(ALPHABET)}
ULID_LENGTH = 26


def new_ulid(when_ms: int | None = None) -> str:
    """Generate a ULID. `when_ms` lets seed scripts synthesise ordered historical IDs."""
    ts = when_ms if when_ms is not None else int(time.time() * 1000)
    value = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(ULID_LENGTH):
        out.append(ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def is_ulid(value: object) -> bool:
    """Strict validation — client-supplied IDs become GCS object paths and Firestore doc IDs."""
    if not isinstance(value, str) or len(value) != ULID_LENGTH:
        return False
    if not all(c in _DECODE for c in value):
        return False
    # First char encodes the top 5 bits of a 48-bit timestamp; > '7' would overflow.
    return value[0] <= "7"


def timestamp_ms(value: str) -> int:
    """Extract the embedded creation time. Raises ValueError on a malformed ULID."""
    if not is_ulid(value):
        raise ValueError(f"not a ULID: {value!r}")
    total = 0
    for c in value:
        total = (total << 5) | _DECODE[c]
    return total >> 80
