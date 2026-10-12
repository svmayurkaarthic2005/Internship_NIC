"""
Pydantic schemas for request/response validation
"""
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List, Any
from datetime import date, datetime
from uuid import UUID


# ========== AUTH SCHEMAS ==========

class LoginRequest(BaseModel):
    """Login request schema"""
    email: EmailStr
    password: str = Field(min_length=6)


class TokenResponse(BaseModel):
    """Token response with officer information"""
    access_token: str
    token_type: str = "bearer"
    officer_id: str
    officer_name: str
    officer_name_tamil: Optional[str] = None
    employee_id: str
    jurisdiction_type: str
    jurisdiction_name: str


class OfficerContext(BaseModel):
    """Officer context used in JWT payload and dependencies"""
    officer_id: UUID
    employee_id: str
    name: str
    email: str
    designation: Optional[str] = None  # SIS Officer, DIS Officer, SD Officer, Tahsildar
    jurisdiction_type: str
    jurisdiction_name: str
    jurisdiction_ids: List[UUID]
    
    model_config = ConfigDict(from_attributes=True)
    
    @property
    def officer_stage(self) -> str:
        """
        Determine the workflow stage this officer handles based on designation.
        SIS Officer -> SIS stage
        DIS Officer -> DIS stage
        SD Officer -> SD stage
        Tahsildar / Zonal Level Tahsildar (ZDT) -> TAHSILDAR stage (CLAUDE.md's
            documented workflow-role table maps ZDT/HQDT to the TAHSILDAR
            stage, the same desk the NISD chain's Zonal Level Tahsildar sits
            at -- there is no separate ZDT value in `current_stage`)

        An unrecognised designation (a typo, a future role this mapping has
        not been taught, or no designation at all) used to fall through to
        "SIS" -- silently handing that officer the SIS desk's queue in every
        `Application.current_stage == officer.officer_stage` filter
        throughout chatbot.py/postgres.py, rather than refusing to guess.
        It now returns a sentinel outside `current_stage`'s own CHECK
        constraint (`'SIS','SD','DIS','TAHSILDAR','COMPLETED','REJECTED'`),
        so every one of those filters fails closed to an empty desk instead
        of a stage this officer was never shown to hold -- no caller needs
        to change, since the comparison stays a plain string equality that
        simply never matches a stored row.
        """
        if not self.designation:
            return "UNASSIGNED_STAGE"
        designation_lower = self.designation.lower()
        if "zdt" in designation_lower or "zonal level tahsildar" in designation_lower:
            return "TAHSILDAR"
        if "sis" in designation_lower or "sub inspector" in designation_lower:
            return "SIS"
        elif "dis" in designation_lower or "deputy inspector" in designation_lower:
            return "DIS"
        elif ("sd" in designation_lower or "sub divisional" in designation_lower
                or "subdivisional" in designation_lower or "draughtsman" in designation_lower):
            # "Senior Draughtsman" (the SD desk's real designation, per
            # workflow_guide.txt and chatbot.py's own `_WFH_STAGE_ALIASES`)
            # contains no "sd" substring at all and fell through to the
            # fail-closed default until this branch was widened to match it
            # the same way the rest of the codebase already does.
            return "SD"
        elif "tahsildar" in designation_lower:
            return "TAHSILDAR"
        else:
            return "UNASSIGNED_STAGE"


class StandardResponse(BaseModel):
    """Standard API response wrapper"""
    success: bool
    data: Optional[Any] = None
    message: str
    timestamp: str  # ISO8601
    
    @staticmethod
    def success_response(data: Any = None, message: str = "Success") -> "StandardResponse":
        """Create a success response"""
        return StandardResponse(
            success=True,
            data=data,
            message=message,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )
    
    @staticmethod
    def error_response(message: str, data: Any = None) -> "StandardResponse":
        """Create an error response"""
        return StandardResponse(
            success=False,
            data=data,
            message=message,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )


# ========== OFFICER SCHEMAS ==========

class OfficerBase(BaseModel):
    employee_id: str
    name: str
    name_tamil: Optional[str] = None
    email: EmailStr
    mobile: Optional[str] = None
    designation: str = "Sub Inspector Surveyor"


class OfficerCreate(OfficerBase):
    password: str = Field(min_length=6)


class OfficerResponse(OfficerBase):
    id: UUID
    is_active: bool
    last_login: Optional[datetime] = None
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class OfficerProfileResponse(BaseModel):
    """Officer profile with jurisdiction information"""
    officer_id: str
    employee_id: str
    name: str
    name_tamil: Optional[str] = None
    email: str
    mobile: Optional[str] = None
    designation: str
    is_active: bool
    last_login: Optional[str] = None
    jurisdiction: dict
    
    model_config = ConfigDict(from_attributes=True)


# ========== APPLICATION SCHEMAS ==========

class ApplicationBase(BaseModel):
    application_number: str
    application_type: str  # ISD, NISD
    submission_channel: Optional[str] = None
    submission_date: date
    declared_reason: Optional[str] = None


class ApplicationCreate(ApplicationBase):
    applicant_id: UUID
    survey_number_id: UUID


class ApplicationResponse(ApplicationBase):
    id: UUID
    current_stage: str
    current_status: str
    field_visit_scheduled: bool
    is_overdue: bool
    priority_flag: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class ApplicationListResponse(BaseModel):
    applications: List[ApplicationResponse]
    total: int


# ========== SURVEY SCHEMAS ==========

class SurveyNumberBase(BaseModel):
    survey_no: str
    total_area_sqm: float = Field(gt=0)
    land_type: Optional[str] = None
    patta_number: Optional[str] = None


class SurveyNumberResponse(SurveyNumberBase):
    id: UUID
    block_id: UUID
    has_encroachment: bool
    has_litigation: bool
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ========== CHAT SCHEMAS ==========

class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    session_token: Optional[str] = None


class ChatMessageResponse(BaseModel):
    message: str
    session_token: str
    detected_language: str = "en"
    response_time_ms: Optional[int] = None


# ========== PLACEHOLDER SCHEMAS ==========
# Add more schemas as needed for different endpoints
