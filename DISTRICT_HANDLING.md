# District Handling in SIS Chatbot

> **Updated:** districts and taluks are no longer tables of their own. They are
> the TAMILNILAM master tables `district_unicode` and `taluk`, loaded from the
> pg_dumps in `backend/sample_table/`; the app's duplicate `districts` /
> `taluks` were dropped by `backend/sample_db/adopt_master_district_taluk.py`.
> The ORM attribute names are unchanged (`District.id`, `Taluk.district_id`, …)
> because the migration added the surrogate identity to the masters as
> `app_uid` / `district_uid`. Sections below that show `__tablename__ =
> "districts"` or `ForeignKey("districts.id")` describe the schema before that
> change. See the "Layer 0" section of CLAUDE.md for the current layout.

## Overview

The SIS Chatbot has a **dedicated `districts` table** and a comprehensive geography hierarchy to handle district-level data and officer jurisdictions.

---

## Geography Hierarchy

The system implements a **5-level geography hierarchy**:

```
District → Taluk → Town → Ward → Block → Survey Numbers
```

### Table Structure

Each level is represented by its own table:

| Table | Key Columns | Relationships |
|-------|-------------|---------------|
| `districts` | id (UUID), name, district_code | → taluks, officer_jurisdictions |
| `taluk` (master) | app_uid (UUID), district_uid, taluk_ename, taluk_code | ← district_unicode, → towns |
| `towns` | id (UUID), taluk_id, name, town_code | ← taluk, → wards |
| `wards` | id (UUID), town_id, ward_number, ward_name | ← towns, → blocks |
| `blocks` | id (UUID), ward_id, block_number, block_name | ← wards, → survey_numbers |

---

## Districts Table Schema

```python
class District(Base):
    __tablename__ = "districts"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False, unique=True)
    district_code = Column(String(10), nullable=False, unique=True)
    created_at = Column(TIMESTAMP(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    
    # Relationships
    taluks = relationship("Taluk", back_populates="district", cascade="all, delete-orphan")
    officer_jurisdictions = relationship("OfficerJurisdiction", back_populates="district")
```

**Key Features:**
- UUID primary key
- Unique district name and code
- Timestamp tracking (created_at, updated_at)
- Cascading relationships with taluks and officer jurisdictions

---

## Current Database State

### Districts in Database

Currently, the database contains **1 district**:

| District Name | District Code |
|---------------|---------------|
| Thoothukudi | 28 |

**Full Hierarchy:**
- **District**: Thoothukudi (28)
  - **Taluk**: Thoothukudi (01)
    - **Town**: Thoothukudi (001)
      - **Wards**: 002, 102, 103 (with applications)
        - **Block**: 0015
          - **Survey Numbers**: 1300s series (ward 002), low series (wards 102/103)

---

## Tamil Nadu District Codes

The system maintains a **complete mapping of all 38 Tamil Nadu districts** in `backend/config.py`:

```python
DISTRICT_CODE_MAP = {
    "01": "Tiruvallur",
    "02": "Chennai",
    "03": "Kancheepuram",
    # ... (38 total districts)
    "28": "Thoothukudi",  # Current test district
    # ...
    "38": "Mayiladuthurai"
}
```

This mapping is used for:
- Validating district codes in applications
- Display names in the UI
- Conversion between codes and names

---

## Officer Jurisdiction System

Officers are assigned jurisdictions at different levels of the geography hierarchy.

### Jurisdiction Types

| Type | Level | Scope |
|------|-------|-------|
| `district` | Broadest | Entire district |
| `taluk` | Mid-level | Taluk within a district |
| `town` | Mid-level | Town within a taluk |
| `ward` | Narrower | Ward within a town |
| `block` | Narrowest | Block within a ward |

### OfficerJurisdiction Table

