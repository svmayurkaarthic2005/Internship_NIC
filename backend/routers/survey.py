"""
Survey Router
Endpoints for querying survey numbers and sub-divisions
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

from backend.database import get_db
from backend.schemas import SurveyNumberResponse, StandardResponse
from backend.dependencies import get_current_officer

router = APIRouter(prefix="/api/v1/survey", tags=["Survey"])


@router.get("/search", response_model=StandardResponse)
async def search_survey_numbers(
    survey_no: Optional[str] = Query(None),
    block_id: Optional[UUID] = Query(None),
    patta_number: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_officer = Depends(get_current_officer)
):
    """
    Search for survey numbers within officer's jurisdiction
    """
    # TODO: Implement survey number search (Phase 2)
    return StandardResponse.success_response(
        data={"results": []},
        message="Survey search completed (stub)"
    )


@router.get("/{survey_id}", response_model=StandardResponse)
async def get_survey_details(
    survey_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_officer = Depends(get_current_officer)
):
    """
    Get detailed information about a survey number
    """
    # TODO: Implement survey detail retrieval (Phase 2)
    raise HTTPException(status_code=404, detail="Survey number not found")


@router.get("/{survey_id}/sub-divisions", response_model=StandardResponse)
async def get_sub_divisions(
    survey_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_officer = Depends(get_current_officer)
):
    """
    Get all sub-divisions for a survey number
    """
    # TODO: Implement sub-division listing (Phase 2)
    return StandardResponse.success_response(
        data={"sub_divisions": []},
        message="Sub-divisions retrieved (stub)"
    )
