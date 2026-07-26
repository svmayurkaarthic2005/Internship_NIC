"""
SQLAlchemy ORM Models for SIS Chatbot Portal
All models use async SQLAlchemy with UUID primary keys.

Fixes applied (2025-07):
  1. datetime.utcnow → datetime.now(timezone.utc)  (deprecated in Python 3.12+)
  2. DateTime → TIMESTAMP(timezone=True) everywhere for consistency
  3. OfficerJurisdiction: CheckConstraint ensuring at least one location FK is set
  4. SurveyOwnership.ownership_share: String → Numeric(5,2)
  5. Application: CheckConstraint on application_type ('ISD','NISD','MERGE')
  6. ChatMessage: CheckConstraint on role ('user','assistant')
  7. OfficerJurisdiction: composite indexes on officer_id+block_id, officer_id+ward_id
  8. AuditLog: officer_employee_id String column to preserve identity after officer deletion
  9. ChatMessage: structured_data JSONB column to record what DB data was shown
"""
from sqlalchemy import (
    Column, String, Integer, Boolean, Date, Numeric, Text,
    ForeignKey, CheckConstraint, UniqueConstraint, Index, CHAR, TIMESTAMP
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
from datetime import datetime, timezone
import uuid

from backend.database import Base


# Timezone-aware UTC timestamp — used as default everywhere
def _utcnow():
    return datetime.now(timezone.utc)


# ========== GEOGRAPHY TABLES ==========

class District(Base):
    __tablename__ = "districts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    district_code = Column(String(10), nullable=False, unique=True)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    taluks = relationship("Taluk", back_populates="district", cascade="all, delete-orphan")
    officer_jurisdictions = relationship("OfficerJurisdiction", back_populates="district")


class Taluk(Base):
    __tablename__ = "taluks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    district_id = Column(UUID(as_uuid=True), ForeignKey("districts.id"), nullable=False)
    name = Column(String(100), nullable=False)
    taluk_code = Column(String(10), nullable=False, unique=True)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    district = relationship("District", back_populates="taluks")
    towns = relationship("Town", back_populates="taluk", cascade="all, delete-orphan")
    officer_jurisdictions = relationship("OfficerJurisdiction", back_populates="taluk")


class Town(Base):
    __tablename__ = "towns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    taluk_id = Column(UUID(as_uuid=True), ForeignKey("taluks.id"), nullable=False)
    name = Column(String(100), nullable=False)
    town_code = Column(String(10), nullable=False, unique=True)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    taluk = relationship("Taluk", back_populates="towns")
    wards = relationship("Ward", back_populates="town", cascade="all, delete-orphan")
    officer_jurisdictions = relationship("OfficerJurisdiction", back_populates="town")


class Ward(Base):
    __tablename__ = "wards"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    town_id = Column(UUID(as_uuid=True), ForeignKey("towns.id"), nullable=False)
    ward_number = Column(String(20), nullable=False)
    ward_name = Column(String(100))
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    town = relationship("Town", back_populates="wards")
    blocks = relationship("Block", back_populates="ward", cascade="all, delete-orphan")
    officer_jurisdictions = relationship("OfficerJurisdiction", back_populates="ward")


class Block(Base):
    __tablename__ = "blocks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ward_id = Column(UUID(as_uuid=True), ForeignKey("wards.id"), nullable=False)
    block_number = Column(String(20), nullable=False)
    block_name = Column(String(100))
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    ward = relationship("Ward", back_populates="blocks")
    survey_numbers = relationship("SurveyNumber", back_populates="block", cascade="all, delete-orphan")
    officer_jurisdictions = relationship("OfficerJurisdiction", back_populates="block")


# ========== SURVEY TABLES ==========

class SurveyNumber(Base):
    __tablename__ = "survey_numbers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    block_id = Column(UUID(as_uuid=True), ForeignKey("blocks.id"), nullable=False)
    survey_no = Column(String(50), nullable=False)
    total_area_sqm = Column(Numeric(12, 2), nullable=False)
    land_type = Column(String(50))          # agricultural, residential, commercial
    patta_number = Column(String(50))
    has_encroachment = Column(Boolean, default=False)
    has_litigation = Column(Boolean, default=False)
    litigation_reference = Column(String(200))
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('block_id', 'survey_no', name='uq_block_survey'),
        Index('idx_survey_no', 'survey_no'),
    )

    block = relationship("Block", back_populates="survey_numbers")
    sub_divisions = relationship("SubDivision", back_populates="survey_number", cascade="all, delete-orphan")
    survey_ownerships = relationship("SurveyOwnership", back_populates="survey_number")
    applications = relationship("Application", back_populates="survey_number")
    patta_transfers = relationship("PattaTransfer", back_populates="survey_number")


