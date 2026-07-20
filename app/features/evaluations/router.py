"""Batch evaluation upload and scheduling router."""

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.features.evaluations.scheduler import run_evaluation_notifications
from app.features.evaluations.service import (
    generate_csv_template,
    generate_prefilled_template,
    generate_xlsx_template,
    get_evaluations_due,
    ingest_batch,
    parse_rows_from_csv,
    parse_rows_from_xlsx,
)
from app.shared.dependencies.auth import get_current_user
from app.shared.dependencies.db import get_db

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

_PRIVILEGED = {"vp_conversion", "purchasing_director"}


@router.get("/template/csv")
async def download_csv_template():
    """Download the blank CSV template (no auth required — contains no business data)."""
    content = generate_csv_template()
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=evaluation_template.csv"},
    )


@router.get("/template/xlsx")
async def download_xlsx_template():
    """Download the blank Excel template (no auth required — contains no business data)."""
    content = generate_xlsx_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=evaluation_template.xlsx"},
    )


@router.get("/template/prefilled")
async def download_prefilled_template(
    filter: str = Query("all", description="'all' = full panel, 'due' = overdue/due-soon/never-evaluated only"),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Download a pre-filled Excel template.
    - filter=all  → every approved relation (full panel)
    - filter=due  → only OVERDUE, DUE_SOON, NEVER_EVALUATED, sorted by urgency,
                    with extra context columns (urgency, frequency, last/next date, days overdue)
    """
    due_only = filter.lower() == "due"
    content = await generate_prefilled_template(db, due_only=due_only)
    filename = "evaluation_due_only.xlsx" if due_only else "evaluation_prefilled.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.post("/batch-upload", response_model=dict, status_code=status.HTTP_200_OK)
async def batch_upload_evaluations(
    file: UploadFile = File(...),
    dry_run: bool = Query(False, description="Validate rows without writing to the database"),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload an Excel (.xlsx) or CSV file with batch evaluation results.

    Expected columns: supplier_name, plant_name, evaluation_date,
    operational_grade (A/B/C/D), class_value (1-4), comments (optional).

    For each row:
    - Finds the active supplier-site relation
    - Creates ScoreCard + Classification records
    - Derives new status from the grade × class matrix
    - Updates the relation and creates a status history entry if status changed
    - Computes next_evaluation_date based on frequency
    """
    content = await file.read()
    filename = (file.filename or "").lower()

    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        rows, parse_errors = parse_rows_from_xlsx(content)
    elif filename.endswith(".csv"):
        rows, parse_errors = parse_rows_from_csv(content)
    else:
        raise AppException(
            "Unsupported file type. Upload a .xlsx or .csv file.",
            status_code=400,
        )

    if parse_errors and not rows:
        return {
            "status": "error",
            "message": "File could not be parsed. Fix the errors and re-upload.",
            "parse_errors": parse_errors,
            "total_rows": 0,
            "processed": 0,
            "skipped": 0,
        }

    actor = _resolve_actor(current_user)
    result = await ingest_batch(db, rows, changed_by=actor or "BATCH_UPLOAD", dry_run=dry_run)

    return {
        "status": "success" if result["skipped"] == 0 else "partial",
        "message": (
            f"Processed {result['processed']} evaluation(s)"
            + (f", {result['skipped']} row(s) skipped." if result["skipped"] else ".")
        ),
        "parse_errors": parse_errors,
        **result,
    }


@router.get("/due", response_model=dict)
async def get_due_evaluations(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Return all active supplier-site relations with their evaluation urgency status.

    Lazy trigger: if the requesting user is vp_conversion or purchasing_director,
    fire evaluation notifications in the background (first visit of the day only —
    the DB lock in run_evaluation_notifications prevents duplicates).
    """
    items = await get_evaluations_due(db)

    summary = {
        "NEVER_EVALUATED": sum(1 for x in items if x["eval_status"] == "NEVER_EVALUATED"),
        "MISSING_DATE":    sum(1 for x in items if x["eval_status"] == "MISSING_DATE"),
        "OVERDUE":         sum(1 for x in items if x["eval_status"] == "OVERDUE"),
        "DUE_SOON":        sum(1 for x in items if x["eval_status"] == "DUE_SOON"),
        "UP_TO_DATE":      sum(1 for x in items if x["eval_status"] == "UP_TO_DATE"),
        "total": len(items),
    }

    # Lazy trigger: privileged users cause notifications to fire on first page load each day
    if current_user.get("access_profile") in _PRIVILEGED:
        background_tasks.add_task(_background_notify)

    return {
        "status": "success",
        "data": {
            "items": items,
            "summary": summary,
        },
        "message": (
            f"{summary['OVERDUE']} overdue, {summary['DUE_SOON']} due soon, "
            f"{summary['NEVER_EVALUATED']} never evaluated"
        ),
    }


@router.post("/trigger-notifications", response_model=dict, status_code=status.HTTP_200_OK)
async def trigger_evaluation_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """
    Manually send evaluation-due notifications to vp_conversion and purchasing_director.
    Safe to call multiple times — DB lock prevents duplicate sends on the same day.
    """
    result = await run_evaluation_notifications(db, source="manual")

    if result.get("skipped"):
        return {
            "status": "success",
            "notifications_sent": 0,
            "message": "Notifications already sent today — no duplicates created.",
        }

    return {
        "status": "success",
        **result,
        "message": (
            f"Sent {result.get('notifications_sent', 0)} notification(s) to "
            "vp_conversion and purchasing_director users."
            if result.get("notifications_sent")
            else "All supplier evaluations are up to date — no notifications needed."
        ),
    }


async def _background_notify() -> None:
    """Background task: opens its own DB session so the request session is already committed."""
    from app.db.session import SessionLocal
    async with SessionLocal() as db:
        await run_evaluation_notifications(db, source="page_visit")


def _resolve_actor(current_user: dict | None) -> str | None:
    if not isinstance(current_user, dict):
        return None
    return current_user.get("email") or current_user.get("upn") or current_user.get("sub")
