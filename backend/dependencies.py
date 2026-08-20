"""
FastAPI Dependencies
"""
from typing import AsyncGenerator
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer

from backend.database import get_db
from backend.config import settings
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services.auth_service import decode_token, get_officer_jurisdiction_ids

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_officer(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> OfficerContext:
    """
    Dependency to get current authenticated SIS officer from JWT token in HTTPOnly cookie.
    
    Reads token from cookie named "sis_access_token".
    Validates token, loads officer from database, resolves jurisdiction IDs.
    
    Returns OfficerContext with all necessary officer information.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Read token from HTTPOnly cookie
    token = request.cookies.get("sis_access_token")
    
    # Fallback to Authorization header if cookie is missing
    if not token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
            logger.info("Using token from Authorization header")
        else:
            logger.warning("No token found in cookie or Authorization header")
    else:
        logger.info("Using token from sis_access_token cookie")
            
    if not token:
        logger.error("Authentication failed: No token provided")
        raise credentials_exception
    
    # Decode token
    payload = decode_token(token)
    if payload is None:
        logger.error("Authentication failed: Invalid or expired token")
        raise credentials_exception
    
    # Extract officer_id from payload
    officer_id_str: str = payload.get("sub")
    if officer_id_str is None:
        logger.error("Authentication failed: No 'sub' in token payload")
        raise credentials_exception
    
    try:
        officer_id = UUID(officer_id_str)
    except ValueError:
        logger.error(f"Authentication failed: Invalid UUID format: {officer_id_str}")
        raise credentials_exception
    
    # Load officer from database
    from sqlalchemy import select
    result = await db.execute(
        select(SISOfficer).where(SISOfficer.id == officer_id)
    )
    officer = result.scalar_one_or_none()
    
    if officer is None:
        logger.error(f"Authentication failed: Officer not found with ID: {officer_id}")
        raise credentials_exception
    
    # Check if officer is active
    if not officer.is_active:
        logger.warning(f"Authentication failed: Officer account is inactive: {officer.employee_id}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Officer account is inactive"
        )
    
    logger.info(f"Authentication successful for officer: {officer.employee_id}")
    
    # Resolve jurisdiction IDs
    jurisdiction_data = await get_officer_jurisdiction_ids(officer.id, db)
    
    # Combine all jurisdiction IDs into a single list
    all_jurisdiction_ids = (
        jurisdiction_data["district_ids"] +
        jurisdiction_data["taluk_ids"] +
        jurisdiction_data["town_ids"] +
        jurisdiction_data["ward_ids"] +
        jurisdiction_data["block_ids"]
    )
    
    # Build OfficerContext
    officer_context = OfficerContext(
        officer_id=officer.id,
        employee_id=officer.employee_id,
        name=officer.name,
        email=officer.email,
        designation=officer.designation,  # Add designation for stage filtering
        jurisdiction_type=jurisdiction_data["jurisdiction_type"],
        jurisdiction_name=jurisdiction_data["jurisdiction_name"],
        jurisdiction_ids=all_jurisdiction_ids
    )
    
    return officer_context


async def get_current_active_officer(
    current_officer: OfficerContext = Depends(get_current_officer)
) -> OfficerContext:
    """
    Dependency to ensure officer is active (already checked in get_current_officer).
    This is here for clarity and potential future use.
    """
    return current_officer
