"""Builds the synthetic SIS_Upload_QA_Fixtures/ test files. Run once:
    python SIS_Upload_QA_Fixtures/_build_fixtures.py
Every value in every fixture is fictional test data, clearly marked as such,
and invented for this QA pass -- none of it is copied from a real officer
record, a real application, or any production database row.
"""
import os
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))


def p(name):
    return os.path.join(HERE, name)


# ---------------------------------------------------------------------
# 1. sis_extraction_test_en.txt -- exact facts for extraction-accuracy checks
# ---------------------------------------------------------------------
TXT_FACTS = """SIS UPLOAD QA FIXTURE -- SYNTHETIC TEST DATA, NOT AN OFFICIAL RECORD

Test Record ID: QA-TXT-001
Synthetic Survey Number: 777
Synthetic Sub-division Number: 777/9
Synthetic Application Reference: TEST/0154/99/000777
Synthetic Ward: 999
Synthetic Block: 0099
Synthetic Status: Pending (fictional)
Synthetic Area: 450.75 sq.m
Synthetic Submission Date: 2026-01-15

This paragraph exists only to verify that the chatbot can answer a plain
"summarize this document" question about a short text file. The document
has exactly one record above and no table. The applicant name on this
fictional record is "Test Applicant Alpha" and the fictional officer note
reads: "No field visit required for this synthetic record."
"""

# ---------------------------------------------------------------------
# 2. sis_survey_records_en.csv -- 12 synthetic rows, consistent fictional schema
# ---------------------------------------------------------------------
CSV_ROWS = [
    ("test_record_id", "synthetic_survey_no", "synthetic_subdivision_no",
     "synthetic_ward", "synthetic_block", "synthetic_application_ref",
     "synthetic_status", "synthetic_area_sqm", "synthetic_date", "notes"),
    ("QA-CSV-001", "801", "801/1", "999", "0099", "TEST/0154/99/000801", "Pending", "320.00", "2026-02-01", "Synthetic test row"),
    ("QA-CSV-002", "802", "802/1", "999", "0099", "TEST/0154/99/000802", "Approved", "410.50", "2026-02-02", "Synthetic test row"),
    ("QA-CSV-003", "803", "0", "999", "0099", "TEST/0153/99/000803", "Rejected", "275.25", "2026-02-03", "Synthetic test row; NISD, no sub-division"),
    ("QA-CSV-004", "804", "804/1", "999", "0100", "TEST/0154/99/000804", "Pending", "505.00", "2026-02-04", "Synthetic test row"),
    ("QA-CSV-005", "805", "805/1", "999", "0100", "TEST/0154/99/000805", "Pending", "198.40", "2026-02-05", "Synthetic test row; conflicts with QA-CSV-011 (see fixture 2b)"),
    ("QA-CSV-006", "806", "0", "999", "0100", "TEST/0153/99/000806", "Approved", "612.00", "2026-02-06", "Synthetic test row"),
    ("QA-CSV-007", "807", "807/1", "999", "0101", "TEST/0154/99/000807", "Rejected", "330.75", "2026-02-07", "Synthetic test row"),
    ("QA-CSV-008", "808", "808/1", "999", "0101", "TEST/0154/99/000808", "Pending", "275.00", "2026-02-08", "Synthetic test row"),
    ("QA-CSV-009", "809", "0", "999", "0101", "TEST/0153/99/000809", "Approved", "440.90", "2026-02-09", "Synthetic test row"),
    ("QA-CSV-010", "810", "810/1", "999", "0102", "TEST/0154/99/000810", "Pending", "365.10", "2026-02-10", "Synthetic test row"),
    ("QA-CSV-011", "805", "805/2", "999", "0100", "TEST/0154/99/000805", "Approved", "201.00", "2026-02-11", "Synthetic test row; DELIBERATE CONFLICT with QA-CSV-005 -- same survey+app ref, different status/area"),
    ("QA-CSV-012", "812", "812/1", "999", "0102", "TEST/0154/99/000812", "Pending", "290.60", "2026-02-12", "Synthetic test row"),
]

