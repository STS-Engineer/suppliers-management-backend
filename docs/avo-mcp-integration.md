# AVO Carbon Central MCP — Integration Guide

How this app talks to the **AVO Carbon Central MCP** (the company's central
Model Context Protocol server, backed by the HR/people database, RFQ, APQP
and Suppliers databases) and how to reuse the same integration in another
application.

Used today for: the gate approval **Project Manager picker** and the
**sourcing committee approver picker** (both in `PurchasingValuePage` /
`GateApprovalPage`) — replacing free-text email inputs with a searchable
list of real AVO Carbon employees.

---

## 1. What the MCP is

- URL: `https://avo-client-db-mcp.azurewebsites.net`
- MCP endpoint: `/mcp` (transport: `streamable-http`, JSON-RPC 2.0 under the hood)
- Health check: `GET /health` and `GET /` (plain JSON, not MCP protocol — safe to curl)
- **No authentication** required as of 2026-07 (confirmed live). Treat this as
  an internal-network-trust assumption, not a guarantee — if you productionize
  a new integration, check with the MCP's maintainer whether that still holds.
- Backed by 5 databases, selected per-tool: `people` (KPI_DB_Final — people,
  roles, units, KPIs, customers), `actions`, `rfq`, `apqp`, `suppliers`.
- 101 tools registered in total (`GET /health` reports the live count). This
  guide only documents the **people/directory** tools we've used — there are
  many more for RFQ/APQP/Suppliers CRUD and generic read-only SQL
  (`query_suppliers_readonly`, `list_external_database_rows`, etc.).

### People/HR directory tools (the ones relevant to "pick a real employee")

| Tool | Purpose |
|---|---|
| `list_members(people_status="Active", limit, offset)` | Paginated list of people. **Most rows have no `email`** (factory floor staff etc.) — filter those out. Of ~2,121 active people, only ~415 have an email on file. |
| `search_member(query, ...filters, limit, offset)` | ILIKE search by name/email, with optional country/factory/dept/role/zone/market filters. |
| `get_member(people_id)` | One person by id, with all active role assignments. |
| `find_member_by_email(email)` | Resolve one person by exact (case-insensitive) email. |
| `list_members_by_unit(unit_id \| unit_name, include_sub_units)` | Everyone assigned to a factory/department. |
| `list_members_by_role(role_id \| role_name, include_sub_roles)` | Everyone holding a given role. |
| `get_direct_reports(manager_people_id)` | Direct reports of a manager. |
| `get_org_path(people_id)` | Walk the org chart up to the root (recursive CTE, depth-guarded). |

Every person row from `list_members` looks like this (irrelevant fields
trimmed) — note the **`assignments`** array, which carries each person's
role/title:

```json
{
  "people_id": 1491,
  "first_name": "Ibrahim",
  "last_name": "ABDALLAH",
  "email": null,
  "people_status": "Active",
  "work_unit_name": "SAME",
  "work_country": "Tunisia",
  "assignments": [
    {
      "is_primary": true,
      "role_name": "Quality technicians",
      "role_full_path": "CEO > VP Operation > Plant manager > ... > Operator",
      "factory_name": "SAME"
    }
  ]
}
```

---

## 2. Connecting from Python

```bash
pip install mcp
```

```python
import asyncio, json
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "https://avo-client-db-mcp.azurewebsites.net/mcp"

async def main():
    async with streamable_http_client(URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "list_members",
                {"people_status": "Active", "limit": 5, "offset": 0},
            )
            text = result.content[0].text
            print(json.loads(text))

asyncio.run(main())
```

### Gotchas (found the hard way)

- **`streamable_http_client`**, not `streamablehttp_client` — the underscore
  placement is easy to get wrong; the older `streamablehttp_client` name from
  some docs/examples doesn't exist in `mcp==2.0.0`.
- The async context manager yields **2** values `(read, write)` in
  `mcp==2.0.0`, not 3 (`(read, write, get_session_id)` as in some older
  examples). Unpacking 3 raises `ValueError: not enough values to unpack`.
- You must call `await session.initialize()` before `call_tool`.
- Every tool response is wrapped: `{"success": true/false, "data": [...], ...}`
  (or `"error": "..."` on failure) — this is the *server's* convention, not
  the MCP protocol's. Always check `payload["success"]` after `json.loads`.
