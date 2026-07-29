"""
avo_mcp_client.py — reusable client for the AVO Carbon Central MCP.

Copy this file as-is into any Python project that needs to read AVO Carbon's
central data (people directory, RFQ, APQP, Suppliers, etc.) through the AVO
Carbon Central MCP server. No dependency on any particular web framework or
ORM — just `pip install mcp`.

See ../avo-mcp-integration.md for the full write-up (tool catalog, gotchas,
caching pattern) this was extracted from.

Requirements:
    pip install mcp

Quick test from a new environment:
    python avo_mcp_client.py
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

# No authentication required as of 2026-07 — re-verify with the MCP's
# maintainer before relying on that staying true in a new integration.
AVO_MCP_URL = "https://avo-client-db-mcp.azurewebsites.net/mcp"


async def call_mcp_tool(
    tool_name: str,
    arguments: dict,
    *,
    mcp_url: str = AVO_MCP_URL,
    timeout: float = 15.0,
) -> dict:
    """Call any tool registered on the AVO Carbon Central MCP and return its
    parsed JSON payload (the server always wraps responses as
    {"success": bool, "data": ..., "error": ...}).

    Raises RuntimeError on a {"success": false} response, ValueError if the
    response wasn't valid JSON, and asyncio.TimeoutError if it took too long.
    """
    # Imported lazily so importing this module doesn't require `mcp` to be
    # installed just to read the docstrings/constants.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def _call() -> dict:
        async with streamable_http_client(mcp_url) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = next(
                    (b.text for b in result.content if getattr(b, "text", None)),
                    None,
                )
                if not text:
                    raise RuntimeError(f"Empty response from MCP tool '{tool_name}'.")
                payload = json.loads(text)
                if not payload.get("success"):
                    raise RuntimeError(
                        payload.get("error") or f"MCP tool '{tool_name}' failed."
                    )
                return payload

    return await asyncio.wait_for(_call(), timeout=timeout)


async def fetch_active_members_with_email(
    *,
    mcp_url: str = AVO_MCP_URL,
    page_size: int = 500,
    max_pages: int = 20,
) -> list[dict]:
    """Paginate `list_members(people_status="Active")` on the People/HR
    directory and return only people who have an email on file — most
    active employees (factory floor staff etc.) don't, so this filters
    ~2,100 rows down to a few hundred.

    Returns a list of:
        {"people_id": int, "full_name": str, "email": str,
         "work_unit_name": str | None, "role_name": str | None}
    sorted by full_name, deduped by email.

    max_pages is a safety cap (page_size * max_pages people scanned at most)
    — raise it if your org has more than ~10k active people.
    """
    entries: list[dict] = []
    seen_emails: set[str] = set()
    offset = 0

    for _ in range(max_pages):
        payload = await call_mcp_tool(
            "list_members",
            {"people_status": "Active", "limit": page_size, "offset": offset},
            mcp_url=mcp_url,
        )
        rows = payload.get("data") or []
        for row in rows:
            email = (row.get("email") or "").strip().lower()
            if not email or email in seen_emails:
                continue
            seen_emails.add(email)
            assignments = row.get("assignments") or []
            primary = (
                next((a for a in assignments if a.get("is_primary")), assignments[0])
                if assignments
                else None
            )
            entries.append(
                {
                    "people_id": row.get("people_id"),
                    "full_name": (
                        " ".join(
                            p for p in [row.get("first_name"), row.get("last_name")] if p
                        )
                        or email
                    ),
                    "email": email,
                    "work_unit_name": row.get("work_unit_name"),
                    "role_name": primary.get("role_name") if primary else None,
                }
            )
        if len(rows) < page_size:
            break
        offset += page_size

    entries.sort(key=lambda e: e["full_name"])
    return entries


if __name__ == "__main__":
    async def _main() -> None:
        members = await fetch_active_members_with_email()
        print(f"{len(members)} active members with an email on file\n")
        for m in members[:10]:
            role = f" — {m['role_name']}" if m.get("role_name") else ""
            print(f"  {m['full_name']:<30} {m['email']:<35}{role}")

    asyncio.run(_main())