class SubDivision(Base):
    __tablename__ = "sub_divisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    survey_number_id = Column(UUID(as_uuid=True), ForeignKey("survey_numbers.id"), nullable=False)
    sub_division_no = Column(String(50), nullable=False)    # e.g. "145/1A"
    area_sqm = Column(Numeric(12, 2), nullable=False)
    status = Column(String(30), default='active')           # active, merged, deleted
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('survey_number_id', 'sub_division_no', name='uq_survey_subdivision'),
        Index('idx_sub_division_no', 'sub_division_no'),
    )

    survey_number = relationship("SurveyNumber", back_populates="sub_divisions")
    survey_ownerships = relationship("SurveyOwnership", back_populates="sub_division")
    application_sub_divisions = relationship("ApplicationSubDivision", back_populates="sub_division")
    patta_transfers = relationship("PattaTransfer", back_populates="sub_division")


# ========== OWNER TABLES ==========

class Owner(Base):
    __tablename__ = "owners"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    name_tamil = Column(String(200))
    father_name = Column(String(200))
    aadhaar_last4 = Column(CHAR(4))
    mobile = Column(String(15))
    address = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    survey_ownerships = relationship("SurveyOwnership", back_populates="owner")
    previous_patta_transfers = relationship("PattaTransfer", foreign_keys="PattaTransfer.previous_owner_id", back_populates="previous_owner")
    new_patta_transfers = relationship("PattaTransfer", foreign_keys="PattaTransfer.new_owner_id", back_populates="new_owner")


class SurveyOwnership(Base):
    __tablename__ = "survey_ownership"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    survey_number_id = Column(UUID(as_uuid=True), ForeignKey("survey_numbers.id"), nullable=False)
    sub_division_id = Column(UUID(as_uuid=True), ForeignKey("sub_divisions.id"), nullable=True)  # null = whole survey
    owner_id = Column(UUID(as_uuid=True), ForeignKey("owners.id"), nullable=False)
    # FIX #4: Numeric instead of String — supports range queries and share validation
    ownership_share = Column(Numeric(5, 2), default=100.00)  # percentage e.g. 100.00, 50.00
    is_joint_owner = Column(Boolean, default=False)
    ownership_type = Column(String(50))     # sole, joint, inherited, partitioned
    effective_from = Column(Date)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    survey_number = relationship("SurveyNumber", back_populates="survey_ownerships")
    sub_division = relationship("SubDivision", back_populates="survey_ownerships")
    owner = relationship("Owner", back_populates="survey_ownerships")


# ========== OFFICER / AUTH TABLES ==========

