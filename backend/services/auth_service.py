"""
Authentication service - password hashing, JWT token generation, jurisdiction resolution
"""
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from uuid import UUID
from jose import jwt, JWTError

# Workaround for passlib + bcrypt >= 4.0.0 bug where passlib checks for a wraparound bug and crashes.
# Newer bcrypt versions raise ValueError if password is > 72 bytes, whereas older ones silently truncated.
import bcrypt
if not hasattr(bcrypt, "__about__"):
    class DummyAbout:
        __version__ = "4.0.1"
    bcrypt.__about__ = DummyAbout

orig_hashpw = bcrypt.hashpw
def patched_hashpw(password, salt):
    if isinstance(password, str):
        password = password.encode('utf-8')
    if len(password) > 72:
        password = password[:72]
    return orig_hashpw(password, salt)
bcrypt.hashpw = patched_hashpw

from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.config import settings
from backend.models import (
    SISOfficer, OfficerJurisdiction, District, Taluk, Town, Ward, Block
)


# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against its hash
    """
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    """
    Hash a plain password (alias for get_password_hash for consistency)
    """
    return pwd_context.hash(password)


def get_password_hash(password: str) -> str:
    """
    Hash a plain password
    """
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create JWT access token
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[Dict]:
    """
    Decode and verify JWT token
    Returns payload dict if valid, None if invalid
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_officer_jurisdiction_ids(officer_id: UUID, db: AsyncSession) -> Dict:
    """
    Resolve all jurisdiction IDs the officer can access based on their assigned jurisdiction.
    
    OPTIMIZED: Uses single query with joins instead of multiple sequential queries.
    
    Returns dict with:
    - jurisdiction_type: str (district/taluk/town/ward/block)
    - district_ids: List[UUID]
    - taluk_ids: List[UUID]
    - town_ids: List[UUID]
    - ward_ids: List[UUID]
    - block_ids: List[UUID]
    - jurisdiction_name: str (human-readable name)
    """
    from sqlalchemy.orm import selectinload
    
    # Get officer's jurisdiction assignment with eager loading
    result = await db.execute(
        select(OfficerJurisdiction)
        .options(
            selectinload(OfficerJurisdiction.district),
            selectinload(OfficerJurisdiction.taluk),
            selectinload(OfficerJurisdiction.town),
            selectinload(OfficerJurisdiction.ward),
            selectinload(OfficerJurisdiction.block)
        )
        .where(OfficerJurisdiction.officer_id == officer_id)
    )
    jurisdictions = result.scalars().all()
    
    if not jurisdictions:
        return {
            "jurisdiction_type": "none",
            "jurisdiction_name": "No Jurisdiction",
            "district_ids": [],
            "taluk_ids": [],
            "town_ids": [],
            "ward_ids": [],
            "block_ids": []
        }
    
    # For simplicity, take the first jurisdiction (officers typically have one primary jurisdiction)
    jurisdiction = jurisdictions[0]
    
    jurisdiction_type = jurisdiction.jurisdiction_type
    district_ids = []
    taluk_ids = []
    town_ids = []
    ward_ids = []
    block_ids = []
    jurisdiction_name = ""
    
    # Resolve based on jurisdiction level using optimized queries
    if jurisdiction_type == "district":
        # Officer has district-level access
        district_ids = [jurisdiction.district_id]
        jurisdiction_name = f"{jurisdiction.district.name} District" if jurisdiction.district else "Unknown District"
        
        # Get ALL child entities in ONE query using joins
        result = await db.execute(
            select(Taluk.id, Town.id, Ward.id, Block.id)
            .select_from(Taluk)
            .join(Town, Town.taluk_id == Taluk.id, isouter=True)
            .join(Ward, Ward.town_id == Town.id, isouter=True)
            .join(Block, Block.ward_id == Ward.id, isouter=True)
            .where(Taluk.district_id == jurisdiction.district_id)
        )
        
        for taluk_id, town_id, ward_id, block_id in result.all():
            if taluk_id and taluk_id not in taluk_ids:
                taluk_ids.append(taluk_id)
            if town_id and town_id not in town_ids:
                town_ids.append(town_id)
            if ward_id and ward_id not in ward_ids:
                ward_ids.append(ward_id)
            if block_id and block_id not in block_ids:
                block_ids.append(block_id)
    
    elif jurisdiction_type == "taluk":
        # Officer has taluk-level access
        taluk_ids = [jurisdiction.taluk_id]
        district_ids = [jurisdiction.district_id]
        jurisdiction_name = f"{jurisdiction.taluk.name} Taluk" if jurisdiction.taluk else "Unknown Taluk"
        
        # Get all child entities in ONE query
        result = await db.execute(
            select(Town.id, Ward.id, Block.id)
            .select_from(Town)
            .join(Ward, Ward.town_id == Town.id, isouter=True)
            .join(Block, Block.ward_id == Ward.id, isouter=True)
            .where(Town.taluk_id == jurisdiction.taluk_id)
        )
        
        for town_id, ward_id, block_id in result.all():
            if town_id and town_id not in town_ids:
                town_ids.append(town_id)
            if ward_id and ward_id not in ward_ids:
                ward_ids.append(ward_id)
            if block_id and block_id not in block_ids:
                block_ids.append(block_id)
    
    elif jurisdiction_type == "town":
        # Officer has town-level access
        town_ids = [jurisdiction.town_id]
        taluk_ids = [jurisdiction.taluk_id]
        district_ids = [jurisdiction.district_id]
        jurisdiction_name = f"{jurisdiction.town.name} Town" if jurisdiction.town else "Unknown Town"
        
        # Get all child entities in ONE query
        result = await db.execute(
            select(Ward.id, Block.id)
            .select_from(Ward)
            .join(Block, Block.ward_id == Ward.id, isouter=True)
            .where(Ward.town_id == jurisdiction.town_id)
        )
        
        for ward_id, block_id in result.all():
            if ward_id and ward_id not in ward_ids:
                ward_ids.append(ward_id)
            if block_id and block_id not in block_ids:
                block_ids.append(block_id)
    
    elif jurisdiction_type == "ward":
        # Officer has ward-level access
        ward_ids = [jurisdiction.ward_id]
        town_ids = [jurisdiction.town_id]
        taluk_ids = [jurisdiction.taluk_id]
        district_ids = [jurisdiction.district_id]
        
        ward = jurisdiction.ward
        jurisdiction_name = f"{ward.ward_name or 'Ward ' + str(ward.ward_number)}" if ward else "Unknown Ward"
        
        # Get all blocks in ONE query
        result = await db.execute(
            select(Block.id).where(Block.ward_id == jurisdiction.ward_id)
        )
        block_ids = [row[0] for row in result.all()]
    
    elif jurisdiction_type == "block":
        # Officer has block-level access
        block_ids = [jurisdiction.block_id]
        ward_ids = [jurisdiction.ward_id]
        town_ids = [jurisdiction.town_id]
        taluk_ids = [jurisdiction.taluk_id]
        district_ids = [jurisdiction.district_id]
        
        block = jurisdiction.block
        jurisdiction_name = f"{block.block_name or 'Block ' + str(block.block_number)}" if block else "Unknown Block"
    
    return {
        "jurisdiction_type": jurisdiction_type,
        "jurisdiction_name": jurisdiction_name,
        "district_ids": district_ids,
        "taluk_ids": taluk_ids,
        "town_ids": town_ids,
        "ward_ids": ward_ids,
        "block_ids": block_ids
    }