# ---------------------------------------------------------------------
# 2b. sis_survey_records_en_v2.csv -- a SECOND document sharing survey 805
#     with a conflicting status/area, for the multi-document conflict tests.
# ---------------------------------------------------------------------
CSV_ROWS_V2 = [
    ("test_record_id", "synthetic_survey_no", "synthetic_subdivision_no",
     "synthetic_ward", "synthetic_block", "synthetic_application_ref",
     "synthetic_status", "synthetic_area_sqm", "synthetic_date", "notes"),
    ("QA-CSV2-001", "805", "805/1", "999", "0100", "TEST/0154/99/000805", "Rejected", "198.40", "2026-03-01", "Synthetic test row; second document, conflicts with QA-CSV-005 on STATUS (Pending vs Rejected)"),
    ("QA-CSV2-002", "901", "901/1", "999", "0200", "TEST/0154/99/000901", "Pending", "150.00", "2026-03-02", "Synthetic test row; unique to this document"),
]

# ---------------------------------------------------------------------
# 3. sis_sample_workflow_en.pdf -- multi-page native-text PDF
# ---------------------------------------------------------------------
WORKFLOW_PAGES = [
    (
        "SIS Fictional Workflow Guide -- QA Test Fixture",
        """SOURCE: SIS_Upload_QA_Fixtures (synthetic test document, not an official SIS publication)

This is a fictional, synthetic procedural document created only to test the
SIS chatbot's document-upload and question-answering pipeline. Nothing in
this document should be treated as a real department rule.

PREREQUISITES (fictional, for testing only):
1. A completed test application form (Form QA-1, fictional).
2. A synthetic survey sketch reference number.
3. Confirmation that the fictional applicant has no other open test file on
   the same synthetic survey number.
"""
    ),
    (
        "Fictional Validation Steps",
        """STEP 1 (fictional): The test officer checks the synthetic application
reference against the fictional register.

STEP 2 (fictional): The test officer verifies the synthetic sub-division
sketch was attached. If it is missing, the fictional file is marked
'awaiting sketch' rather than rejected outright.

STEP 3 (fictional): The test officer forwards the fictional file to the
Senior Test Draughtsman for synthetic review.

EXCEPTIONS (fictional): A fictional file flagged 'urgent-test' skips Step 2
and goes directly to Step 3.
"""
    ),
    (
        "Fictional Terminology",
        """QA-WF = fictional workflow code used only in this test document.
This document deliberately does NOT define or mention OCR anywhere, so a
later question asking the chatbot to "explain OCR" must not be answered as
if OCR were part of this document's content.
"""
    ),
]


def build_workflow_pdf():
    pdf = FPDF()
    for title, body in WORKFLOW_PAGES:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.multi_cell(0, 10, title)
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 7, body)
    pdf.output(p("sis_sample_workflow_en.pdf"))


# ---------------------------------------------------------------------
# 4. sis_scanned_records_en.pdf -- IMAGE-ONLY PDF (no text layer), for OCR
# ---------------------------------------------------------------------
SCAN_LINES = [
    "SIS SCANNED TEST RECORD -- SYNTHETIC, NOT OFFICIAL",
    "Test Record ID: QA-OCR-001",
    "Synthetic Survey Number: 950",
    "Synthetic Sub-division Number: 950/2",
    "Synthetic Application Reference: TEST/0154/99/000950",
    "Synthetic Status: Pending",
    "Synthetic Area: 388.20 sq.m",
    "Synthetic Submission Date: 2026-04-01",
]


def build_scanned_pdf():
    img = Image.new("RGB", (1240, 1754), "white")  # ~A4 at 150dpi
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 34)
    except Exception:
        font = ImageFont.load_default()
    y = 80
    for line in SCAN_LINES:
        draw.text((80, y), line, fill="black", font=font)
        y += 60
    img.save(p("sis_scanned_records_en.pdf"), "PDF", resolution=150.0)


