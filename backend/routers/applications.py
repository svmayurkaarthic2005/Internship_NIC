"""
Applications Router
Endpoints for managing SIS applications
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from backend.database import get_db
from backend.schemas import ApplicationResponse, ApplicationListResponse, StandardResponse
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
    Get list of applications assigned to current officer.
    Filtered by jurisdiction and optional query parameters.
    """
    # TODO: Implement application listing with filters (Phase 2)
    return StandardResponse.success_response(
        data={"applications": [], "total": 0},
        message="Applications retrieved (stub)"
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
