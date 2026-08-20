"""
Comprehensive 200-Question Test Suite for SIS Chatbot
Covers all 39 TamilNilam Urban schema columns:
  1. serial_number
  2. user_id
  3. department_code
  4. service_code
  5. district_code
  6. taluk_code
  7. village_code
  8. urban_unit_code
  9. ward_code
  10. block_code
  11. application_date
  12. application_status
  13. last_updated_datetime
  14. application_id
  15. survey_number
  16. subdivision_number
  17. patta_number
  18. role_id
  19. source_code
  20. csc_service_charge
  21. government_service_charge
  22. can_number
  23. dispatch_date
  24. received_date
  25. ip_address
  26. generated_datetime
  27. source_name
  28. renewal_number
  29. workflow_state
  30. igrs_form6_number
  31. return_status
  32. current_subdivision_number
  33. parent_application_id
  34. auto_mutated_flag
  35. is_auto_mutated
  36. igrs_auto_mutation_flag
  37. camp_flag
  38. camp_correction_id
  39. camp_code
"""

import asyncio
import sys
import time
import uuid
from uuid import UUID
from datetime import datetime

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

sys.path.insert(0, '.')

from backend.database import AsyncSessionLocal
from backend.models import SISOfficer
from backend.schemas import OfficerContext
from backend.services.auth_service import get_officer_jurisdiction_ids
from backend.services.chatbot import process_chat
from sqlalchemy import select

