"""
Evaluation Dataset for SIS Chatbot.
Covers all 14 evaluation categories using synthetic, anonymized domain data.
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


class EvaluationTestCase(BaseModel):
    """Structured test case definition for chatbot evaluation."""
    id: str
    category: str
    question: str
    expected_language: str
    expected_intent: Optional[str] = None
    expected_entities: Dict[str, Any] = Field(default_factory=dict)
    expected_filters: Dict[str, Any] = Field(default_factory=dict)
    expected_tool: Optional[str] = None
    expected_tool_args: Optional[Dict[str, Any]] = None
    expected_clarification: bool = False
    expected_db_scope: Optional[Dict[str, Any]] = None
    validation_rules: Dict[str, Any] = Field(default_factory=dict)
    seed_turn: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None


BENCHMARK_DATASET: List[EvaluationTestCase] = [
    # ── Category 1: English Questions ──────────────────────────────────────────
    EvaluationTestCase(
        id="ENG-001",
        category="1_English",
        question="Show my pending applications",
        expected_language="en",
        expected_intent="all_service_code_applications",
        expected_filters={"status": "pending"},
        validation_rules={"must_contain": ["application", "pending"], "must_not_contain": ["Error"]}
    ),
    EvaluationTestCase(
        id="ENG-002",
        category="1_English",
        question="What is my current workload summary?",
        expected_language="en",
        expected_intent="officer_workload",
        validation_rules={"must_contain": ["workload", "ISD", "NISD"]}
    ),
    EvaluationTestCase(
        id="ENG-003",
        category="1_English",
        question="List all applications submitted through CSC channel",
        expected_language="en",
        expected_intent="all_service_code_applications",
        expected_filters={"channel": "CSC"},
        validation_rules={"must_contain": ["CSC"]}
    ),

    # ── Category 2: Tamil Questions ────────────────────────────────────────────
    EvaluationTestCase(
        id="TAM-001",
        category="2_Tamil",
        question="எனது அதிகார வரம்பில் எத்தனை விண்ணப்பங்கள் நிலுவையில் உள்ளன?",
        expected_language="ta",
        expected_intent="pending_applications",
        expected_filters={"status": "pending", "count_only": True},
        validation_rules={"must_contain": ["நிலுவை", "விண்ணப்ப"]}
    ),
    EvaluationTestCase(
        id="TAM-002",
        category="2_Tamil",
        question="தாமதமான விண்ணப்பங்களின் பட்டியலைக் காட்டு",
        expected_language="ta",
        expected_intent="overdue_applications",
        validation_rules={"must_contain": ["விண்ணப்ப"]}
    ),
    EvaluationTestCase(
        id="TAM-003",
        category="2_Tamil",
        question="விண்ணப்ப எண் 2026/0154/02/013710 இன் நிலை என்ன?",
        expected_language="ta",
        expected_intent="application_status",
        expected_entities={"application_number": "2026/0154/02/013710"},
        validation_rules={"must_contain": ["2026/0154/02/013710"]}
    ),

    # ── Category 3: Tanglish Questions ─────────────────────────────────────────
    EvaluationTestCase(
        id="TNG-001",
        category="3_Tanglish",
        question="pending list kaattu",
        expected_language="tanglish",
        expected_intent="pending_applications",
        expected_filters={"status": "pending"},
        validation_rules={"must_contain": ["விண்ணப்ப"]}
    ),
    EvaluationTestCase(
        id="TNG-002",
        category="3_Tanglish",
        question="2026/0153/28/000002 status enna?",
        expected_language="tanglish",
        expected_intent="application_status",
        expected_entities={"application_number": "2026/0153/28/000002"},
        validation_rules={"must_contain": ["2026/0153/28/000002"]}
    ),
    EvaluationTestCase(
        id="TNG-003",
        category="3_Tanglish",
        question="overdue applications evlo irukku",
        expected_language="tanglish",
        expected_intent="overdue_applications",
        validation_rules={"must_contain": ["விண்ணப்ப"]}
    ),

    # ── Category 4: Paraphrases of Identical Questions ─────────────────────────
    EvaluationTestCase(
        id="PAR-001a",
        category="4_Paraphrases",
        question="Show overdue applications",
        expected_language="en",
        expected_intent="overdue_applications",
        validation_rules={"must_contain": ["overdue"]}
    ),
    EvaluationTestCase(
        id="PAR-001b",
        category="4_Paraphrases",
        question="List all applications that are delayed past SLA",
        expected_language="en",
        expected_intent="overdue_applications",
        validation_rules={"must_contain": ["overdue"]}
    ),
    EvaluationTestCase(
        id="PAR-001c",
        category="4_Paraphrases",
        question="Which files missed their deadline?",
        expected_language="en",
        expected_intent="overdue_applications",
        validation_rules={"must_contain": ["overdue"]}
    ),

    # ── Category 5: Typos and Spelling Variations ──────────────────────────────
    EvaluationTestCase(
        id="TYP-001",
        category="5_Typos",
        question="show pendng aplications",
        expected_language="en",
        expected_intent="all_service_code_applications",
        validation_rules={"must_contain": ["application"]}
    ),
    EvaluationTestCase(
        id="TYP-002",
        category="5_Typos",
        question="overdu apllicatons in jaunary",
        expected_language="en",
        expected_intent="overdue_applications",
        expected_filters={"submission_month": 1},
        validation_rules={"must_contain": ["overdue"]}
    ),
    EvaluationTestCase(
        id="TYP-003",
        category="5_Typos",
        question="what is the servise code 0153",
        expected_language="en",
        expected_intent="service_code_lookup",
        expected_entities={"service_code": "0153"},
        validation_rules={"must_contain": ["0153", "Not Involving"]}
    ),

    # ── Category 6: Application Status Questions ───────────────────────────────
    EvaluationTestCase(
        id="APP-001",
        category="6_ApplicationStatus",
        question="Status of 2026/0154/02/013710",
        expected_language="en",
        expected_intent="application_status",
        expected_entities={"application_number": "2026/0154/02/013710"},
        expected_tool="get_application_details",
        validation_rules={"must_contain": ["2026/0154/02/013710"]}
    ),
    EvaluationTestCase(
        id="APP-002",
        category="6_ApplicationStatus",
        question="Has application 2026/0154/02/013710 been approved?",
        expected_language="en",
        expected_intent="application_status",
        expected_entities={"application_number": "2026/0154/02/013710"},
        validation_rules={"must_contain": ["2026/0154/02/013710"]}
    ),

    # ── Category 7: Survey and Ownership Questions ─────────────────────────────
    EvaluationTestCase(
        id="SRV-001",
        category="7_SurveyOwnership",
        question="Who is the registered owner of survey number 1355?",
        expected_language="en",
        expected_intent="survey_owners",
        expected_entities={"survey_number": "1355"},
        expected_tool="get_survey_details",
        validation_rules={"must_contain": ["1355"]}
    ),
    EvaluationTestCase(
        id="SRV-002",
        category="7_SurveyOwnership",
        question="What is the patta number and area of survey 1355/1B12?",
        expected_language="en",
        expected_intent="survey_detail",
        expected_entities={"survey_number": "1355/1B12"},
        expected_tool="get_survey_details",
        validation_rules={"must_contain": ["1355/1B12"]}
    ),
    EvaluationTestCase(
        id="SRV-003",
        category="7_SurveyOwnership",
        question="Is survey 1355 locked by an active mutation application?",
        expected_language="en",
        expected_intent="can_apply_check",
        expected_entities={"survey_number": "1355"},
        expected_tool="check_survey_application_lock",
        validation_rules={"must_contain": ["1355"]}
    ),

    # ── Category 8: Field Visit Questions ──────────────────────────────────────
    EvaluationTestCase(
        id="FV-001",
        category="8_FieldVisits",
        question="Show my field visits schedule",
        expected_language="en",
        expected_intent="field_visits",
        expected_tool="get_field_visits",
        validation_rules={"must_contain": ["visit"]}
    ),
    EvaluationTestCase(
        id="FV-002",
        category="8_FieldVisits",
        question="Which application should I inspect next?",
        expected_language="en",
        expected_intent="fv_visit_plan",
        expected_tool="get_visit_plan",
        validation_rules={"must_contain": ["visit", "plan"]}
    ),
    EvaluationTestCase(
        id="FV-003",
        category="8_FieldVisits",
        question="Are any field visits overdue?",
        expected_language="en",
        expected_intent="fv_overdue_inspections",
        validation_rules={"must_contain": ["visit"]}
    ),

    # ── Category 9: Multi-Part Questions ───────────────────────────────────────
    EvaluationTestCase(
        id="MUL-001",
        category="9_MultiPart",
        question="How many pending ISD applications do I have and how many are overdue?",
        expected_language="en",
        expected_intent="officer_workload",
        validation_rules={"must_contain": ["ISD", "overdue"]}
    ),
    EvaluationTestCase(
        id="MUL-002",
        category="9_MultiPart",
        question="Give me the status and field inspection date for 2026/0154/02/013710",
        expected_language="en",
        expected_intent="application_status",
        expected_entities={"application_number": "2026/0154/02/013710"},
        validation_rules={"must_contain": ["2026/0154/02/013710"]}
    ),

    # ── Category 10: Conversational Follow-ups ─────────────────────────────────
    EvaluationTestCase(
        id="FOL-001",
        category="10_Followups",
        question="who is the applicant?",
        expected_language="en",
        expected_intent="application_status",
        seed_turn={
            "user": "Status of 2026/0154/02/013710",
            "assistant": "Application 2026/0154/02/013710 is approved.",
            "context": {
                "entity": "application",
                "application_numbers": ["2026/0154/02/013710"],
                "survey_numbers": ["54"],
                "filters": {"status": "approved"},
                "version": 1
            }
        },
        expected_entities={"application_number": "2026/0154/02/013710"},
        validation_rules={"must_contain": ["applicant"]}
    ),
    EvaluationTestCase(
        id="FOL-002",
        category="10_Followups",
        question="details of the first one",
        expected_language="en",
        expected_intent="application_status",
        seed_turn={
            "user": "Show approved applications",
            "assistant": "Found 2 applications: 1) 2026/0154/02/013710 2) 2026/0154/02/012834",
            "context": {
                "entity": "application_list",
                "application_numbers": ["2026/0154/02/013710", "2026/0154/02/012834"],
                "survey_numbers": [],
                "filters": {"status": "approved"},
                "version": 1
            }
        },
        expected_entities={"application_number": "2026/0154/02/013710"},
        validation_rules={"must_contain": ["2026/0154/02/013710"]}
    ),

    # ── Category 11: Ambiguous Questions ───────────────────────────────────────
    EvaluationTestCase(
        id="AMB-001",
        category="11_Ambiguous",
        question="when was it approved?",
        expected_language="en",
        expected_clarification=True,
        seed_turn={
            "user": "Show approved applications",
            "assistant": "Found 3 approved applications.",
            "context": {
                "entity": "application_list",
                "application_numbers": ["2026/0154/28/000001", "2026/0153/28/000002", "2026/0154/28/000003"],
                "survey_numbers": [],
                "filters": {"status": "approved"},
                "version": 1
            }
        },
        validation_rules={"must_contain": ["Which", "application"]}
    ),

    # ── Category 12: Out-of-Jurisdiction Questions ─────────────────────────────
    EvaluationTestCase(
        id="JUR-001",
        category="12_OutOfJurisdiction",
        question="Show applications in Ward 999",
        expected_language="en",
        expected_db_scope={"ward_number": "999"},
        validation_rules={"must_contain": ["outside", "jurisdiction"]}
    ),
    EvaluationTestCase(
        id="JUR-002",
        category="12_OutOfJurisdiction",
        question="Details of application 2026/0154/99/999999",
        expected_language="en",
        expected_entities={"application_number": "2026/0154/99/999999"},
        validation_rules={"must_not_contain": ["applicant_name", "approval_date"]}
    ),

    # ── Category 13: Questions Outside Available Knowledge Base ────────────────
    EvaluationTestCase(
        id="OOK-001",
        category="13_OutOfKnowledge",
        question="What is the recipe for chocolate chip cookies?",
        expected_language="en",
        validation_rules={"must_contain": ["only answer", "survey", "TAMILNILAM"]}
    ),
    EvaluationTestCase(
        id="OOK-002",
        category="13_OutOfKnowledge",
        question="What is the weather in Madurai today?",
        expected_language="en",
        validation_rules={"must_contain": ["only answer", "survey"]}
    ),

    # ── Category 14: Procedural and Service-Code Questions ─────────────────────
    EvaluationTestCase(
        id="PRO-001",
        category="14_ProceduralServiceCodes",
        question="What is the difference between ISD and NISD?",
        expected_language="en",
        expected_intent="service_code_guide",
        validation_rules={"must_contain": ["0154", "0153", "subdivision", "field"]}
    ),
    EvaluationTestCase(
        id="PRO-002",
        category="14_ProceduralServiceCodes",
        question="What documents are required for patta transfer?",
        expected_language="en",
        expected_intent="check_documents",
        validation_rules={"must_contain": ["sale deed", "patta", "documents"]}
    ),
    EvaluationTestCase(
        id="PRO-003",
        category="14_ProceduralServiceCodes",
        question="What is the government fee for service code 0154?",
        expected_language="en",
        expected_intent="fee_lookup",
        expected_entities={"service_code": "0154"},
        validation_rules={"must_contain": ["0154"]}
    ),
]

