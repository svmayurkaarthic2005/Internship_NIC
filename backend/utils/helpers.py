"""
Helper utilities and official TamilNilam Urban Schema / Service Codes / District Codes
Streamlined specifically for Sub Inspector Surveyors (SIS) in Urban jurisdictions.
"""
from datetime import datetime, date
from typing import Any, Dict, List, Optional
import uuid

# ========== TAMIL NADU 38 DISTRICTS (Official Codes) ==========
TAMIL_NADU_DISTRICTS = {
    "01": {"name": "Tiruvallur", "name_ta": "திருவள்ளூர்"},
    "02": {"name": "Chennai", "name_ta": "சென்னை"},
    "03": {"name": "Kancheepuram", "name_ta": "காஞ்சிபுரம்"},
    "04": {"name": "Vellore", "name_ta": "வேலூர்"},
    "05": {"name": "Dharmapuri", "name_ta": "தருமபுரி"},
    "06": {"name": "Tiruvannamalai", "name_ta": "திருவண்ணாமலை"},
    "07": {"name": "Villupuram", "name_ta": "விழுப்புரம்"},
    "08": {"name": "Salem", "name_ta": "சேலம்"},
    "09": {"name": "Namakkal", "name_ta": "நாமக்கல்"},
    "10": {"name": "Erode", "name_ta": "ஈரோடு"},
    "11": {"name": "Nilgiris", "name_ta": "நீலகிரி"},
    "12": {"name": "Coimbatore", "name_ta": "கோயம்புத்தூர்"},
    "13": {"name": "Dindigul", "name_ta": "திண்டுக்கல்"},
    "14": {"name": "Karur", "name_ta": "கரூர்"},
    "15": {"name": "Tiruchirappalli", "name_ta": "திருச்சிராப்பள்ளி"},
    "16": {"name": "Perambalur", "name_ta": "பெரம்பலூர்"},
    "17": {"name": "Ariyalur", "name_ta": "அரியலூர்"},
    "18": {"name": "Cuddalore", "name_ta": "கடலூர்"},
    "19": {"name": "Nagapattinam", "name_ta": "நாகப்பட்டினம்"},
    "20": {"name": "Thiruvarur", "name_ta": "திருவாரூர்"},
    "21": {"name": "Thanjavur", "name_ta": "தஞ்சாவூர்"},
    "22": {"name": "Pudukkottai", "name_ta": "புதுக்கோட்டை"},
    "23": {"name": "Sivaganga", "name_ta": "சிவகங்கை"},
    "24": {"name": "Madurai", "name_ta": "மதுரை"},
    "25": {"name": "Theni", "name_ta": "தேனி"},
    "26": {"name": "Virudhunagar", "name_ta": "விருதுநகர்"},
    "27": {"name": "Ramanathapuram", "name_ta": "இராமநாதபுரம்"},
    "28": {"name": "Thoothukudi", "name_ta": "தூத்துக்குடி"},
    "29": {"name": "Tirunelveli", "name_ta": "திருநெல்வேலி"},
    "30": {"name": "Kanniyakumari", "name_ta": "கன்னியாகுமரி"},
    "31": {"name": "Krishnagiri", "name_ta": "கிருஷ்ணகிரி"},
    "32": {"name": "Tiruppur", "name_ta": "திருப்பூர்"},
    "33": {"name": "Kallakurichi", "name_ta": "கள்ளக்குறிச்சி"},
    "34": {"name": "Tenkasi", "name_ta": "தென்காசி"},
    "35": {"name": "Chengalpattu", "name_ta": "செங்கல்பட்டு"},
    "36": {"name": "Tirupattur", "name_ta": "திருப்பத்தூர்"},
    "37": {"name": "Ranipet", "name_ta": "இராணிப்பேட்டை"},
    "38": {"name": "Mayiladuthurai", "name_ta": "மயிலாடுதுறை"},
}