# 200 Questions covering the 39 fields
TEST_QUESTIONS_200 = [
    # ── Category 1: Application Identifiers & Core Metadata (Q1 - Q25) ──
    {"id": 1, "column": "application_id", "lang": "en", "q": "Show details for application 2026/0154/02/000001"},
    {"id": 2, "column": "application_id", "lang": "ta", "q": "விண்ணப்ப எண் 2026/0154/02/000001 இன் முழு விவரங்களைத் தருக"},
    {"id": 3, "column": "application_id", "lang": "en", "q": "What is the application type of 2026/0153/02/000002?"},
    {"id": 4, "column": "application_id", "lang": "ta", "q": "2026/0155/02/000003 விண்ணப்பத்தின் வகை என்ன?"},
    {"id": 5, "column": "serial_number", "lang": "en", "q": "What is the serial number of application 2026/0154/02/000001?"},
    {"id": 6, "column": "serial_number", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்தின் வரிசை எண் (serial number) என்ன?"},
    {"id": 7, "column": "parent_application_id", "lang": "en", "q": "Does application 2026/0154/02/000001 have a parent application id?"},
    {"id": 8, "column": "parent_application_id", "lang": "ta", "q": "2026/0155/02/000003 விண்ணப்பத்திற்கு parent_application_id உள்ளதா?"},
    {"id": 9, "column": "renewal_number", "lang": "en", "q": "Show renewal number for application 2026/0153/02/000002"},
    {"id": 10, "column": "renewal_number", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்தின் புதுப்பித்தல் எண் (renewal_number) என்ன?"},
    {"id": 11, "column": "user_id", "lang": "en", "q": "Which officer user_id is assigned to application 2026/0154/02/000001?"},
    {"id": 12, "column": "user_id", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பம் எந்த அதிகாரிக்கு (user_id) ஒதுக்கப்பட்டுள்ளது?"},
    {"id": 13, "column": "role_id", "lang": "en", "q": "What role_id is handling application 2026/0154/02/000001?"},
    {"id": 14, "column": "role_id", "lang": "ta", "q": "விண்ணப்பம் 2026/0154/02/000001 இன் அதிகாரியின் role_id என்ன?"},
    {"id": 15, "column": "application_id", "lang": "en", "q": "List all applications currently assigned to me"},
    {"id": 16, "column": "application_id", "lang": "ta", "q": "எனக்கு ஒதுக்கப்பட்ட அனைத்து விண்ணப்பங்களையும் பட்டியலிடுங்கள்"},
    {"id": 17, "column": "application_id", "lang": "en", "q": "How many total applications are in my jurisdiction?"},
    {"id": 18, "column": "application_id", "lang": "ta", "q": "எனது அதிகார வரம்பில் உள்ள மொத்த விண்ணப்பங்களின் எண்ணிக்கை எவ்வளவு?"},
    {"id": 19, "column": "application_id", "lang": "en", "q": "Give me summary of application 2026/0154/02/000001"},
    {"id": 20, "column": "application_id", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்தின் சுருக்கத்தை வழங்கவும்"},
    {"id": 21, "column": "application_id", "lang": "en", "q": "Check status of application 2026/0153/02/000002"},
    {"id": 22, "column": "application_id", "lang": "ta", "q": "2026/0153/02/000002 விண்ணப்பத்தின் தற்போதைய நிலையைச் சரிபார்க்கவும்"},
    {"id": 23, "column": "application_id", "lang": "en", "q": "Find application 2026/0155/02/000003"},
    {"id": 24, "column": "application_id", "lang": "ta", "q": "2026/0155/02/000003 விண்ணப்பத்தைக் கண்டறியவும்"},
    {"id": 25, "column": "parent_application_id", "lang": "en", "q": "List all merge applications with parent application linkages"},

    # ── Category 2: Geographic & Administrative Hierarchy Codes (Q26 - Q55) ──
    {"id": 26, "column": "district_code", "lang": "en", "q": "What is the official district code for Chennai?"},
    {"id": 27, "column": "district_code", "lang": "ta", "q": "சென்னை மாவட்டத்தின் district_code என்ன?"},
    {"id": 28, "column": "district_code", "lang": "en", "q": "What is the district code of Tiruvallur?"},
    {"id": 29, "column": "district_code", "lang": "ta", "q": "திருவள்ளூர் மாவட்ட குறியீடு என்ன?"},
    {"id": 30, "column": "district_code", "lang": "en", "q": "Show applications for district_code 02"},
    {"id": 31, "column": "district_code", "lang": "ta", "q": "மாவட்டம் 02 இல் உள்ள விண்ணப்பங்களைக் காட்டு"},
    {"id": 32, "column": "taluk_code", "lang": "en", "q": "Show applications under taluk Ambattur"},
    {"id": 33, "column": "taluk_code", "lang": "ta", "q": "அம்பத்தூர் வட்டத்தில் உள்ள விண்ணப்பங்களைக் காட்டு"},
    {"id": 34, "column": "taluk_code", "lang": "en", "q": "What is the taluk_code for Ambattur?"},
    {"id": 35, "column": "taluk_code", "lang": "ta", "q": "அம்பத்தூர் வட்டத்தின் taluk_code என்ன?"},
    {"id": 36, "column": "urban_unit_code", "lang": "en", "q": "Show applications under urban_unit_code AMB-T01"},
    {"id": 37, "column": "urban_unit_code", "lang": "ta", "q": "நகர்ப்புற அலகு குறியீடு (urban_unit_code) AMB-T01 விண்ணப்பங்களைக் காட்டு"},
    {"id": 38, "column": "village_code", "lang": "en", "q": "What is the village_code associated with survey 145?"},
    {"id": 39, "column": "village_code", "lang": "ta", "q": "சர்வே 145 இன் கிராம குறியீடு (village_code) என்ன?"},
    {"id": 40, "column": "ward_code", "lang": "en", "q": "List all applications in Ward 12"},
    {"id": 41, "column": "ward_code", "lang": "ta", "q": "வார்டு 12 இல் உள்ள அனைத்து விண்ணப்பங்களையும் பட்டியலிடுங்கள்"},
    {"id": 42, "column": "ward_code", "lang": "en", "q": "Show survey numbers in Ward 12"},
    {"id": 43, "column": "ward_code", "lang": "ta", "q": "வார்டு 12 இல் உள்ள புல எண்களைக் காட்டு"},
    {"id": 44, "column": "ward_code", "lang": "en", "q": "What is the ward_code for application 2026/0154/02/000001?"},
    {"id": 45, "column": "ward_code", "lang": "ta", "q": "2026/0154/02/000001 இன் வார்டு எண் என்ன?"},
    {"id": 46, "column": "block_code", "lang": "en", "q": "List applications in Block B1"},
    {"id": 47, "column": "block_code", "lang": "ta", "q": "பிளாக் B1 இல் உள்ள விண்ணப்பங்களைக் காட்டு"},
    {"id": 48, "column": "block_code", "lang": "en", "q": "How many applications are pending in Block B1?"},
    {"id": 49, "column": "block_code", "lang": "ta", "q": "பிளாக் B1 இல் எத்தனை விண்ணப்பங்கள் நிலுவையில் உள்ளன?"},
    {"id": 50, "column": "block_code", "lang": "en", "q": "Show all survey numbers in Block B1"},
    {"id": 51, "column": "block_code", "lang": "ta", "q": "பிளாக் B1 இல் உள்ள அனைத்து சர்வே எண்களையும் காட்டு"},
    {"id": 52, "column": "district_code", "lang": "en", "q": "What is the district code of Coimbatore?"},
    {"id": 53, "column": "district_code", "lang": "ta", "q": "கோயம்புத்தூர் மாவட்ட குறியீடு என்ன?"},
    {"id": 54, "column": "district_code", "lang": "en", "q": "What is the district code of Madurai?"},
    {"id": 55, "column": "district_code", "lang": "ta", "q": "மதுரை மாவட்ட குறியீடு என்ன?"},

    # ── Category 3: Service & Department Codes (Q56 - Q75) ──
    {"id": 56, "column": "service_code", "lang": "en", "q": "What does service_code 0154 represent?"},
    {"id": 57, "column": "service_code", "lang": "ta", "q": "சேவை குறியீடு (service_code) 0154 எதனைக் குறிக்கிறது?"},
    {"id": 58, "column": "service_code", "lang": "en", "q": "What does service_code 0153 mean?"},
    {"id": 59, "column": "service_code", "lang": "ta", "q": "சேவை குறியீடு 0153 என்றால் என்ன?"},
    {"id": 60, "column": "service_code", "lang": "en", "q": "What is service_code 0155 in Urban TamilNilam?"},
    {"id": 61, "column": "service_code", "lang": "ta", "q": "தமிழ்நிலம் நகர்ப்புறத்தில் சேவை குறியீடு 0155 என்ன?"},
    {"id": 62, "column": "service_code", "lang": "en", "q": "List all ISD applications (service_code 0154)"},
    {"id": 63, "column": "service_code", "lang": "ta", "q": "அனைத்து ISD விண்ணப்பங்களையும் காட்டு (0154)"},
    {"id": 64, "column": "service_code", "lang": "en", "q": "List all NISD applications (service_code 0153)"},
    {"id": 65, "column": "service_code", "lang": "ta", "q": "அனைத்து NISD விண்ணப்பங்களையும் காட்டு (0153)"},
    {"id": 66, "column": "service_code", "lang": "en", "q": "List all MERGE applications (service_code 0155)"},
    {"id": 67, "column": "service_code", "lang": "ta", "q": "அனைத்து MERGE விண்ணப்பங்களையும் காட்டு (0155)"},
    {"id": 68, "column": "department_code", "lang": "en", "q": "What is the department_code for survey and settlement department?"},
    {"id": 69, "column": "department_code", "lang": "ta", "q": "சர்வே துறையின் department_code என்ன?"},
    {"id": 70, "column": "source_code", "lang": "en", "q": "Show applications with source_code CITIZEN"},
    {"id": 71, "column": "source_code", "lang": "ta", "q": "பொதுமக்கள் நேரடியாக சமர்ப்பித்த (CITIZEN) விண்ணப்பங்களைக் காட்டு"},
    {"id": 72, "column": "source_code", "lang": "en", "q": "Show applications with source_code CSC"},
    {"id": 73, "column": "source_code", "lang": "ta", "q": "இ-சேவை மையம் (CSC) மூலம் பெறப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 74, "column": "source_name", "lang": "en", "q": "What is the source_name of application 2026/0154/02/000001?"},
    {"id": 75, "column": "source_name", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்தின் ஆதாரம் (source_name) என்ன?"},

    # ── Category 4: Land & Survey Identifiers (Q76 - Q105) ──
    {"id": 76, "column": "survey_number", "lang": "en", "q": "Show survey details for survey 145"},
    {"id": 77, "column": "survey_number", "lang": "ta", "q": "புல எண் 145 இன் விவரங்களைத் தருக"},
    {"id": 78, "column": "survey_number", "lang": "en", "q": "What is the survey_number for application 2026/0154/02/000001?"},
    {"id": 79, "column": "survey_number", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்தின் சர்வே எண் என்ன?"},
    {"id": 80, "column": "survey_number", "lang": "en", "q": "Who is the owner of survey number 145?"},
    {"id": 81, "column": "survey_number", "lang": "ta", "q": "புல எண் 145 இன் உரிமையாளர் யார்?"},
    {"id": 82, "column": "survey_number", "lang": "en", "q": "What is the total area of survey 145?"},
    {"id": 83, "column": "survey_number", "lang": "ta", "q": "புல எண் 145 இன் மொத்த பரப்பளவு என்ன?"},
    {"id": 84, "column": "subdivision_number", "lang": "en", "q": "What is the next subdivision number for survey 145?"},
    {"id": 85, "column": "subdivision_number", "lang": "ta", "q": "புல எண் 145 க்கான அடுத்த உட்பிரிவு எண் (next subdivision number) என்ன?"},
    {"id": 86, "column": "subdivision_number", "lang": "en", "q": "What are the existing subdivisions for survey 145?"},
    {"id": 87, "column": "subdivision_number", "lang": "ta", "q": "புல எண் 145 இல் உள்ள உட்பிரிவுகள் என்ன?"},
    {"id": 88, "column": "current_subdivision_number", "lang": "en", "q": "What is the current_subdivision_number proposed for 2026/0154/02/000001?"},
    {"id": 89, "column": "current_subdivision_number", "lang": "ta", "q": "2026/0154/02/000001 இல் முன்மொழியப்பட்ட உட்பிரிவு எண் (current_subdivision_number) என்ன?"},
    {"id": 90, "column": "patta_number", "lang": "en", "q": "What is the patta_number for survey 145?"},
    {"id": 91, "column": "patta_number", "lang": "ta", "q": "புல எண் 145 இன் பட்டா எண் என்ன?"},
    {"id": 92, "column": "patta_number", "lang": "en", "q": "What is the patta_number for application 2026/0154/02/000001?"},
    {"id": 93, "column": "patta_number", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்தின் பட்டா எண் என்ன?"},
    {"id": 94, "column": "survey_number", "lang": "en", "q": "Does survey 145 have any encroachment or litigation?"},
    {"id": 95, "column": "survey_number", "lang": "ta", "q": "புல எண் 145 இல் ஆக்கிரமிப்பு அல்லது வழக்கு உள்ளதா?"},
    {"id": 96, "column": "survey_number", "lang": "en", "q": "Show details for survey 146"},
    {"id": 97, "column": "survey_number", "lang": "ta", "q": "சர்வே 146 விவரங்களைக் காட்டு"},
    {"id": 98, "column": "subdivision_number", "lang": "en", "q": "Show next available subdivision number for survey 146"},
    {"id": 99, "column": "subdivision_number", "lang": "ta", "q": "புல எண் 146 இன் அடுத்த உட்பிரிவு எண் என்ன?"},
    {"id": 100, "column": "survey_number", "lang": "en", "q": "Show all survey numbers in my jurisdiction"},
    {"id": 101, "column": "survey_number", "lang": "ta", "q": "எனது அதிகார வரம்பில் உள்ள அனைத்து சர்வே எண்களையும் காட்டு"},
    {"id": 102, "column": "patta_number", "lang": "en", "q": "Find surveys linked to patta number 101"},
    {"id": 103, "column": "patta_number", "lang": "ta", "q": "பட்டா எண் 101 உடன் இணைக்கப்பட்ட சர்வே எண்களைக் காட்டு"},
    {"id": 104, "column": "subdivision_number", "lang": "en", "q": "Check subdivision 145/1A details"},
    {"id": 105, "column": "subdivision_number", "lang": "ta", "q": "உட்பிரிவு 145/1A விவரங்களைச் சரிபார்க்கவும்"},

    # ── Category 5: Date, Timeline & Timestamp Fields (Q106 - Q130) ──
    {"id": 106, "column": "application_date", "lang": "en", "q": "When was application 2026/0154/02/000001 submitted?"},
    {"id": 107, "column": "application_date", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பம் எப்போது சமர்ப்பிக்கப்பட்டது?"},
    {"id": 108, "column": "application_date", "lang": "en", "q": "Show applications submitted in July 2026"},
    {"id": 109, "column": "application_date", "lang": "ta", "q": "ஜூலை 2026 இல் சமர்ப்பிக்கப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 110, "column": "application_date", "lang": "en", "q": "Show applications submitted between 2026-07-01 and 2026-07-29"},
    {"id": 111, "column": "application_date", "lang": "ta", "q": "2026-07-01 முதல் 2026-07-29 வரை சமர்ப்பிக்கப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 112, "column": "received_date", "lang": "en", "q": "What is the received_date of application 2026/0154/02/000001?"},
    {"id": 113, "column": "received_date", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பம் பெறப்பட்ட தேதி (received_date) என்ன?"},
    {"id": 114, "column": "dispatch_date", "lang": "en", "q": "What is the dispatch_date for application 2026/0154/02/000001?"},
    {"id": 115, "column": "dispatch_date", "lang": "ta", "q": "2026/0154/02/000001 அனுப்பப்பட்ட தேதி (dispatch_date) என்ன?"},
    {"id": 116, "column": "last_updated_datetime", "lang": "en", "q": "When was application 2026/0154/02/000001 last updated?"},
    {"id": 117, "column": "last_updated_datetime", "lang": "ta", "q": "2026/0154/02/000001 கடைசியாக எப்போது புதுப்பிக்கப்பட்டது (last_updated)?"},
    {"id": 118, "column": "generated_datetime", "lang": "en", "q": "What is the generated_datetime for application 2026/0154/02/000001?"},
    {"id": 119, "column": "generated_datetime", "lang": "ta", "q": "2026/0154/02/000001 உருவான தேதி/நேரம் (generated_datetime) என்ன?"},
    {"id": 120, "column": "application_date", "lang": "en", "q": "Show applications submitted in June 2026"},
    {"id": 121, "column": "application_date", "lang": "ta", "q": "ஜூன் 2026 இல் சமர்ப்பிக்கப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 122, "column": "application_date", "lang": "en", "q": "What year was application 2026/0154/02/000001 submitted?"},
    {"id": 123, "column": "application_date", "lang": "ta", "q": "2026/0154/02/000001 சமர்ப்பிக்கப்பட்ட ஆண்டு என்ன?"},
    {"id": 124, "column": "received_date", "lang": "en", "q": "List applications received this month"},
    {"id": 125, "column": "received_date", "lang": "ta", "q": "இந்த மாதம் பெறப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 126, "column": "dispatch_date", "lang": "en", "q": "Show all completed applications with dispatch dates"},
    {"id": 127, "column": "dispatch_date", "lang": "ta", "q": "முடிவடைந்து அனுப்பப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 128, "column": "last_updated_datetime", "lang": "en", "q": "List applications updated in the last 7 days"},
    {"id": 129, "column": "last_updated_datetime", "lang": "ta", "q": "கடந்த 7 நாட்களில் புதுப்பிக்கப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 130, "column": "application_date", "lang": "en", "q": "How many applications were submitted in July 2026?"},

    # ── Category 6: Status, Workflow States & Return Status (Q131 - Q155) ──
    {"id": 131, "column": "application_status", "lang": "en", "q": "What is the application_status of 2026/0154/02/000001?"},
    {"id": 132, "column": "application_status", "lang": "ta", "q": "2026/0154/02/000001 இன் நிலை (status) என்ன?"},
    {"id": 133, "column": "application_status", "lang": "en", "q": "List all pending applications"},
    {"id": 134, "column": "application_status", "lang": "ta", "q": "நிலுவையில் உள்ள அனைத்து விண்ணப்பங்களையும் காட்டு"},
    {"id": 135, "column": "application_status", "lang": "en", "q": "How many applications are pending?"},
    {"id": 136, "column": "application_status", "lang": "ta", "q": "எத்தனை விண்ணப்பங்கள் நிலுவையில் உள்ளன?"},
    {"id": 137, "column": "application_status", "lang": "en", "q": "List all approved applications"},
    {"id": 138, "column": "application_status", "lang": "ta", "q": "அங்கீகரிக்கப்பட்ட அனைத்து விண்ணப்பங்களையும் காட்டு"},
    {"id": 139, "column": "application_status", "lang": "en", "q": "List all rejected applications"},
    {"id": 140, "column": "application_status", "lang": "ta", "q": "நிராகரிக்கப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 141, "column": "application_status", "lang": "en", "q": "Show overdue applications in my jurisdiction"},
    {"id": 142, "column": "application_status", "lang": "ta", "q": "காலதாமதமான விண்ணப்பங்களைக் காட்டு"},
    {"id": 143, "column": "workflow_state", "lang": "en", "q": "What is the workflow_state of 2026/0154/02/000001?"},
    {"id": 144, "column": "workflow_state", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்தின் தற்போதைய கட்டம் (workflow_state) என்ன?"},
    {"id": 145, "column": "workflow_state", "lang": "en", "q": "Show applications currently at SIS workflow state"},
    {"id": 146, "column": "workflow_state", "lang": "ta", "q": "SIS கட்டத்தில் உள்ள விண்ணப்பங்களைக் காட்டு"},
    {"id": 147, "column": "workflow_state", "lang": "en", "q": "Show applications forwarded to Tahsildar"},
    {"id": 148, "column": "workflow_state", "lang": "ta", "q": "தாசில்தார் அலுவலகத்தில் உள்ள விண்ணப்பங்களைக் காட்டு"},
    {"id": 149, "column": "workflow_state", "lang": "en", "q": "Where is application 2026/0154/02/000001 right now?"},
    {"id": 150, "column": "workflow_state", "lang": "ta", "q": "2026/0154/02/000001 தற்போது எந்த அலுவலகத்தில் உள்ளது?"},
    {"id": 151, "column": "return_status", "lang": "en", "q": "What is the return_status of application 2026/0154/02/000001?"},
    {"id": 152, "column": "return_status", "lang": "ta", "q": "2026/0154/02/000001 இன் திருப்பி அனுப்பிய நிலை (return_status) என்ன?"},
    {"id": 153, "column": "return_status", "lang": "en", "q": "Show all applications with return_status flagged as returned for clarification"},
    {"id": 154, "column": "return_status", "lang": "ta", "q": "விளக்கத்திற்காக திருப்பி அனுப்பப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 155, "column": "workflow_state", "lang": "en", "q": "Show applications at DIS review stage"},

    # ── Category 7: Financial & Fee Details (Q156 - Q170) ──
    {"id": 156, "column": "csc_service_charge", "lang": "en", "q": "What is the csc_service_charge for application 2026/0154/02/000001?"},
    {"id": 157, "column": "csc_service_charge", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்திற்கான CSC சேவை கட்டணம் (csc_service_charge) என்ன?"},
    {"id": 158, "column": "government_service_charge", "lang": "en", "q": "What is the government_service_charge for application 2026/0154/02/000001?"},
    {"id": 159, "column": "government_service_charge", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்திற்கான அரசு சேவை கட்டணம் என்ன?"},
    {"id": 160, "column": "csc_service_charge", "lang": "en", "q": "What is the standard fee for ISD application (service_code 0154)?"},
    {"id": 161, "column": "csc_service_charge", "lang": "ta", "q": "ISD விண்ணப்பத்திற்கான கட்டண விவரங்கள் என்ன?"},
    {"id": 162, "column": "government_service_charge", "lang": "en", "q": "What is the government fee for NISD mutation (service_code 0153)?"},
    {"id": 163, "column": "government_service_charge", "lang": "ta", "q": "NISD பட்டா மாறுதலுக்கான அரசு கட்டணம் என்ன?"},
    {"id": 164, "column": "csc_service_charge", "lang": "en", "q": "Show fee breakdown for application 2026/0154/02/000001"},
    {"id": 165, "column": "csc_service_charge", "lang": "ta", "q": "2026/0154/02/000001 இன் கட்டண விவரங்களைக் காட்டு"},
    {"id": 166, "column": "government_service_charge", "lang": "en", "q": "Is survey fee paid for application 2026/0154/02/000001?"},
    {"id": 167, "column": "government_service_charge", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்திற்கு சர்வே கட்டணம் செலுத்தப்பட்டுள்ளதா?"},
    {"id": 168, "column": "csc_service_charge", "lang": "en", "q": "What are the CSC charges for TSLR extract services?"},
    {"id": 169, "column": "csc_service_charge", "lang": "ta", "q": "TSLR சான்றிதழுக்கான இ-சேவை கட்டணம் என்ன?"},
    {"id": 170, "column": "government_service_charge", "lang": "en", "q": "Show total service charges collected for July 2026"},

    # ── Category 8: Citizen & External Integration (Q171 - Q185) ──
    {"id": 171, "column": "can_number", "lang": "en", "q": "What is the can_number for application 2026/0154/02/000001?"},
    {"id": 172, "column": "can_number", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்தின் குடிமக்கள் அணுகல் எண் (can_number) என்ன?"},
    {"id": 173, "column": "can_number", "lang": "en", "q": "Find application associated with CAN number CAN-000001"},
    {"id": 174, "column": "can_number", "lang": "ta", "q": "CAN எண் CAN-000001 இன் விண்ணப்பத்தைக் காட்டு"},
    {"id": 175, "column": "igrs_form6_number", "lang": "en", "q": "What is the igrs_form6_number for application 2026/0154/02/000001?"},
    {"id": 176, "column": "igrs_form6_number", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்தின் igrs_form6_number என்ன?"},
    {"id": 177, "column": "igrs_form6_number", "lang": "en", "q": "Show applications integrated with IGRS Form 6"},
    {"id": 178, "column": "igrs_form6_number", "lang": "ta", "q": "IGRS படிவம் 6 மூலம் பெறப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 179, "column": "ip_address", "lang": "en", "q": "What is the ip_address from which application 2026/0154/02/000001 was submitted?"},
    {"id": 180, "column": "ip_address", "lang": "ta", "q": "2026/0154/02/000001 சமர்ப்பிக்கப்பட்ட IP முகவரி (ip_address) என்ன?"},
    {"id": 181, "column": "can_number", "lang": "en", "q": "Show citizen details and CAN number for 2026/0153/02/000002"},
    {"id": 182, "column": "can_number", "lang": "ta", "q": "2026/0153/02/000002 விண்ணப்பதாரரின் CAN எண் என்ன?"},
    {"id": 183, "column": "igrs_form6_number", "lang": "en", "q": "Check registered sale deed and IGRS Form 6 status for 2026/0154/02/000001"},
    {"id": 184, "column": "igrs_form6_number", "lang": "ta", "q": "2026/0154/02/000001 இன் பத்திரப் பதிவு மற்றும் IGRS விவரங்களைச் சரிபார்க்கவும்"},
    {"id": 185, "column": "ip_address", "lang": "en", "q": "Are there any submission audit logs with IP addresses for application 2026/0154/02/000001?"},

    # ── Category 9: Auto-Mutation & Special Camp Flags (Q186 - Q200) ──
    {"id": 186, "column": "auto_mutated_flag", "lang": "en", "q": "Is application 2026/0154/02/000001 marked with auto_mutated_flag?"},
    {"id": 187, "column": "auto_mutated_flag", "lang": "ta", "q": "2026/0154/02/000001 விண்ணப்பத்திற்கு auto_mutated_flag உள்ளதா?"},
    {"id": 188, "column": "is_auto_mutated", "lang": "en", "q": "Show applications where is_auto_mutated is True"},
    {"id": 189, "column": "is_auto_mutated", "lang": "ta", "q": "தானியங்கி பட்டா மாறுதல் (is_auto_mutated) செய்யப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 190, "column": "igrs_auto_mutation_flag", "lang": "en", "q": "What is the igrs_auto_mutation_flag for application 2026/0154/02/000001?"},
    {"id": 191, "column": "igrs_auto_mutation_flag", "lang": "ta", "q": "2026/0154/02/000001 இன் igrs_auto_mutation_flag என்ன?"},
    {"id": 192, "column": "camp_flag", "lang": "en", "q": "Show applications marked with camp_flag"},
    {"id": 193, "column": "camp_flag", "lang": "ta", "q": "சிறப்பு முகாம் (camp_flag) மூலம் பெறப்பட்ட விண்ணப்பங்களைக் காட்டு"},
    {"id": 194, "column": "camp_flag", "lang": "en", "q": "Is application 2026/0154/02/000001 processed under a special camp?"},
    {"id": 195, "column": "camp_flag", "lang": "ta", "q": "2026/0154/02/000001 சிறப்பு முகாம் விண்ணப்பமா?"},
    {"id": 196, "column": "camp_correction_id", "lang": "en", "q": "What is the camp_correction_id for application 2026/0154/02/000001?"},
    {"id": 197, "column": "camp_correction_id", "lang": "ta", "q": "2026/0154/02/000001 இன் முகாம் திருத்த எண் (camp_correction_id) என்ன?"},
    {"id": 198, "column": "camp_code", "lang": "en", "q": "What is the camp_code for special revenue camps in Ambattur?"},
    {"id": 199, "column": "camp_code", "lang": "ta", "q": "அம்பத்தூர் வட்ட சிறப்பு முகாம் குறியீடு (camp_code) என்ன?"},
    {"id": 200, "column": "camp_code", "lang": "en", "q": "List all applications received under camp_code CMP-2026-01"}
]


async def run_200_test_suite():
    print("=" * 80)
    print("RUNNING 200-QUESTION TEST SUITE ACROSS ALL 39 TAMILNILAM SCHEMA COLUMNS")
    print("=" * 80)
    print(f"Total Questions: {len(TEST_QUESTIONS_200)}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("-" * 80)

    async with AsyncSessionLocal() as db:
        # Load test SIS Officer (Arjun Kumar - Block SIS)
        result = await db.execute(
            select(SISOfficer).where(SISOfficer.email == "arjun.kumar@sis.tn.gov.in")
        )
        officer_model = result.scalar_one_or_none()
        if not officer_model:
            # Fallback to first officer
            result = await db.execute(select(SISOfficer).limit(1))
            officer_model = result.scalar_one()

        jurisdiction_data = await get_officer_jurisdiction_ids(officer_model.id, db)
        all_jur_ids = (
            jurisdiction_data["district_ids"] +
            jurisdiction_data["taluk_ids"] +
            jurisdiction_data["town_ids"] +
            jurisdiction_data["ward_ids"] +
            jurisdiction_data["block_ids"]
        )

        officer = OfficerContext(
            officer_id=officer_model.id,
            employee_id=officer_model.employee_id,
            name=officer_model.name,
            name_tamil=officer_model.name_tamil,
            email=officer_model.email,
            designation=officer_model.designation,
            jurisdiction_type=jurisdiction_data.get("jurisdiction_type", "block"),
            jurisdiction_name=jurisdiction_data.get("jurisdiction_name", "Block B1"),
            jurisdiction_ids=all_jur_ids,
            district_ids=jurisdiction_data["district_ids"],
            taluk_ids=jurisdiction_data["taluk_ids"],
            town_ids=jurisdiction_data["town_ids"],
            ward_ids=jurisdiction_data["ward_ids"],
            block_ids=jurisdiction_data["block_ids"],
            is_active=officer_model.is_active
        )

        print(f"Running tests as Officer: {officer.name} ({officer.employee_id})")
        print(f"Jurisdiction Type: {officer.jurisdiction_type}")
        print("-" * 80)

        passed = 0
        failed = 0
        latencies = []
        results = []

        session_id = str(uuid.uuid4())

        for idx, item in enumerate(TEST_QUESTIONS_200, 1):
            q_id = item["id"]
            col = item["column"]
            lang = item["lang"]
            query = item["q"]

            t0 = time.time()
            try:
                chat_res = await process_chat(
                    message=query,
                    session_id=session_id,
                    officer=officer,
                    db=db,
                    chat_history=[]
                )
                elapsed_ms = int((time.time() - t0) * 1000)
                latencies.append(elapsed_ms)

                resp_text = chat_res.get("response", "")
                intent = chat_res.get("intent", "unknown")
                detected_lang = chat_res.get("language", "unknown")

                # Validation criteria: response is not empty, does not throw unhandled exception
                is_pass = bool(resp_text and len(resp_text.strip()) > 0)
                
                if is_pass:
                    passed += 1
                    status_symbol = "[PASS]"
                else:
                    failed += 1
                    status_symbol = "[FAIL]"

                preview = resp_text.replace("\n", " ")[:65]
                try:
                    print(f"[{q_id:3d}/200] {status_symbol} ({col:25s}) [{lang.upper()}] {elapsed_ms:5d}ms | Q: {query[:40]}... -> {preview}...")
                except Exception:
                    print(f"[{q_id:3d}/200] {status_symbol} ({col:25s}) [{lang.upper()}] {elapsed_ms:5d}ms")

                results.append({
                    "id": q_id,
                    "column": col,
                    "language": lang,
                    "query": query,
                    "status": "PASS" if is_pass else "FAIL",
                    "intent": intent,
                    "latency_ms": elapsed_ms,
                    "response": resp_text
                })

            except Exception as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                failed += 1
                try:
                    print(f"[{q_id:3d}/200] [ERROR] ({col:25s}) [{lang.upper()}] {elapsed_ms:5d}ms | Q: {query[:40]}... -> Error: {e}")
                except Exception:
                    print(f"[{q_id:3d}/200] [ERROR] ({col:25s}) [{lang.upper()}] {elapsed_ms:5d}ms")
                results.append({
                    "id": q_id,
                    "column": col,
                    "language": lang,
                    "query": query,
                    "status": "ERROR",
                    "intent": "exception",
                    "latency_ms": elapsed_ms,
                    "error": str(e)
                })

        # Summary statistics
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        print("=" * 80)
        print("TEST SUITE SUMMARY REPORT")
        print("=" * 80)
        print(f"Total Questions Evaluated : 200")
        print(f"Passed                    : {passed} / 200 ({passed/200*100:.1f}%)")
        print(f"Failed                    : {failed} / 200")
        print(f"Average Latency           : {avg_latency:.2f} ms")
        print(f"Min Latency               : {min(latencies) if latencies else 0} ms")
        print(f"Max Latency               : {max(latencies) if latencies else 0} ms")
        print("=" * 80)

        # Breakdown by 39 schema columns
        print("\nBreakdown by 39 Schema Columns:")
        col_counts = {}
        for r in results:
            c = r["column"]
            col_counts[c] = col_counts.get(c, {"pass": 0, "total": 0})
            col_counts[c]["total"] += 1
            if r["status"] == "PASS":
                col_counts[c]["pass"] += 1

        for c, st in sorted(col_counts.items()):
            print(f"  - {c:28s}: {st['pass']}/{st['total']} passed ({st['pass']/st['total']*100:.0f}%)")

        # Save results to JSON
        import json
        with open("backend/test_200_results.json", "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "total": len(results),
                "passed": passed,
                "failed": failed,
                "avg_latency_ms": avg_latency,
                "column_breakdown": col_counts,
                "results": results
            }, f, indent=2, ensure_ascii=False)
        print("\n[OK] Detailed test report saved to backend/test_200_results.json")

        return results


if __name__ == "__main__":
    asyncio.run(run_200_test_suite())

