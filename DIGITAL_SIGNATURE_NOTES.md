# Digital Signature Implementation Notes

## Current Status

✅ **Test Questions Created**: `test_questions_digital_signature.txt` (50 questions)
✅ **Query Handler Added**: `get_digital_signature_details()` in `backend/services/postgres.py`
❌ **Not Yet Implemented**: Digital signature data is in CSV but not ingested into database

## Available Data (CSV Extracts)

### 1. Parcel Digital Signatures (`uaregmap_ds_demo.csv`)
- **Records**: 1,036 parcel signature records
- **Columns**:
  - `digital_signature_content` — JSON data (NOT base64 PKCS#7)
  - `signed_datetime` — timestamp when signed
  - `nic_dsign_flag` — empty for all records
  - `document_hash` — hash of the document
  
### 2. Owner Digital Signatures (`uchitta_nathammap_ds_demo.csv`)
- **Records**: 439 owner signature records
- **Columns**:
  - `signature_content` — JSON owner data (NOT cryptographic signature)
  - `nic_digital_signature` — empty for all records (intended for actual PKCS#7 signature)
  - `signed_by_username` — e.g., `tut_ramyadevi`
  - `signed_datetime` — e.g., `2022-12-15 17:37:43.465`

## Current Behavior

When users ask digital signature questions (e.g., "Is patta 7585 digitally signed?"), the chatbot will:

1. ✅ Recognize the intent (falls under `general_query` for now)
2. ✅ Call `get_digital_signature_details()` function
3. ⚠️ Return explanation that feature is not yet implemented
4. ℹ️ Provide guidance on how to enable the feature

## To Fully Implement

### Step 1: Create ORM Models

```python
# backend/models.py

class PattaSignature(Base):
    __tablename__ = "patta_signatures"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    district_code = Column(String(2), nullable=False)
    taluk_code = Column(String(2), nullable=False)
    town_code = Column(String(3), nullable=False)
    ward_code = Column(String(3), nullable=False)
    block_code = Column(String(4), nullable=False)
    patta_number = Column(String(20), nullable=False, index=True)
    survey_number = Column(String(10), nullable=True)
    subdivision_number = Column(String(10), nullable=True)
    form6_number = Column(String(20), nullable=True)
    form8_number = Column(String(20), nullable=True)
    document_hash = Column(Text, nullable=True)
    digital_signature_content = Column(Text, nullable=True)  # JSON metadata
    signed_datetime = Column(TIMESTAMP(timezone=True), nullable=True, index=True)
    signed_by_username = Column(String(100), nullable=True, index=True)
    nic_digital_signature = Column(Text, nullable=True)  # Future: actual PKCS#7 signature
    nic_dsign_flag = Column(String(1), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.now(timezone.utc))

class NathamSignature(Base):
    __tablename__ = "natham_signatures"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    district_code = Column(String(2), nullable=False)
    taluk_code = Column(String(2), nullable=False)
    town_code = Column(String(3), nullable=False)
    ward_code = Column(String(3), nullable=False)
    block_code = Column(String(4), nullable=False)
    patta_number = Column(String(20), nullable=False, index=True)
    door_number = Column(String(20), nullable=True)
    form6_number = Column(String(20), nullable=True)
    document_hash = Column(Text, nullable=True)
    signature_content = Column(Text, nullable=True)  # JSON owner metadata
    signed_datetime = Column(TIMESTAMP(timezone=True), nullable=True, index=True)
    signed_by_username = Column(String(100), nullable=True, index=True)
    nic_digital_signature = Column(Text, nullable=True)  # Future: actual PKCS#7 signature
    verified_datetime = Column(TIMESTAMP(timezone=True), nullable=True)
    username_verify = Column(String(100), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.now(timezone.utc))
```

### Step 2: Add Ingest Logic to seed.py

```python
# backend/seed.py (add to seed_database function)

async def seed_digital_signatures(db: AsyncSession):
    """Ingest digital signature data from CSV files"""
    logger.info("Seeding digital signature data...")
    
    # Ingest parcel signatures from uaregmap_ds_demo.csv
    parcel_sig_file = "backend/sample_table/uaregmap_ds_demo.csv"
    if os.path.exists(parcel_sig_file):
        with open(parcel_sig_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            parcel_sigs = []
            for row in reader:
                parcel_sigs.append(PattaSignature(
                    district_code=row['district_code'],
                    taluk_code=row['taluk_code'],
                    town_code=row['town_code'],
                    ward_code=row['ward_code'],
                    block_code=row['block_code'],
                    patta_number=row.get('patta_number', ''),
                    survey_number=row.get('survey_number'),
                    subdivision_number=row.get('subdivision_number'),
                    form6_number=row.get('form6_number'),
                    form8_number=row.get('form8_number'),
                    document_hash=row.get('document_hash'),
                    digital_signature_content=row.get('digital_signature_content'),
                    signed_datetime=_parse_datetime(row.get('signed_datetime')),
                    signed_by_username=row.get('username'),
                    nic_dsign_flag=row.get('nic_dsign_flag'),
                ))
            db.add_all(parcel_sigs)
            await db.commit()
            logger.info(f"Seeded {len(parcel_sigs)} parcel signatures")
    
    # Ingest owner signatures from uchitta_nathammap_ds_demo.csv
    natham_sig_file = "backend/sample_table/uchitta_nathammap_ds_demo.csv"
    if os.path.exists(natham_sig_file):
        with open(natham_sig_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            natham_sigs = []
            for row in reader:
                natham_sigs.append(NathamSignature(
                    district_code=row['district_code'],
                    taluk_code=row['taluk_code'],
                    town_code=row['town_code'],
                    ward_code=row['ward_code'],
                    block_code=row['block_code'],
                    patta_number=row.get('patta_number', ''),
                    door_number=row.get('door_number'),
                    form6_number=row.get('form6_number'),
                    document_hash=row.get('document_hash'),
                    signature_content=row.get('signature_content'),
                    signed_datetime=_parse_datetime(row.get('signed_datetime')),
                    signed_by_username=row.get('signed_by_username'),
                    verified_datetime=_parse_datetime(row.get('verified_datetime')),
                    username_verify=row.get('username_verify'),
                ))
            db.add_all(natham_sigs)
            await db.commit()
            logger.info(f"Seeded {len(natham_sigs)} owner signatures")
```

### Step 3: Update postgres.py Query Function

Replace the placeholder in `get_digital_signature_details()` with actual database queries:

```python
# backend/services/postgres.py

async def get_digital_signature_details(
    db: AsyncSession,
    officer: OfficerContext,
    patta_number: Optional[str] = None,
    survey_number: Optional[str] = None,
    subdivision_number: Optional[str] = None,
    signed_by: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """Query digital signature details from database"""
    try:
        signatures = []
        
        # Query parcel signatures
        if patta_number or survey_number or subdivision_number:
            parcel_query = select(PattaSignature)
            filters = []
            if patta_number:
                filters.append(PattaSignature.patta_number == patta_number)
            if survey_number:
                filters.append(PattaSignature.survey_number == survey_number)
            if subdivision_number:
                filters.append(PattaSignature.subdivision_number == subdivision_number)
            if signed_by:
                filters.append(PattaSignature.signed_by_username.ilike(f"%{signed_by}%"))
            if start_date:
                filters.append(PattaSignature.signed_datetime >= start_date)
            if end_date:
                filters.append(PattaSignature.signed_datetime <= end_date)
            
            parcel_query = parcel_query.where(and_(*filters))
            result = await db.execute(parcel_query)
            parcel_sigs = result.scalars().all()
            
            for sig in parcel_sigs:
                signatures.append({
                    "type": "parcel",
                    "patta_number": sig.patta_number,
                    "survey_number": sig.survey_number,
                    "subdivision_number": sig.subdivision_number,
                    "signed_by": sig.signed_by_username,
                    "signed_datetime": sig.signed_datetime.isoformat() if sig.signed_datetime else None,
                    "has_nic_signature": bool(sig.nic_digital_signature),
                })
        
        return {
            "found": len(signatures) > 0,
            "count": len(signatures),
            "signatures": signatures,
            "query_type": "Digital Signature Query"
        }
        
    except Exception as e:
        logger.error(f"Error querying digital signature details: {e}")
        return {
            "found": False,
            "error": str(e),
            "query_type": "Digital Signature Query"
        }
```

### Step 4: Add Intent to rag.py

```python
# backend/services/rag.py (in parse_intent function)

# Digital signature queries
if any(w in msg for w in ["digital signature", "digitally signed", "signature", "signed",
                           "டிஜிட்டல் கையொப்பம்", "கையொப்பம்", "கையொப்பமிட்டது",
                           "digital sign", "sign panna"]):
    if any(w in msg for w in ["patta", "பட்டா", "survey", "சர்வே", "parcel"]):
        return "digital_signature_check"
```

### Step 5: Add Chatbot Handler

```python
# backend/services/chatbot.py (in process_chat_stream function)

elif intent == "digital_signature_check":
    # Extract patta number or survey number from message
    patta_match = re.search(r'\bpatta\s*(?:number|no\.?)?\s*(\d+)\b', message, re.IGNORECASE)
    survey_match = re.search(r'\bsurvey\s*(?:number|no\.?)?\s*(\d+)\b', message, re.IGNORECASE)
    
    patta_num = patta_match.group(1) if patta_match else None
    survey_num = survey_match.group(1) if survey_match else None
    
    structured_data = await get_digital_signature_details(
        db, officer,
        patta_number=patta_num,
        survey_number=survey_num
    )
    structured_data["query_type"] = "Digital Signature Status"
```

## Test Questions Created

Created `test_questions_digital_signature.txt` with 50 questions covering:
- Patta digital signature status (15 questions)
- Parcel/survey number signatures (10 questions)
- Signature by officer name (10 questions)
- Signature date queries (10 questions)
- General signature queries (5 questions)

## Important Notes

1. **JSON Metadata Only**: Current signature_content fields contain JSON metadata, NOT actual cryptographic PKCS#7 signatures
2. **nic_digital_signature Empty**: The column intended for actual digital signatures is currently empty
3. **Signed By Username**: Officer usernames like `tut_ramyadevi` are recorded in signed_by_username
4. **Date Range**: Signatures date from 2022 (e.g., `2022-12-15 17:37:43.465`)
5. **Counts**: 1,036 parcel signatures + 439 owner signatures = 1,475 total signature records

## Related Documentation

- CSV files: `backend/sample_table/uaregmap_ds_demo.csv`, `backend/sample_table/uchitta_nathammap_ds_demo.csv`
- Test questions: `test_questions_digital_signature.txt`
- Query function: `backend/services/postgres.py::get_digital_signature_details()`