class SISOfficer(Base):
    __tablename__ = "sis_officers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id = Column(String(20), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    name_tamil = Column(String(200))
    email = Column(String(200), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    mobile = Column(String(15))
    designation = Column(String(100), default='Sub Inspector Surveyor')
    is_active = Column(Boolean, default=True)
    last_login = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    jurisdictions = relationship("OfficerJurisdiction", back_populates="officer", cascade="all, delete-orphan")
    assigned_applications = relationship("Application", back_populates="assigned_officer")
    workflow_actions = relationship("WorkflowHistory", back_populates="performed_by_officer")
    field_visits = relationship("FieldVisit", back_populates="officer")
    notifications = relationship("Notification", back_populates="officer")
    audit_logs = relationship("AuditLog", back_populates="officer")
    chat_sessions = relationship("ChatSession", back_populates="officer")


class OfficerJurisdiction(Base):
    __tablename__ = "officer_jurisdictions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    officer_id = Column(UUID(as_uuid=True), ForeignKey("sis_officers.id"), nullable=False)
    jurisdiction_type = Column(String(20), nullable=False)  # district/taluk/town/ward/block
    district_id = Column(UUID(as_uuid=True), ForeignKey("districts.id"), nullable=True)
    taluk_id = Column(UUID(as_uuid=True), ForeignKey("taluks.id"), nullable=True)
    town_id = Column(UUID(as_uuid=True), ForeignKey("towns.id"), nullable=True)
    ward_id = Column(UUID(as_uuid=True), ForeignKey("wards.id"), nullable=True)
    block_id = Column(UUID(as_uuid=True), ForeignKey("blocks.id"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        # FIX #3: at least one location FK must be set
        CheckConstraint(
            "district_id IS NOT NULL OR taluk_id IS NOT NULL OR town_id IS NOT NULL "
            "OR ward_id IS NOT NULL OR block_id IS NOT NULL",
            name='ck_jurisdiction_not_empty'
        ),
        # FIX #7: composite indexes for chatbot jurisdiction queries
        Index('idx_officer_jurisdiction', 'officer_id'),
        Index('idx_officer_block', 'officer_id', 'block_id'),
        Index('idx_officer_ward', 'officer_id', 'ward_id'),
    )

    officer = relationship("SISOfficer", back_populates="jurisdictions")
    district = relationship("District", back_populates="officer_jurisdictions")
    taluk = relationship("Taluk", back_populates="officer_jurisdictions")
    town = relationship("Town", back_populates="officer_jurisdictions")
    ward = relationship("Ward", back_populates="officer_jurisdictions")
    block = relationship("Block", back_populates="officer_jurisdictions")


# ========== APPLICATION TABLES ==========

class Applicant(Base):
    __tablename__ = "applicants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    mobile = Column(String(15))
    email = Column(String(200))
    aadhaar_last4 = Column(CHAR(4))
    address = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    applications = relationship("Application", back_populates="applicant")


class Application(Base):
    __tablename__ = "applications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_number = Column(String(30), nullable=False, unique=True)
    application_type = Column(String(10), nullable=False)   # ISD, NISD, MERGE
    applicant_id = Column(UUID(as_uuid=True), ForeignKey("applicants.id"), nullable=False)
    survey_number_id = Column(UUID(as_uuid=True), ForeignKey("survey_numbers.id"), nullable=False)
    assigned_officer_id = Column(UUID(as_uuid=True), ForeignKey("sis_officers.id"), nullable=False)
    submission_channel = Column(String(20))                 # CSC, citizen, sub_registrar
    submission_date = Column(Date, nullable=False)
    sale_deed_number = Column(String(100))
    sale_deed_registered = Column(Boolean, default=False)
    declared_reason = Column(String(100))                   # sale, inheritance, partition, gift_deed
    current_stage = Column(String(30), nullable=False, default='SIS')
    current_status = Column(String(30), nullable=False, default='pending')
    field_visit_date = Column(Date)
    field_visit_scheduled = Column(Boolean, default=False)
    is_overdue = Column(Boolean, default=False)
    priority_flag = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "current_stage IN ('SIS','SD','DIS','TAHSILDAR','COMPLETED','REJECTED')",
            name='ck_current_stage'
        ),
        CheckConstraint(
            "current_status IN ('pending','in_progress','approved','rejected','escalated')",
            name='ck_current_status'
        ),
        # FIX #5: enforce valid application types at DB level
        CheckConstraint(
            "application_type IN ('ISD','NISD','MERGE')",
            name='ck_application_type'
        ),
        Index('idx_app_officer', 'assigned_officer_id'),
        Index('idx_app_stage', 'current_stage'),
        Index('idx_app_status', 'current_status'),
        Index('idx_app_type', 'application_type'),
        Index('idx_app_submission_date', 'submission_date'),
        # Composite indexes for common query patterns (performance optimization)
        Index('idx_app_officer_status', 'assigned_officer_id', 'current_status'),
        Index('idx_app_officer_overdue', 'assigned_officer_id', 'is_overdue'),
        Index('idx_app_officer_type', 'assigned_officer_id', 'application_type'),
    )

    applicant = relationship("Applicant", back_populates="applications")
    survey_number = relationship("SurveyNumber", back_populates="applications")
    assigned_officer = relationship("SISOfficer", back_populates="assigned_applications")
    application_sub_divisions = relationship("ApplicationSubDivision", back_populates="application", cascade="all, delete-orphan")
    application_documents = relationship("ApplicationDocument", back_populates="application", cascade="all, delete-orphan")
    workflow_history = relationship("WorkflowHistory", back_populates="application", cascade="all, delete-orphan")
    field_visits = relationship("FieldVisit", back_populates="application", cascade="all, delete-orphan")
    patta_transfers = relationship("PattaTransfer", back_populates="application")
    notifications = relationship("Notification", back_populates="application")


