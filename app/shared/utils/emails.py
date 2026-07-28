"""Email string normalization.

Single canonical form for any email that gets persisted or compared — owner
fields, login identities, notification recipients. Storing the same address in
different casings/whitespace via different write paths (bulk import, API,
auto-assignment) silently breaks equality checks (ownership/RBAC), recipient
de-duplication and owner-based grouping. Normalize on the way in so those all
match.
"""

from __future__ import annotations

from typing import Optional


def normalize_email(value: Optional[str]) -> Optional[str]:
    """Return a canonical email (trimmed + lower-cased), or None if empty.

    Does not validate the format beyond emptiness — callers that need a strict
    check (e.g. an API boundary) should validate the returned value.
    """
    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned or None