# ========== SIS URBAN SERVICE CODES (Complete official TamilNilam Urban list) ==========
SIS_URBAN_SERVICES = {
    "0153": {"name": "Not Involving Subdivision",                     "short": "NISD",             "category": "Urban", "requires_field_visit": False},
    "0154": {"name": "Involving Subdivision",                         "short": "ISD",              "category": "Urban", "requires_field_visit": True},
    "0155": {"name": "Merge Subdivisions",                            "short": "MERGE",            "category": "Urban", "requires_field_visit": True},
    "0156": {"name": "TSLR Extract with Sketch",                      "short": "TSLR_SKETCH",      "category": "Urban", "requires_field_visit": False},
    "0157": {"name": "TSLR Extract Only",                             "short": "TSLR_EXTRACT",     "category": "Urban", "requires_field_visit": False},
    "0158": {"name": "Modification / Anadeenam",                      "short": "MODIFICATION",     "category": "Urban", "requires_field_visit": True},
    "0159": {"name": "Addition",                                       "short": "ADDITION",         "category": "Urban", "requires_field_visit": True},
    "0160": {"name": "Deletion",                                       "short": "DELETION",         "category": "Urban", "requires_field_visit": True},
    "0161": {"name": "Street Master",                                  "short": "STREET_MASTER",    "category": "Urban", "requires_field_visit": False},
    "0162": {"name": "Street and Door Number Modification",            "short": "STREET_DOOR_MOD",  "category": "Urban", "requires_field_visit": False},
    "0163": {"name": "Block/Revoke Town Survey Number",               "short": "REVOKE_TSN",       "category": "Urban", "requires_field_visit": False},
    "0164": {"name": "ULC Land Subdivision",                          "short": "ULC_SUBDIV",       "category": "Urban", "requires_field_visit": True},
    "0165": {"name": "TSLR Owner Name Correction",                    "short": "NAME_CORRECTION",  "category": "Urban", "requires_field_visit": False},
    "0167": {"name": "TSLR Settlement - Owner Entry",                 "short": "SETTLE_OWNER",     "category": "Urban", "requires_field_visit": False},
    "0168": {"name": "TSLR Settlement - Subdivision",                 "short": "SETTLE_SUBDIV",    "category": "Urban", "requires_field_visit": True},
    "0169": {"name": "Govt to Private",                               "short": "GOVT_TO_PRIVATE",  "category": "Urban", "requires_field_visit": True},
    "0170": {"name": "TSLR Sketch Verification",                      "short": "SKETCH_VERIFY",    "category": "Urban", "requires_field_visit": True},
    "0171": {"name": "Rural-Urban Correlation",                       "short": "RURAL_URBAN_CORR", "category": "Urban", "requires_field_visit": True},
    "0172": {"name": "Town Settlement",                               "short": "TOWN_SETTLE",      "category": "Urban", "requires_field_visit": True},
    "0173": {"name": "Temple Land Unblock",                           "short": "TEMPLE_UNBLOCK",   "category": "Urban", "requires_field_visit": True},
    "0175": {"name": "Block Change",                                  "short": "BLOCK_CHANGE",     "category": "Urban", "requires_field_visit": False},
    "0176": {"name": "Govt to Govt Poramboke",                        "short": "GOVT_PORAMBOKE",   "category": "Urban", "requires_field_visit": True},
    "0178": {"name": "F-Line (Urban Demarcation)",                    "short": "F_LINE",           "category": "Urban", "requires_field_visit": True},
    "0179": {"name": "Settlement Govt to Private",                    "short": "SETTLE_GOVT_PVT",  "category": "Urban", "requires_field_visit": True},
    "0181": {"name": "Settlement Modification",                       "short": "SETTLE_MOD",       "category": "Urban", "requires_field_visit": True},
    "0183": {"name": "Settlement Govt Poramboke to Private (GO-506)", "short": "SETTLE_PORM_PVT",  "category": "Urban", "requires_field_visit": True},
    "0184": {"name": "TSR Preparation Subdivision",                   "short": "TSR_PREP_SUBDIV",  "category": "Urban", "requires_field_visit": True},
    "0185": {"name": "F-Line Appeal",                                  "short": "F_LINE_APPEAL",    "category": "Urban", "requires_field_visit": True},
    "0187": {"name": "Register Patta",                                "short": "REG_PATTA",        "category": "Urban", "requires_field_visit": False},
    "0188": {"name": "Natham Settlement",                             "short": "NATHAM_SETTLE",    "category": "Urban", "requires_field_visit": True},
}