class ApplicationSubDivision(Base):
    __tablename__ = "application_sub_divisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False)
    sub_division_id = Column(UUID(as_uuid=True), ForeignKey("sub_divisions.id"), nullable=False)
    proposed_area_sqm = Column(Numeric(12, 2))
    proposed_sub_division_no = Column(String(50))
    status = Column(String(30), default='pending')
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    application = relationship("Application", back_populates="application_sub_divisions")
    sub_division = relationship("SubDivision", back_populates="application_sub_divisions")


class ApplicationDocument(Base):
    __tablename__ = "application_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False)
    document_type = Column(String(100), nullable=False)
    document_name = Column(String(200))
    is_uploaded = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    uploaded_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    application = relationship("Application", back_populates="application_documents")


class WorkflowHistory(Base):
    __tablename__ = "workflow_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False)
    from_stage = Column(String(30))
    to_stage = Column(String(30))
    action = Column(String(100))
    performed_by_officer_id = Column(UUID(as_uuid=True), ForeignKey("sis_officers.id"), nullable=True)
    remarks = Column(Text)
    rejection_reason = Column(Text)
    performed_at = Column(TIMESTAMP(timezone=True), nullable=False, default=_utcnow)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        Index('idx_workflow_app', 'application_id'),
    )

    application = relationship("Application", back_populates="workflow_history")
    performed_by_officer = relationship("SISOfficer", back_populates="workflow_actions")


class FieldVisit(Base):
    __tablename__ = "field_visits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False)
    officer_id = Column(UUID(as_uuid=True), ForeignKey("sis_officers.id"), nullable=False)
    scheduled_date = Column(Date)
    actual_date = Column(Date)
    status = Column(String(20), default='unscheduled')
    visit_notes = Column(Text)
    encroachment_found = Column(Boolean, default=False)
    encroachment_notes = Column(Text)
    area_verified = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('unscheduled','scheduled','completed','overdue','rescheduled','cancelled')",
            name='ck_visit_status'
        ),
        Index('idx_field_visit_officer', 'officer_id'),
        Index('idx_field_visit_status', 'status'),
    )

    application = relationship("Application", back_populates="field_visits")
    officer = relationship("SISOfficer", back_populates="field_visits")


class PattaTransfer(Base):
    __tablename__ = "patta_transfers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"), nullable=False)
    survey_number_id = Column(UUID(as_uuid=True), ForeignKey("survey_numbers.id"), nullable=False)
    sub_division_id = Column(UUID(as_uuid=True), ForeignKey("sub_divisions.id"), nullable=True)
    previous_owner_id = Column(UUID(as_uuid=True), ForeignKey("owners.id"), nullable=False)
    new_owner_id = Column(UUID(as_uuid=True), ForeignKey("owners.id"), nullable=False)
    transfer_order_number = Column(String(100))
    transfer_date = Column(Date)
    tahsildar_signature_date = Column(Date)
    dsc_applied = Column(Boolean, default=False)
    status = Column(String(30), default='pending')
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    application = relationship("Application", back_populates="patta_transfers")
    survey_number = relationship("SurveyNumber", back_populates="patta_transfers")
    sub_division = relationship("SubDivision", back_populates="patta_transfers")
    previous_owner = relationship("Owner", foreign_keys=[previous_owner_id], back_populates="previous_patta_transfers")
    new_owner = relationship("Owner", foreign_keys=[new_owner_id], back_populates="new_patta_transfers")


