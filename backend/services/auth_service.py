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
    
    Returns dict with:
    - jurisdiction_type: str (district/taluk/town/ward/block)
    - district_ids: List[UUID]
    - taluk_ids: List[UUID]
    - town_ids: List[UUID]
    - ward_ids: List[UUID]
    - block_ids: List[UUID]
    - jurisdiction_name: str (human-readable name)
    """
    # Get officer's jurisdiction assignment
    result = await db.execute(
        select(OfficerJurisdiction)
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
    
    # Resolve based on jurisdiction level
    if jurisdiction_type == "district":
        # Officer has district-level access - get all child entities
        district_ids = [jurisdiction.district_id]
        
        # Get district name
        district_result = await db.execute(
            select(District).where(District.id == jurisdiction.district_id)
        )
        district = district_result.scalar_one_or_none()
        jurisdiction_name = f"{district.name} District" if district else "Unknown District"
        
        # Get all taluks in district
        taluk_result = await db.execute(
            select(Taluk).where(Taluk.district_id == jurisdiction.district_id)
        )
        taluks = taluk_result.scalars().all()
        taluk_ids = [t.id for t in taluks]
        
        # Get all towns in those taluks
        if taluk_ids:
            town_result = await db.execute(
                select(Town).where(Town.taluk_id.in_(taluk_ids))
            )
            towns = town_result.scalars().all()
            town_ids = [t.id for t in towns]
        
        # Get all wards in those towns
        if town_ids:
            ward_result = await db.execute(
                select(Ward).where(Ward.town_id.in_(town_ids))
            )
            wards = ward_result.scalars().all()
            ward_ids = [w.id for w in wards]
        
        # Get all blocks in those wards
        if ward_ids:
            block_result = await db.execute(
                select(Block).where(Block.ward_id.in_(ward_ids))
            )
            blocks = block_result.scalars().all()
            block_ids = [b.id for b in blocks]
    
    elif jurisdiction_type == "taluk":
        # Officer has taluk-level access
        taluk_ids = [jurisdiction.taluk_id]
        district_ids = [jurisdiction.district_id]
        
        # Get taluk name
        taluk_result = await db.execute(
            select(Taluk).where(Taluk.id == jurisdiction.taluk_id)
        )
        taluk = taluk_result.scalar_one_or_none()
        jurisdiction_name = f"{taluk.name} Taluk" if taluk else "Unknown Taluk"
        
        # Get all towns in taluk
        town_result = await db.execute(
            select(Town).where(Town.taluk_id == jurisdiction.taluk_id)
        )
        towns = town_result.scalars().all()
        town_ids = [t.id for t in towns]
        
        # Get all wards in those towns
        if town_ids:
            ward_result = await db.execute(
                select(Ward).where(Ward.town_id.in_(town_ids))
            )
            wards = ward_result.scalars().all()
            ward_ids = [w.id for w in wards]
        
        # Get all blocks in those wards
        if ward_ids:
            block_result = await db.execute(
                select(Block).where(Block.ward_id.in_(ward_ids))
            )
            blocks = block_result.scalars().all()
            block_ids = [b.id for b in blocks]
    
    elif jurisdiction_type == "town":
        # Officer has town-level access
        town_ids = [jurisdiction.town_id]
        taluk_ids = [jurisdiction.taluk_id]
        district_ids = [jurisdiction.district_id]
        
        # Get town name
        town_result = await db.execute(
            select(Town).where(Town.id == jurisdiction.town_id)
        )
        town = town_result.scalar_one_or_none()
        jurisdiction_name = f"{town.name} Town" if town else "Unknown Town"
        
        # Get all wards in town
        ward_result = await db.execute(
            select(Ward).where(Ward.town_id == jurisdiction.town_id)
        )
        wards = ward_result.scalars().all()
        ward_ids = [w.id for w in wards]
        
        # Get all blocks in those wards
        if ward_ids:
            block_result = await db.execute(
                select(Block).where(Block.ward_id.in_(ward_ids))
            )
            blocks = block_result.scalars().all()
            block_ids = [b.id for b in blocks]
    
    elif jurisdiction_type == "ward":
        # Officer has ward-level access
        ward_ids = [jurisdiction.ward_id]
        town_ids = [jurisdiction.town_id]
        taluk_ids = [jurisdiction.taluk_id]
        district_ids = [jurisdiction.district_id]
        
        # Get ward name
        ward_result = await db.execute(
            select(Ward).where(Ward.id == jurisdiction.ward_id)
        )
        ward = ward_result.scalar_one_or_none()
        jurisdiction_name = f"{ward.ward_name or 'Ward ' + ward.ward_number}" if ward else "Unknown Ward"
        
        # Get all blocks in ward
        block_result = await db.execute(
            select(Block).where(Block.ward_id == jurisdiction.ward_id)
        )
        blocks = block_result.scalars().all()
        block_ids = [b.id for b in blocks]
    
    elif jurisdiction_type == "block":
        # Officer has block-level access
        block_ids = [jurisdiction.block_id]
        ward_ids = [jurisdiction.ward_id]
        town_ids = [jurisdiction.town_id]
        taluk_ids = [jurisdiction.taluk_id]
        district_ids = [jurisdiction.district_id]
        
        # Get block name
        block_result = await db.execute(
            select(Block).where(Block.id == jurisdiction.block_id)
        )
        block = block_result.scalar_one_or_none()
        jurisdiction_name = f"{block.block_name or 'Block ' + block.block_number}" if block else "Unknown Block"
    
    return {
        "jurisdiction_type": jurisdiction_type,
        "jurisdiction_name": jurisdiction_name,
        "district_ids": district_ids,
        "taluk_ids": taluk_ids,
        "town_ids": town_ids,
        "ward_ids": ward_ids,
        "block_ids": block_ids
    }