- **On Windows**, `mcp` pulls in `pywin32`. If you see
  `ModuleNotFoundError: No module named 'pywintypes'` at runtime, it almost
  always means the process actually running (uvicorn, a service wrapper, an
  IDE run config) is using a **different Python interpreter** than the venv
  where you `pip install`ed `mcp` — not a broken pywin32 install. Fix: start
  the server from the same venv (`venv\Scripts\activate` then run it, or
  point your IDE's interpreter at `venv\Scripts\python.exe`). Only if the
  error persists from the *correct* venv, run once:
  `python venv\Scripts\pywin32_postinstall.py -install`.

---

## 3. Copy-paste reusable module

[`avo_mcp_client.py`](./snippets/avo_mcp_client.py) — a standalone,
framework-agnostic module. No dependency on this app's DB models, FastAPI, or
SQLAlchemy — just `pip install mcp`. Drop it into any Python project that
needs to:

- call **any** tool on the MCP generically (`call_mcp_tool`), or
- fetch the directory of active AVO Carbon employees who have an email
  (`fetch_active_members_with_email`) — the exact building block this app
  uses for its Project Manager / committee approver pickers.

```python
from avo_mcp_client import fetch_active_members_with_email

members = await fetch_active_members_with_email()
# [{"people_id": 1491, "full_name": "Ibrahim ABDALLAH", "email": "...",
#   "work_unit_name": "SAME", "role_name": "Quality technicians"}, ...]
```

Run it standalone to sanity-check connectivity from a new environment:
`python avo_mcp_client.py`.

---

## 4. The caching pattern used in this app

`list_members` returns **every** active employee (~2,121), most without an
email — fetching + filtering that on every page load would be slow and
wasteful. This app's actual implementation
([`app/features/gate_approval/pm_directory.py`](../app/features/gate_approval/pm_directory.py))
layers two things on top of the raw client:

1. **In-process TTL cache** (10 minutes, `asyncio.Lock`-guarded so concurrent
   requests don't all stampede the MCP at once). Cheap, resets on server
   restart, fine for a "refreshes every few minutes" directory.
2. **DB fallback table** (`avo_member_directory` — see
   [models.py](../app/db/models.py) / migrations `20260729_0091` +
   `20260729_0092`), which is **upserted on every successful live fetch**
   and **read from when the live fetch fails or times out**. Self-healing:
   no manual seeding needed — the first successful call populates it, and it
   stays reasonably fresh as long as the MCP is usually reachable. The
   response tells the caller which happened: `{"entries": [...], "source":
   "live" | "cache"}`.

If you're integrating this in a new app, decide whether you need step 2 at
all — for a low-traffic internal tool, the in-process cache alone (or even no
cache) may be enough. Reach for the DB-fallback pattern only if the picker
needs to keep working through MCP downtime.

---

## 5. Steps to reuse this in a new application

1. `pip install mcp`.
2. Copy [`avo_mcp_client.py`](./snippets/avo_mcp_client.py) into the new
   project.
3. Call `fetch_active_members_with_email()` (or `call_mcp_tool(...)` for a
   different tool — see the table in §1, or ask the MCP's maintainer for the
   full 101-tool catalog if you need RFQ/APQP/Suppliers data instead of
   people).
4. Wrap it with whatever caching fits your app (in-memory TTL is usually
   enough; add a DB fallback table only if you need resilience to MCP
   downtime — see §4).
5. Expose it behind your own API endpoint — **do not** call the MCP directly
   from a browser/frontend; it has no auth today, and 101 tools include
   writes (`create_external_database_row`, `update_...`, `delete_...`) you
   almost certainly don't want reachable from client-side code.
6. On the frontend, a plain searchable list (type-to-filter on name/email/role,
   pinned "currently selected" entry, manual-entry fallback) is enough — no
   MCP-specific frontend code is needed, since the frontend only ever talks
   to your own backend endpoint from step 5. This app's version is
   [`MemberDirectoryPicker.tsx`](../../suppliers-management-frontend/src/components/common/MemberDirectoryPicker.tsx),
   copy-paste-able as-is if you're also on React + Tailwind.
