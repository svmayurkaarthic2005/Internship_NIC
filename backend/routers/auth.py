"""
Authentication Router
Endpoints for login, token refresh, logout, and user profile
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db
from backend.schemas import (
    LoginRequest, TokenResponse, StandardResponse, OfficerProfileResponse
)
from backend.dependencies import get_current_officer
from backend.models import SISOfficer
from backend.services.auth_service import (
    verify_password, create_access_token, get_officer_jurisdiction_ids
)
from backend.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@router.post("/login", response_model=StandardResponse)
async def login(
    login_data: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """
    Login endpoint - authenticate SIS officer and return JWT token in HTTPOnly cookie.
    
    Returns officer information and sets HTTPOnly cookie named "sis_access_token".
    """
    # Lookup officer by email
    result = await db.execute(
        select(SISOfficer).where(SISOfficer.email == login_data.email)
    )
    officer = result.scalar_one_or_none()
    
    if not officer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Verify password
    if not verify_password(login_data.password, officer.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    # Check if officer is active
    if not officer.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Officer account is inactive"
        )
    
    # Resolve jurisdiction
    jurisdiction_data = await get_officer_jurisdiction_ids(officer.id, db)
    
    # Create JWT access token (8 hours expiry)
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(officer.id), "email": officer.email},
        expires_delta=access_token_expires
    )
    
    # Set HTTPOnly cookie
    response.set_cookie(
        key="sis_access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,  # Convert to seconds
        secure=settings.ENVIRONMENT == "production"  # HTTPS only in production
    )
    
    # Update last_login timestamp
    officer.last_login = datetime.utcnow()
    await db.commit()
    
    # Build token response
    token_response = TokenResponse(
        access_token=access_token,
        token_type="bearer",
        officer_id=str(officer.id),
        officer_name=officer.name,
        officer_name_tamil=officer.name_tamil,
        employee_id=officer.employee_id,
        jurisdiction_type=jurisdiction_data["jurisdiction_type"],
        jurisdiction_name=jurisdiction_data["jurisdiction_name"]
    )
    
    return StandardResponse.success_response(
        data=token_response.model_dump(),
        message="Login successful"
    )


@router.post("/logout", response_model=StandardResponse)
async def logout(response: Response):
    """
    Logout endpoint - clear HTTPOnly cookie.
    
    Does not require authentication (allows logout even with expired token).
    """
    # Clear the cookie by setting max_age to 0
    response.set_cookie(
        key="sis_access_token",
        value="",
        httponly=True,
        samesite="lax",
        max_age=0,
        secure=settings.ENVIRONMENT == "production"
    )
    
    return StandardResponse.success_response(
        message="Logged out successfully"
    )


@router.post("/refresh", response_model=StandardResponse)
async def refresh_token(
    response: Response,
    current_officer = Depends(get_current_officer),
    db: AsyncSession = Depends(get_db)
):
    """
    Refresh token endpoint - issue new token for authenticated user.
    
    Protected by get_current_officer dependency.
    Returns new token and resets HTTPOnly cookie.
    """
    # Resolve jurisdiction (might have changed)
    jurisdiction_data = await get_officer_jurisdiction_ids(
        current_officer.officer_id, db
    )
    
    # Create new JWT access token
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(current_officer.officer_id), "email": current_officer.email},
        expires_delta=access_token_expires
    )
    
    # Set new HTTPOnly cookie
    response.set_cookie(
        key="sis_access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        secure=settings.ENVIRONMENT == "production"
    )
    
    # Build token response
    token_response = TokenResponse(
        access_token=access_token,
        token_type="bearer",
        officer_id=str(current_officer.officer_id),
        officer_name=current_officer.name,
        officer_name_tamil=None,  # Not in context, would need to re-query
        employee_id=current_officer.employee_id,
        jurisdiction_type=jurisdiction_data["jurisdiction_type"],
        jurisdiction_name=jurisdiction_data["jurisdiction_name"]
    )
    
    return StandardResponse.success_response(
        data=token_response.model_dump(),
        message="Token refreshed successfully"
    )


@router.get("/me", response_model=StandardResponse)
async def get_current_user_profile(
    current_officer = Depends(get_current_officer),
    db: AsyncSession = Depends(get_db)
):
    """
    Get current officer profile endpoint.
    
    Protected by get_current_officer dependency.
    Returns detailed officer profile with jurisdiction information.
    """
    # Load full officer details from database
    result = await db.execute(
        select(SISOfficer).where(SISOfficer.id == current_officer.officer_id)
    )
    officer = result.scalar_one_or_none()
    
    if not officer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Officer not found"
        )
    
    # Get jurisdiction details
    jurisdiction_data = await get_officer_jurisdiction_ids(officer.id, db)
    
    # Build profile response
    profile = OfficerProfileResponse(
        officer_id=str(officer.id),
        employee_id=officer.employee_id,
        name=officer.name,
        name_tamil=officer.name_tamil,
        email=officer.email,
        mobile=officer.mobile,
        designation=officer.designation,
        is_active=officer.is_active,
        last_login=officer.last_login.isoformat() + "Z" if officer.last_login else None,
        jurisdiction={
            "type": jurisdiction_data["jurisdiction_type"],
            "name": jurisdiction_data["jurisdiction_name"],
            "district_count": len(jurisdiction_data["district_ids"]),
            "taluk_count": len(jurisdiction_data["taluk_ids"]),
            "town_count": len(jurisdiction_data["town_ids"]),
            "ward_count": len(jurisdiction_data["ward_ids"]),
            "block_count": len(jurisdiction_data["block_ids"])
        }
    )
    
    return StandardResponse.success_response(
        data=profile.model_dump(),
        message="Profile retrieved successfully"
    )