# ---------------------------------------------------------------------
# 5. sis_terms_ta.txt -- Tamil terminology, clearly labelled synthetic
# ---------------------------------------------------------------------
TA_TERMS = """SIS QA TEST FIXTURE -- \u0b9a\u0bc6\u0bb5\u0bcd\u0bb5\u0bbf\u0baf\u0bb2\u0bcd \u0b9a\u0bca\u0bb1\u0bcd\u0b95\u0bb3\u0bcd (\u0b95\u0bb1\u0bcd\u0baa\u0bbf\u0ba9 \u0b9a\u0bca\u0bb1\u0bcd\u0b95\u0bb3\u0bcd, \u0b85\u0bb2\u0bcd\u0bb2 \u0b85\u0bb0\u0b9a\u0bc1 \u0bb5\u0bbf\u0bb3\u0b95\u0bcd\u0b95\u0bae\u0bcd)

\u0b87\u0ba8\u0bcd\u0ba4 \u0b95\u0bc1\u0bb1\u0bbf\u0baa\u0bcd\u0baa\u0bc1 \u0b85\u0bb3\u0bb5\u0bc1\u0b95\u0bb3\u0bcd \u0b95\u0bb1\u0bcd\u0baa\u0bbf\u0ba9\u0bb5\u0bc8, \u0bb5\u0bbf\u0b9a\u0bc1\u0bb5\u0bbe\u0b9a\u0ba9 \u0b9a\u0bb0\u0bbf\u0baa\u0bbe\u0bb0\u0bcd\u0baa\u0bcd\u0baa\u0bbf\u0bb1\u0bcd\u0b95\u0bbe\u0ba9\u0bb5\u0bc8.

\u0b9a\u0bca\u0bb1\u0bcd 1: \u0b9a\u0bbe\u0bb0\u0bcd\u0bb5\u0bc7 \u0b8e\u0ba3\u0bcd (Test Survey Number) = 950
\u0b9a\u0bca\u0bb1\u0bcd 2: \u0bc1\u0b9f\u0bcd\u0baa\u0bbf\u0bb0\u0bbf\u0bb5\u0bc1 \u0b8e\u0ba3\u0bcd (Test Sub-division) = 950/2
\u0b9a\u0bca\u0bb1\u0bcd 3: \u0ba8\u0bbf\u0bb2\u0bc8 (Status) = Pending (\u0b95\u0bb1\u0bcd\u0baa\u0bbf\u0ba9\u0bc1)
\u0b9a\u0bca\u0bb1\u0bcd 4: \u0baa\u0bb0\u0baa\u0bcd\u0baa\u0bb3\u0bb5\u0bc1 (Area) = 388.20 \u0b9a\u0b9a 2
"""

# ---------------------------------------------------------------------
# 6. sis_oversized_test.txt -- over the 5MB UPLOAD_MAX_FILE_BYTES limit
# ---------------------------------------------------------------------
def build_oversized():
    line = ("SYNTHETIC OVERSIZE TEST LINE -- this file exists only to exceed "
            "the configured UPLOAD_MAX_FILE_BYTES limit of 5 MiB. " * 4 + "\n")
    target = 5 * 1024 * 1024 + 200_000  # ~5.19 MiB, comfortably over 5 MiB
    with open(p("sis_oversized_test.txt"), "w", encoding="utf-8") as f:
        written = 0
        while written < target:
            f.write(line)
            written += len(line)


# ---------------------------------------------------------------------
# 7. sis_near_limit_test.txt -- just under the 5MB limit
# ---------------------------------------------------------------------
def build_near_limit():
    line = "SYNTHETIC NEAR-LIMIT TEST LINE. " * 4 + "\n"
    target = 5 * 1024 * 1024 - 50_000  # comfortably under 5 MiB
    with open(p("sis_near_limit_test.txt"), "w", encoding="utf-8") as f:
        written = 0
        while written < target:
            f.write(line)
            written += len(line)


# ---------------------------------------------------------------------
# 8. sis_corrupt.pdf -- .pdf extension, not real PDF bytes (signature check)
# ---------------------------------------------------------------------
def build_corrupt_pdf():
    with open(p("sis_corrupt.pdf"), "wb") as f:
        f.write(b"This is plain text, not a PDF, saved with a .pdf extension "
                 b"on purpose to test signature validation.\n" * 10)


# ---------------------------------------------------------------------
# 9. sis_empty.txt -- zero-byte file
# ---------------------------------------------------------------------
def build_empty():
    open(p("sis_empty.txt"), "wb").close()


def write_text(name, text):
    with open(p(name), "w", encoding="utf-8") as f:
        f.write(text)


def write_csv(name, rows):
    import csv
    with open(p(name), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerows(rows)


if __name__ == "__main__":
    write_text("sis_extraction_test_en.txt", TXT_FACTS)
    write_csv("sis_survey_records_en.csv", CSV_ROWS)
    write_csv("sis_survey_records_en_v2.csv", CSV_ROWS_V2)
    build_workflow_pdf()
    build_scanned_pdf()
    write_text("sis_terms_ta.txt", TA_TERMS)
    build_oversized()
    build_near_limit()
    build_corrupt_pdf()
    build_empty()
    print("Fixtures built in", HERE)
    for fn in sorted(os.listdir(HERE)):
        if fn.startswith("_"):
            continue
        fp = p(fn)
        print(f"  {fn:40s} {os.path.getsize(fp):>10,} bytes")