# ========== NOTIFICATION & AUDIT TABLES ==========

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    officer_id = Column(UUID(as_uuid=True), ForeignKey("sis_officers.id"), nullable=False)
    application_id = Column(UUID(as_uuid=True), ForeignKey("applications.id"), nullable=True)
    title = Column(String(200))
    message = Column(Text)
    is_read = Column(Boolean, default=False)
    notification_type = Column(String(50))
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    officer = relationship("SISOfficer", back_populates="notifications")
    application = relationship("Application", back_populates="notifications")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    officer_id = Column(UUID(as_uuid=True), ForeignKey("sis_officers.id"), nullable=True)
    # FIX #8: denormalised string copy — audit trail survives officer deletion
    officer_employee_id = Column(String(20), nullable=True)
    action = Column(String(200))
    entity_type = Column(String(50))
    entity_id = Column(UUID(as_uuid=True))
    old_values = Column(JSONB)
    new_values = Column(JSONB)
    ip_address = Column(String(50))
    user_agent = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow)

    officer = relationship("SISOfficer", back_populates="audit_logs")


# ========== CHAT TABLES ==========

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    officer_id = Column(UUID(as_uuid=True), ForeignKey("sis_officers.id"), nullable=False)
    session_token = Column(String(100), nullable=False, unique=True)
    started_at = Column(TIMESTAMP(timezone=True), default=_utcnow)
    last_activity = Column(TIMESTAMP(timezone=True))
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    officer = relationship("SISOfficer", back_populates="chat_sessions")
    chat_messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(String(10), nullable=False)        # user, assistant
    content = Column(Text, nullable=False)
    detected_language = Column(String(10), default='en')
    retrieved_context = Column(JSONB)
    # FIX #9: record what DB data was shown so sessions are auditable / replayable
    structured_data = Column(JSONB, nullable=True)
    response_time_ms = Column(Integer)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow)

    __table_args__ = (
        # FIX #6: enforce valid roles at DB level
        CheckConstraint("role IN ('user','assistant')", name='ck_message_role'),
        Index('idx_chat_session', 'session_id'),
    )

    session = relationship("ChatSession", back_populates="chat_messages")


# ========== VECTOR STORE TABLE ==========

class KnowledgeEmbedding(Base):
    """
    Stores document chunks and their vector embeddings for semantic
    similarity search via pgvector.  Replaces ChromaDB as the vector store.

    Embedding dimension : 768  (nomic-embed-text output size)
    Index type          : HNSW cosine similarity
    """
    __tablename__ = "knowledge_embeddings"

    id       = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id = Column(Text, unique=True, nullable=False)
    content  = Column(Text, nullable=False)
    # Vector(768) requires pgvector extension to be installed in PostgreSQL
    embedding = Column(Vector(768), nullable=False)

    # Metadata columns (directly queryable without JSON parsing)
    source   = Column(Text)                          # e.g. "SIS Question Bank"
    category = Column(Text)                          # e.g. "workflow"
    section  = Column(Text)                          # e.g. "Field Visit Scheduling"
    language = Column(Text, default="en")            # en / ta / tanglish
    page     = Column(Integer, default=0)

    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow,
                        onupdate=_utcnow, nullable=False)

    __table_args__ = (
        # HNSW index for fast approximate cosine-distance nearest neighbour
        Index(
            "idx_ke_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("idx_ke_category", "category"),
        Index("idx_ke_language",  "language"),
        Index("idx_ke_chunk_id",  "chunk_id"),
    )
