"""
Applications Router
Endpoints for managing SIS applications
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from backend.database import get_db
from backend.schemas import StandardResponse
from backend.dependencies import get_current_officer

router = APIRouter(prefix="/api/v1/applications", tags=["Applications"])


@router.get("/", response_model=StandardResponse)
async def get_applications(
    stage: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    application_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
    current_officer = Depends(get_current_officer)
):
    """
    The officer's applications, every service code, from the register -- scoped to the officer's own
    jurisdiction by `get_all_service_code_applications` (the same source the chat uses for listings).

    Filters: `status` (approved / pending / rejected), `stage` (the register's coarse Open / Completed),
    `application_type` (ISD, NISD or a 4-digit service code). Rejected files are included unless a status
    is given. `total` counts every match; `skip` / `limit` page through it.
    """
    from backend.services.postgres import get_all_service_code_applications

    code = None
    if application_type:
        t = application_type.strip().upper()
        code = {"ISD": "0154", "NISD": "0153"}.get(t, t if t.isdigit() and len(t) == 4 else None)
        if code is None:
            raise HTTPException(status_code=422, detail="application_type must be ISD, NISD or a 4-digit service code")
    data = await get_all_service_code_applications(
        db, current_officer, service_code=code, status=status.strip().lower() if status else None,
        hide_rejected=False)
    rows = data.get("applications") or []
    if stage:
        rows = [r for r in rows if (r.get("stage") or "").lower() == stage.strip().lower()]
    return StandardResponse.success_response(
        data={"applications": rows[skip:skip + limit], "total": len(rows), "skip": skip, "limit": limit},
        message="Applications retrieved"
    )


@router.get("/{application_id}", response_model=StandardResponse)
async def get_application_details(
    application_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_officer = Depends(get_current_officer)
):
    """
    Get detailed information about a specific application
    """
    # TODO: Implement application detail retrieval (Phase 2)
    raise HTTPException(status_code=404, detail="Application not found")


@router.post("/{application_id}/forward", response_model=StandardResponse)
async def forward_application(
    application_id: UUID,
    target_stage: str,
    remarks: str,
    db: AsyncSession = Depends(get_db),
    current_officer = Depends(get_current_officer)
):
    """
    Forward application to next stage in workflow
    """
    # TODO: Implement application forwarding logic (Phase 2)
    raise HTTPException(status_code=501, detail="Not yet implemented")