```python
class OfficerJurisdiction(Base):
    __tablename__ = "officer_jurisdictions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    officer_id = Column(UUID(as_uuid=True), ForeignKey("sis_officers.id"), nullable=False)
    jurisdiction_type = Column(String(20), nullable=False)  # district/taluk/town/ward/block
    
    # Geography FKs (only one needs to be set based on jurisdiction_type)
    district_id = Column(UUID(as_uuid=True), ForeignKey("districts.id"), nullable=True)
    taluk_id = Column(UUID(as_uuid=True), ForeignKey("taluks.id"), nullable=True)
    town_id = Column(UUID(as_uuid=True), ForeignKey("towns.id"), nullable=True)
    ward_id = Column(UUID(as_uuid=True), ForeignKey("wards.id"), nullable=True)
    block_id = Column(UUID(as_uuid=True), ForeignKey("blocks.id"), nullable=True)
    
    # Constraints
    CheckConstraint(
        "district_id IS NOT NULL OR taluk_id IS NOT NULL OR town_id IS NOT NULL "
        "OR ward_id IS NOT NULL OR block_id IS NOT NULL",
        name='ck_jurisdiction_not_empty'
    )
    CheckConstraint(
        "jurisdiction_type IN ('district','taluk','town','ward','block')",
        name='ck_jurisdiction_type'
    )
```

### Current Officer Jurisdictions

All 3 test officers have **WARD-level** jurisdiction:

| Officer ID | Name | Jurisdiction Type | District | Ward |
|------------|------|-------------------|----------|------|
| SIS-001 | Csenthil | ward | Thoothukudi (28) | 002 |
| SIS-002 | Msivakumar | ward | Thoothukudi (28) | 102 |
| SIS-003 | Muthulakshmis | ward | Thoothukudi (28) | 103 |

**Distribution:**
- Ward-level officers: 3
- Taluk-level officers: 0
- District-level officers: 0

---

## How District Filtering Works

### 1. **Application Assignment**

Every application is linked to a district through the geography hierarchy:

```
Application → Survey Number → Block → Ward → Town → Taluk → District
```

### 2. **Officer Access Control**

The chatbot filters applications based on officer jurisdiction:

```python
# From backend/services/chatbot.py
_JUR_LEVELS = {
    "district": 0,  # Sees entire district
    "taluk": 1,     # Sees entire taluk
    "town": 2,      # Sees entire town
    "ward": 3,      # Sees entire ward
    "block": 4      # Sees only their block
}
```

### 3. **Query Filtering**

When an officer queries for applications:
- **Ward-level officer**: Only sees applications in their assigned ward(s)
- **District-level officer**: Would see all applications in the district
- **Queries with date ranges**: Drop `current_stage` filter to show historical data across the register

### 4. **District Code Usage**

District codes appear in:
- Application IDs: `2025/0154/28/000001` (format: YYYY/SERVICE_CODE/DISTRICT_CODE/SEQUENCE)
- CSV extracts: `district_code` column in all geography-related tables
- Workflow routing: Ensures applications route to correct district offices

---

## Adding More Districts

To add additional districts:

1. **Insert into districts table:**
   ```sql
   INSERT INTO districts (id, name, district_code, created_at, updated_at)
   VALUES (
       gen_random_uuid(),
       'Chennai',
       '02',
       NOW(),
       NOW()
   );
   ```

2. **Create geography hierarchy:**
   - Add taluks for the district
   - Add towns for each taluk
   - Add wards for each town
   - Add blocks for each ward

3. **Assign officers:**
   - Create officer records
   - Link them via `officer_jurisdictions` table

4. **Seed applications:**
   - Applications will automatically link to district through survey numbers

---

## Key Constraints & Rules

1. **District codes are strings** to preserve leading zeros (e.g., "01", "02", "28")
2. **One district per officer jurisdiction** (but officers can have multiple jurisdictions)
3. **Geography hierarchy is strictly enforced** via foreign keys
4. **Cascading deletes** ensure referential integrity
5. **CheckConstraints** validate jurisdiction types and ensure at least one geography FK is set

---

## Related Files

- `backend/models.py` - District ORM model definition
- `backend/config.py` - District code mapping (38 TN districts)
- `backend/services/chatbot.py` - Jurisdiction filtering logic
- `backend/services/postgres.py` - Database query handlers with geography filters
- `backend/sample_db/build_app_tables.py` - Geography table population from CSV extracts

---

## Testing District Functionality

Run the district check script:

```bash
python check_districts.py
```

This shows:
- All districts in the database
- Officer jurisdiction distribution by type
- Detailed officer-to-geography mappings
