"""Action Plan sync via the AVO Carbon Central MCP.

Same MCP server already used for the People/HR directory (see
app/features/gate_approval/pm_directory.py), but a different tool
namespace/DB backend ("actions", gated by ACTION_PLAN_DATABASE_URL on the
MCP's own side) exposing sujet/action CRUD: create_sujet, create_action,
update_action, update_action_status, add_action_attachment, delete_action, etc.

This replaces both the earlier disabled HTTP push (POST .../api/v2/plans)
and a raw-SQL direct-DB module — the MCP is the governed interface: it owns
validation, status-change history, and event logging, so we don't reimplement
that here.

Structure created per plan (mirrors what APQP's own sync creates in the same
DB, under a separate root so the two apps' trees don't collide):
  "Purchasing Value Action Plans"                (root sujet, code=PV-ROOT)
    └── "Opportunity {id} — {name}"              (group sujet, code=PV-OPP-{id})
          └── plan's own sujets tree (code derived from plan_code)
                └── actions (parent_action_id used for real sub_actions nesting)
    └── "General Action Plans"                   (group sujet, code=PV-GENERAL)
          └── actions attached directly (no per-plan wrapper sujet — see
              _sync_actions_directly; avoids one near-empty sujet per
              quick-added general action). Genuine sous_sujets, if any,
              still get their own sujet row.

Idempotency: the MCP has no upsert-by-code tool, only create/get/list. Each
synced sujet/action's returned id is written back onto the corresponding node
in plan_data (as "_external_id"), the same write-back-id pattern APQP's sync
uses for its sprint tasks. Re-syncing a plan skips creating nodes that
already carry an "_external_id" and instead calls update_action(_status) so
we don't create duplicate rows on every sync click.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

INSERTED_BY = "suppliers_purchasing_value_system"
PV_ROOT_CODE = "PV-ROOT"
PV_ROOT_TITLE = "Purchasing Value Action Plans"

# Our own action.status vocabulary ({"open", "closed", "blocked"} — see
# PurchasingValueService._validate_closed_actions / valid_statuses) does not
# match the MCP's action.status enum (open | in_progress | done | cancelled |
# on_hold). "blocked" maps to "on_hold" as the closest equivalent.
_STATUS_TO_MCP = {"open": "open", "blocked": "on_hold", "closed": "done"}

# The MCP's action.type enum (corrective | preventive | improvement |
# observation) has no equivalent field in our ActionNodeV2 schema — we don't
# collect this from the user today, so every synced action defaults to
# "corrective". Revisit if the UI grows a field for this.
DEFAULT_ACTION_TYPE = "action"


def is_enabled() -> bool:
    return bool(settings.AVO_MCP_URL)


def carry_forward_external_ids(
    old_nodes: Optional[list[dict]], new_nodes: Optional[list[dict]]
) -> None:
    """update_action_plan rebuilds `sujets`/`actions` from a fresh
    model_dump() of the incoming payload, which has no "_external_id" field
    (SujetNodeV2/ActionNodeV2 don't declare one) — so a naive replace would
    wipe out the sync identity written back by a previous sync_plan_to_mcp
    call, and the next sync would create duplicate rows instead of updating
    the existing ones. Call this BEFORE overwriting plan_data["sujets"] to
    carry "_external_id"/"_last_synced_status" over onto matching new nodes.

    Matches siblings by titre (best-effort — a title edit or reorder that
    also changes titles will be treated as a new node, which just means one
    extra create + an orphaned row on the MCP side rather than data loss).
    Also carries forward each action's attachment "_external_id"s, matched by
    blob_name — belt-and-suspenders, since `attachments` is an untyped dict
    list in ActionNodeV2 and normally survives the update round-trip as-is."""
    old_nodes = old_nodes or []
    new_nodes = new_nodes or []

    by_titre: dict[Optional[str], list[dict]] = {}
    for node in old_nodes:
        by_titre.setdefault(node.get("titre"), []).append(node)

    for new_node in new_nodes:
        bucket = by_titre.get(new_node.get("titre"))
        if not bucket:
            continue
        old_node = bucket.pop(0)
        if "_external_id" in old_node:
            new_node["_external_id"] = old_node["_external_id"]
        if "_last_synced_status" in old_node:
            new_node["_last_synced_status"] = old_node["_last_synced_status"]
        old_attachments_by_blob = {
            a.get("blob_name"): a for a in (old_node.get("attachments") or []) if a.get("blob_name")
        }
        for new_att in new_node.get("attachments") or []:
            old_att = old_attachments_by_blob.get(new_att.get("blob_name"))
            if old_att and "_external_id" in old_att:
                new_att["_external_id"] = old_att["_external_id"]
        carry_forward_external_ids(
            old_node.get("sous_sujets"), new_node.get("sous_sujets")
        )
        carry_forward_external_ids(old_node.get("actions"), new_node.get("actions"))
        carry_forward_external_ids(
            old_node.get("sous_actions"), new_node.get("sous_actions")
        )


def _to_mcp_status(status: Optional[str]) -> str:
    return _STATUS_TO_MCP.get(status or "open", "open")


def _uppercase_lastname(name: Optional[str]) -> Optional[str]:
    """Action Plan DB convention for `responsable`: last name uppercased
    (e.g. "Hayfa Rajhi" -> "Hayfa RAJHI"). Applied only on the wire to the
    MCP — the locally stored plan_data keeps its own Title Case display
    value untouched (see PurchasingValueService._name_from_email)."""
    if not name:
        return name
    parts = name.strip().split(" ")
    if len(parts) < 2:
        return parts[0].upper() if parts and parts[0] else name
    parts[-1] = parts[-1].upper()
    return " ".join(parts)


def _flatten_exception(exc: BaseException) -> list[BaseException]:
    """anyio's TaskGroup (used internally by the MCP's streamable_http_client)
    wraps the real failure (connection refused, DNS error, TLS error, ...) in
    an ExceptionGroup — str(exc) on that just prints "unhandled errors in a
    TaskGroup (N sub-exceptions)" with no useful detail. Recurse into
    `.exceptions` to find the actual leaf cause(s)."""
    sub = getattr(exc, "exceptions", None)
    if sub:
        flat: list[BaseException] = []
        for e in sub:
            flat.extend(_flatten_exception(e))
        return flat
    return [exc]


def _describe_exception(exc: BaseException) -> str:
    leaves = _flatten_exception(exc)
    return "; ".join(f"{type(e).__name__}: {e}" for e in leaves)


async def _call_mcp_tool(
    tool_name: str, arguments: dict, *, timeout: float = 15.0
) -> Any:
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def _call() -> Any:
        async with streamable_http_client(settings.AVO_MCP_URL) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                text = next(
                    (b.text for b in result.content if getattr(b, "text", None)), None
                )
                if not text:
                    raise RuntimeError(f"Empty response from MCP tool '{tool_name}'.")
                payload = json.loads(text)
                if not payload.get("success"):
                    raise RuntimeError(
                        payload.get("error") or f"MCP tool '{tool_name}' failed."
                    )
                return payload.get("data")

    try:
        return await asyncio.wait_for(_call(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"MCP tool '{tool_name}' timed out after {timeout}s (is AVO_MCP_URL={settings.AVO_MCP_URL!r} reachable?)"
        ) from exc
    except RuntimeError:
        raise  # already a clean, specific message (empty response / tool-level failure)
    except Exception as exc:
        detail = _describe_exception(exc)
        raise RuntimeError(f"MCP tool '{tool_name}' failed: {detail}") from exc


_LIST_SUJETS_PAGE_SIZE = 500
_LIST_SUJETS_MAX_PAGES = 40  # safety cap (~20k sujets scanned at most)


async def _find_sujet_by_code(code: str) -> Optional[int]:
    """The MCP has no get-by-code tool, only list_sujets — scan client-side,
    paginating fully. This DB is shared with other apps (APQP and others), so
    a single unpaginated page could easily miss our own root/group sujets if
    the total count exceeds one page — that would silently create duplicates
    on every sync instead of reusing the existing row."""
    offset = 0
    for _ in range(_LIST_SUJETS_MAX_PAGES):
        rows = (
            await _call_mcp_tool(
                "list_sujets", {"limit": _LIST_SUJETS_PAGE_SIZE, "offset": offset}
            )
            or []
        )
        match = next((r for r in rows if r.get("code") == code), None)
        if match:
            return match["id"]
        if len(rows) < _LIST_SUJETS_PAGE_SIZE:
            return None
        offset += _LIST_SUJETS_PAGE_SIZE
    logger.warning(
        "_find_sujet_by_code('%s'): hit the %d-page scan cap without finding a match",
        code,
        _LIST_SUJETS_MAX_PAGES,
    )
    return None


async def _get_or_create_sujet(
    titre: str, code: str, description: Optional[str], parent_sujet_id: Optional[int]
) -> int:
    existing_id = await _find_sujet_by_code(code)
    if existing_id is not None:
        return existing_id
    created = await _call_mcp_tool(
        "create_sujet",
        {
            "titre": titre,
            "code": code,
            "description": description,
            "parent_sujet_id": parent_sujet_id,
            "inserted_by": INSERTED_BY,
        },
    )
    return created["id"]


async def _sync_attachments(action: dict[str, Any], action_external_id: int) -> None:
    """Register any not-yet-synced attachments on this action via
    add_action_attachment. Tracks sync identity per attachment the same way
    actions/sujets do (writes "_external_id" back onto the attachment dict)
    so re-syncing doesn't register the same file twice. Files themselves stay
    in our own Azure Blob storage — only the reference (file_url) is passed,
    matching what add_action_attachment expects (file_path, not bytes)."""
    for attachment in action.get("attachments") or []:
        if attachment.get("_external_id") is not None:
            continue
        file_path = attachment.get("file_url")
        if not file_path:
            continue
        file_name = attachment.get("filename") or attachment.get("blob_name") or "attachment"
        created = await _call_mcp_tool(
            "add_action_attachment",
            {
                "action_id": action_external_id,
                "file_name": file_name,
                "file_path": file_path,
                "uploaded_by": attachment.get("uploaded_by"),
            },
        )
        attachment["_external_id"] = created["id"]


async def _sync_action(
    action: dict[str, Any], sujet_id: int, parent_action_id: Optional[int] = None
) -> None:
    mcp_status = _to_mcp_status(action.get("status"))
    external_id = action.get("_external_id")

    if external_id is None:
        created = await _call_mcp_tool(
            "create_action",
            {
                "sujet_id": sujet_id,
                "type": DEFAULT_ACTION_TYPE,
                "titre": action.get("titre") or "Untitled action",
                "description": action.get("description"),
                "status": mcp_status,
                "priorite": action.get("priorite"),
                "responsable": _uppercase_lastname(action.get("responsable")),
                "email_responsable": action.get("email_responsable"),
                "demandeur": action.get("demandeur"),
                "email_demandeur": action.get("email_demandeur"),
                "due_date": action.get("due_date"),
                "parent_action_id": parent_action_id,
                "importance": action.get("importance"),
                "urgency": action.get("urgency"),
                "estimated_duration_days": action.get("estimated_duration_days"),
                "ordre": action.get("ordre"),
            },
        )
        action["_external_id"] = created["id"]
        action["_last_synced_status"] = mcp_status
        external_id = created["id"]
    else:
        if action.get("_last_synced_status") != mcp_status:
            await _call_mcp_tool(
                "update_action_status",
                {
                    "action_id": external_id,
                    "new_status": mcp_status,
                    "created_by": action.get("email_responsable"),
                    "closed_date": action.get("closed_date")
                    if mcp_status == "done"
                    else None,
                },
            )
            action["_last_synced_status"] = mcp_status
        await _call_mcp_tool(
            "update_action",
            {
                "action_id": external_id,
                "titre": action.get("titre"),
                "description": action.get("description"),
                "responsable": _uppercase_lastname(action.get("responsable")),
                "email_responsable": action.get("email_responsable"),
                "due_date": action.get("due_date"),
                "priorite": action.get("priorite"),
                "importance": action.get("importance"),
                "urgency": action.get("urgency"),
                "estimated_duration_days": action.get("estimated_duration_days"),
                "ordre": action.get("ordre"),
            },
        )

    await _sync_attachments(action, external_id)

    for sub in action.get("sous_actions") or []:
        await _sync_action(sub, sujet_id, parent_action_id=external_id)


async def _sync_sujet_tree(
    sujet: dict[str, Any], parent_sujet_id: int, code: Optional[str]
) -> None:
    external_id = sujet.get("_external_id")
    if external_id is None:
        created = await _call_mcp_tool(
            "create_sujet",
            {
                "titre": sujet.get("titre") or "Untitled subject",
                "code": code,
                "description": sujet.get("description"),
                "parent_sujet_id": parent_sujet_id,
                "inserted_by": INSERTED_BY,
            },
        )
        sujet["_external_id"] = created["id"]
        external_id = created["id"]

    for action in sujet.get("actions") or []:
        await _sync_action(action, external_id)

    for idx, child in enumerate(sujet.get("sous_sujets") or []):
        child_code = f"{code}-{idx}" if code else None
        await _sync_sujet_tree(child, external_id, child_code)


async def _sync_actions_directly(
    sujet: dict[str, Any], parent_sujet_id: int, code_prefix: Optional[str]
) -> None:
    """For general (no-opportunity) plans: skip creating a wrapper sujet for
    the plan itself — one per quick-added action would otherwise pile up as a
    near-empty one-action sujet under PV-GENERAL. Attach the plan's actions
    directly under the group sujet instead. Genuine sub-subjects
    (sous_sujets), if present, still get their own sujet row since those
    represent real grouping, not just the plan wrapper."""
    for action in sujet.get("actions") or []:
        await _sync_action(action, parent_sujet_id)

    for idx, child in enumerate(sujet.get("sous_sujets") or []):
        child_code = f"{code_prefix}-{idx}" if code_prefix else None
        await _sync_sujet_tree(child, parent_sujet_id, child_code)


async def sync_plan_to_mcp(
    plan_data: dict[str, Any],
    plan_code: Optional[str],
    opportunity_id: Optional[int],
    opportunity_name: Optional[str],
) -> dict[str, Any]:
    """Sync a Purchasing Value action plan's sujet/action tree to the shared
    Action Plan DB via MCP tool calls. Mutates plan_data in place, writing
    "_external_id" onto each synced sujet/action node — the caller is
    responsible for persisting plan_data (flag_modified) afterwards so
    re-syncs are idempotent. Raises on any MCP call failure."""
    root_id = await _get_or_create_sujet(PV_ROOT_TITLE, PV_ROOT_CODE, None, None)

    if opportunity_id is not None:
        group_code = f"PV-OPP-{opportunity_id}"
        group_title = (
            f"Opportunity {opportunity_id} — {opportunity_name}"
            if opportunity_name
            else f"Opportunity {opportunity_id}"
        )
    else:
        group_code = "PV-GENERAL"
        group_title = "General Action Plans"

    group_id = await _get_or_create_sujet(group_title, group_code, None, root_id)

    sujets = plan_data.get("sujets") or []
    for idx, sujet in enumerate(sujets):
        code = (
            plan_code
            if len(sujets) == 1
            else (f"{plan_code}-{idx}" if plan_code else None)
        )
        if opportunity_id is None:
            await _sync_actions_directly(sujet, group_id, code)
        else:
            await _sync_sujet_tree(sujet, group_id, code)

    return {"root_sujet_id": root_id, "group_sujet_id": group_id}


def collect_external_action_ids(sujets: Optional[list[dict]]) -> list[int]:
    """Recursively collect every synced action's "_external_id" out of a
    sujets tree — used when deleting a whole plan, to know which rows to
    remove on the MCP side.

    NOTE: the MCP only exposes delete_action, not a delete_sujet tool — the
    wrapper sujets themselves (per-plan / PV-OPP-{id} / PV-GENERAL / PV-ROOT)
    can't be cleaned up this way and will remain in the shared DB even after
    the last plan under them is deleted."""
    ids: list[int] = []

    def walk_actions(actions: Optional[list[dict]]) -> None:
        for action in actions or []:
            external_id = action.get("_external_id")
            if external_id is not None:
                ids.append(external_id)
            walk_actions(action.get("sous_actions"))

    def walk_sujets(nodes: Optional[list[dict]]) -> None:
        for sujet in nodes or []:
            walk_actions(sujet.get("actions"))
            walk_sujets(sujet.get("sous_sujets"))

    walk_sujets(sujets)
    return ids


async def delete_action_from_mcp(external_id: int) -> None:
    await _call_mcp_tool("delete_action", {"action_id": external_id})
