"""Project Manager directory — live lookup against the AVO Carbon Central
MCP (people/HR directory), with a locally cached fallback for when the MCP
is unreachable.

The MCP's list_members tool returns every active AVO Carbon employee
(~2000+), most of whom (factory floor operators, etc.) have no email on
file. Only rows with an email are kept, since the Project Manager is
designated by email.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.db.models import AvoMemberDirectory

_MCP_TOOL = "list_members"
_PAGE_SIZE = 500
_MAX_PAGES = 20  # safety cap (~10k people) — never loop forever on bad data
_FETCH_TIMEOUT_SECONDS = 12
_CACHE_TTL_SECONDS = 600

_cache: dict[str, Any] = {"entries": None, "fetched_at": 0.0}
_cache_lock = asyncio.Lock()


def _primary_role_name(row: dict) -> Optional[str]:
    assignments = row.get("assignments") or []
    if not assignments:
        return None
    primary = next((a for a in assignments if a.get("is_primary")), assignments[0])
    return primary.get("role_name")


def _to_entry(row: dict) -> Optional[dict]:
    email = (row.get("email") or "").strip()
    if not email:
        return None
    full_name = (
        " ".join(p for p in [row.get("first_name"), row.get("last_name")] if p)
        or email
    )
    return {
        "people_id": row.get("people_id"),
        "full_name": full_name,
        "email": email.lower(),
        "work_unit_name": row.get("work_unit_name"),
        "role_name": _primary_role_name(row),
    }


async def _fetch_from_mcp() -> list[dict]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    entries: list[dict] = []
    seen_emails: set[str] = set()

    async with streamable_http_client(settings.AVO_MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            offset = 0
            for _ in range(_MAX_PAGES):
                result = await session.call_tool(
                    _MCP_TOOL,
                    {"people_status": "Active", "limit": _PAGE_SIZE, "offset": offset},
                )
                text = next(
                    (b.text for b in result.content if getattr(b, "text", None)), None
                )
                if not text:
                    break
                payload = json.loads(text)
                if not payload.get("success"):
                    raise RuntimeError(payload.get("error") or "list_members call failed")
                rows = payload.get("data") or []
                for row in rows:
                    entry = _to_entry(row)
                    if entry and entry["email"] not in seen_emails:
                        seen_emails.add(entry["email"])
                        entries.append(entry)
                if len(rows) < _PAGE_SIZE:
                    break
                offset += _PAGE_SIZE

    entries.sort(key=lambda e: e["full_name"])
    return entries


async def _refresh_db_cache(db: AsyncSession, entries: list[dict]) -> None:
    await db.execute(delete(AvoMemberDirectory))
    for e in entries:
        db.add(
            AvoMemberDirectory(
                people_id=e["people_id"],
                full_name=e["full_name"],
                email=e["email"],
                work_unit_name=e.get("work_unit_name"),
                role_name=e.get("role_name"),
            )
        )
    await db.flush()


async def _read_db_cache(db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(AvoMemberDirectory).order_by(AvoMemberDirectory.full_name)
    )
    return [
        {
            "people_id": row.people_id,
            "full_name": row.full_name,
            "email": row.email,
            "work_unit_name": row.work_unit_name,
            "role_name": row.role_name,
        }
        for row in result.scalars().all()
    ]


async def get_pm_directory(db: AsyncSession) -> dict:
    """Return {"entries": [...], "source": "live"|"cache"}.

    "live" means the MCP answered this call (or a recent one, within the
    in-process TTL). "cache" means the MCP call failed and this is the last
    successfully synced snapshot from the local DB fallback table.
    """
    now = time.monotonic()
    if _cache["entries"] is not None and now - _cache["fetched_at"] < _CACHE_TTL_SECONDS:
        return {"entries": _cache["entries"], "source": "live"}

    async with _cache_lock:
        # Re-check after acquiring the lock — a concurrent request may have
        # already refreshed it while we were waiting.
        now = time.monotonic()
        if _cache["entries"] is not None and now - _cache["fetched_at"] < _CACHE_TTL_SECONDS:
            return {"entries": _cache["entries"], "source": "live"}

        try:
            entries = await asyncio.wait_for(
                _fetch_from_mcp(), timeout=_FETCH_TIMEOUT_SECONDS
            )
            await _refresh_db_cache(db, entries)
            await db.commit()
            _cache["entries"] = entries
            _cache["fetched_at"] = now
            return {"entries": entries, "source": "live"}
        except Exception as exc:
            await db.rollback()
            logger.warning(
                "PM directory: live MCP fetch failed, falling back to local cache: %s",
                exc,
            )
            entries = await _read_db_cache(db)
            return {"entries": entries, "source": "cache"}