# ========== ESSENTIAL TABLE FIELDS REQUIRED FOR SIS WORKFLOW ==========
SIS_REQUIRED_COLUMNS = [
    "application_id",             # Application Number (e.g. 2026/0154/02/000001)
    "service_code",               # Urban Service Code (0154, 0153, 0155, 0156, etc.)
    "application_type",           # ISD, NISD, MERGE
    "district_code",              # District Code (e.g. 02 for Chennai)
    "taluk_code",                 # Taluk Code (e.g. CHN-AMB)
    "urban_unit_code",            # Town / Urban Unit Code
    "ward_code",                  # Ward Number (e.g. 12)
    "block_code",                 # Block Number (e.g. B1)
    "survey_number",              # Town Survey Number (TS No)
    "subdivision_number",         # Subdivision Number (e.g. 145/1A)
    "current_subdivision_number", # Target New Subdivision
    "patta_number",               # Patta / TSLR Register Number
    "application_date",           # Submission Date
    "application_status",         # pending, in_progress, approved, rejected
    "workflow_state",             # SIS, SD, DIS, TAHSILDAR, COMPLETED
    "last_updated_datetime",      # Last modification timestamp
    "user_id",                    # Assigned SIS Officer ID
    "sale_deed_number",           # Registered Sale Deed Number
    "sale_deed_registered",       # Boolean flag
    "declared_reason",            # sale, inheritance, partition, gift_deed
    "field_visit_date",           # Scheduled/Actual Field Visit Date
    "field_visit_scheduled",      # Boolean flag
    "is_overdue",                 # Boolean flag
    "priority_flag",              # Priority flag
    "source_code",                # CSC, CITIZEN, IGRS
    "can_number",                 # Citizen Access Number
    "notes"                       # Officer Inspection Remarks / Notes
]


def generate_application_number(application_type: str = "NISD", district_code: str = "02", year: int = None, submission_date: date = None) -> str:
    """
    Generate unique application number for SIS Urban jurisdiction.
    Format: YYYY/URBAN_SERVICE_CODE/DISTRICT_CODE/SEQUENCE
    Example: 2026/0154/02/000001
    """
    if submission_date and hasattr(submission_date, 'year'):
        year = submission_date.year
    if not year:
        year = datetime.now().year
    
    service_code = "0153"
    if application_type:
        app_t = str(application_type).upper()
        if app_t == "ISD":
            service_code = "0154"
        elif app_t == "MERGE":
            service_code = "0155"
        elif app_t == "NISD":
            service_code = "0153"
        elif app_t in SIS_URBAN_SERVICES:
            service_code = app_t
            
    dist_code = str(district_code).zfill(2)
    unique_part = str(uuid.uuid4().int)[:6].zfill(6)
    return f"{year}/{service_code}/{dist_code}/{unique_part}"


def calculate_days_between(start_date: date, end_date: date = None) -> int:
    """Calculate days between two dates. If end_date is None, use today."""
    if end_date is None:
        end_date = date.today()
    return (end_date - start_date).days


def is_overdue(submission_date: date, threshold_days: int = 30) -> bool:
    """Check if an application is overdue based on submission date."""
    days_elapsed = calculate_days_between(submission_date)
    return days_elapsed > threshold_days


def format_area(area_sqm: float, unit: str = "sqm") -> str:
    """Format area with proper unit."""
    if unit == "sqm":
        return f"{area_sqm:.2f} sq.m"
    elif unit == "sqft":
        return f"{area_sqm * 10.7639:.2f} sq.ft"
    elif unit == "cent":
        return f"{area_sqm / 40.4686:.2f} cents"
    elif unit == "ground":
        return f"{area_sqm / 222.967:.2f} grounds"
    return f"{area_sqm:.2f}"
