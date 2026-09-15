import os
import sys
import csv

# Ensure we use the virtual env paths if needed, though running it via the venv python is safer.
from fpdf import FPDF
import docx

def create_directory(path):
    if not os.path.exists(path):
        os.makedirs(path)
        print(f"Created directory: {path}")

def generate_domain_pdf(output_path):
    pdf = FPDF()
    pdf.set_auto_page_break(False)
    
    # Page 1: Introduction to ISD
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Tamil Nadu Survey Department - ISD Workflow", ln=True, align="C")
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 7, 
        "Service Code: 0154 (Involving Sub-Division)\n\n"
        "An ISD application is triggered when a land parcel is split into multiple sub-divisions. "
        "Unlike NISD applications, ISD applications require a mandatory field inspection and the preparation "
        "of a sub-division sketch by the Senior Draughtsman (SD).\n\n"
        "Key Characteristics:\n"
        "- The tracking format is ISD/DISTRICT_CODE/YEAR/SEQUENCE.\n"
        "- Sum of all proposed sub-divisions must equal the original survey area.\n"
        "- All joint owners must be present and sign the field inspection report."
    )
    
    # Page 2: Field Visit Rules
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "SIS Field Visit Guidelines", ln=True)
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 7,
        "Scheduling and Protocol:\n"
        "- The SIS officer must schedule the field visit within 15 working days of application submission.\n"
        "- Rescheduling Rule: If an SIS officer needs to reschedule or change the date of a field visit, "
        "they MUST ask the Tahsildar for approval. Only the Tahsildar has the authority to approve field visit date changes.\n"
        "- Boundary Verification: The officer must verify physical boundaries, check for encroachments, "
        "and take GPS coordinates along with photographs.\n"
        "- Encroachments: If an encroachment is detected, the SIS officer must log a detailed remark "
        "and refer the matter to the Deputy Inspector Surveyor (DIS)."
    )

    # Page 3: Subdivision & Temporary Numbers
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Subdivision Sketch & Temporary Numbers", ln=True)
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 7,
        "Draughtsman and DIS Roles:\n"
        "- The Senior Draughtsman (SD) prepares the detailed survey sketch containing the proposed sub-divisions.\n"
        "- Area Tolerance: The sum of all sub-division extents must equal the parent survey extent within a tolerance of +/- 0.5%.\n"
        "- Temporary Subdivision Format:\n"
        "  DIS assigns temporary subdivision numbers to proposed parcels using the format:\n"
        "  {existing_subdiv}/T{sequence}, e.g., 3/T1, 3/T2, 0/T1.\n"
        "  - The number before '/T' is the parent subdivision being split (0 if the parent has no prior subdivision).\n"
        "  - The number after 'T' is a sequence counter.\n"
        "- Once approved by the Tahsildar, these temporary numbers are converted to final subdivision numbers (e.g., 3/T1 -> 3, 3/T2 -> 4)."
    )
    
    pdf.output(output_path)
    print(f"Generated PDF: {output_path}")

def generate_domain_docx(output_path):
    doc = docx.Document()
    doc.add_heading("Urban Land Classification & Rules", 0)
    
    doc.add_heading("Land Type Classification Codes", 1)
    p = doc.add_paragraph(
        "In the TAMILNILAM urban workflow, the land type classification code determines "
        "the nature of verification required by the SIS officer. For example, transferring "
        "private land is straightforward, whereas government lands require strict adherence to "
        "government orders."
    )
    
    # Adding Land Type table
    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Shading Accent 1"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "Code"
    hdr_cells[1].text = "Description"
    hdr_cells[2].text = "Tamil Translation"
    
    data = [
        ("1", "Residential", "நடைமுறை குடியிருப்பு"),
        ("2", "Commercial", "வணிகப் பயன்பாடு"),
        ("4", "Agricultural", "விவசாய நிலம்"),
        ("5", "Government Poramboke", "அரசு புறம்போக்கு"),
        ("6", "Institutional", "நிறுவன நிலம்")
    ]
    
    for code, desc, ta_desc in data:
        row_cells = table.add_row().cells
        row_cells[0].text = code
        row_cells[1].text = desc
        row_cells[2].text = ta_desc
        
    doc.add_heading("Poramboke and Institutional Rules", 1)
    doc.add_paragraph(
        "Government Poramboke (Type 5) land cannot be sub-divided or transferred to private owners "
        "except under specific settlement service codes (e.g., 0179 or 0183) where a valid Government Order (GO) "
        "is explicitly cited and verified.\n\n"
        "Institutional Land (Type 6) transfers (such as temples, churches, mosques, or educational trusts) "
        "require a formal No Objection Certificate (NOC) or endorsement from the controlling authority "
        "(such as HR&CE or the Wakf Board) on the record before any mutation can be approved."
    )
    
    doc.save(output_path)
    print(f"Generated DOCX: {output_path}")

def generate_domain_txt(output_path):
    content = """SIS CHATBOT TESTING FAQ - REFERENCE FOR UPLOADS

=== CITIZEN IDENTIFIERS: CAN AND IGRS ===

Q: What is a CAN number, and what does its length indicate?
A: CAN stands for Citizen Access Number. Every application carries one. The length of the CAN number indicates the counter that ISSUED the number, NOT the submission channel of the application:
- 15 digits (starting with 133, e.g., 133280122203291) indicates it was issued by a CSC / e-Sevai counter.
- 12 digits (e.g., 202329380999) indicates it was issued by the TN citizen portal.
A Sub-Registrar referral carries a 12-digit portal-issued CAN because it is generated automatically, so a 12-digit CAN does not automatically mean a citizen submission.

Q: This application has no IGRS Form 6 number. What does that mean?
A: An application without an IGRS Form 6 number indicates it was filed via a CSC/e-Sevai counter or directly by a citizen at a revenue camp. Only Sub-Registrar (SRO) referrals carry an IGRS Form 6 number, which is raised automatically off the registered deed. For CSC and citizen channels, an empty IGRS field is the standard rule, not a missing record or data gap.

=== SUBMISSION CHANNELS ===

Q: How is the submission channel decided?
A: The submission channel is derived strictly from two columns in the application log: 'source_name' and 'camp_flag':
1. Sub-Registrar: source_name is "-" (indicating no operator touched the file). This means IGRS automatically raised the mutation off the registered deed.
2. Citizen: source_name holds a bare mobile number (10 digits) AND camp_flag is "P" (special revenue camp).
3. CSC (Common Service Centre): source_name holds an operator or VLE code (e.g., tut_tct_t131_02) and camp_flag is anything else.

=== SURVEY NUMBER LOCKS ===

Q: Can multiple applications be active on the same survey number?
A: No. Mutation on a survey number is a synchronous process. While one application on a survey number is active (status is pending, in_progress, or escalated), no other application may be filed on it. This rule applies across all sub-divisions of that survey number because the entire parcel record is locked.
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generated TXT: {output_path}")

def generate_domain_csv(output_path):
    headers = ["App No", "Type", "Ward", "Status", "Fee", "Applicant", "CAN Number", "IGRS Form 6"]
    rows = [
        ["2026/0154/28/0001", "ISD", "102", "pending", "2500", "Karthik Rajan", "133280122203291", "N/A"],
        ["2026/0153/28/0002", "NISD", "103", "approved", "1200", "Priyanka Sen", "202329380999", "202329380999"],
        ["2026/0154/28/0003", "ISD", "102", "approved", "2500", "Anand Kumar", "133280122203295", "N/A"],
        ["2026/0155/28/0004", "MERGE", "103", "rejected", "900", "Suresh Pillai", "202329380991", "N/A"],
        ["2026/0153/28/0005", "NISD", "102", "pending", "1200", "Meena Selvam", "202329380992", "202329380992"]
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    print(f"Generated CSV: {output_path}")

def generate_random_pdf(output_path):
    pdf = FPDF()
    pdf.set_auto_page_break(False)
    
    # Page 1: Mars
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Wonders of the Solar System - Planet Mars", ln=True, align="C")
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 7, 
        "Mars is the fourth planet from the Sun and the second-smallest planet in the Solar System. "
        "Often referred to as the 'Red Planet' due to the iron oxide prevalent on its surface, which gives "
        "it a reddish appearance.\n\n"
        "Key Feature: Olympus Mons\n"
        "Olympus Mons is a colossal shield volcano on Mars. It is the largest volcano in the Solar System, "
        "standing at an astounding height of 21.9 km (about 2.5 times the height of Mount Everest above sea level)."
    )
    
    # Page 2: Jupiter
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "The Great Gas Giant - Jupiter", ln=True, align="C")
    pdf.ln(5)
    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 7,
        "Jupiter is the fifth planet from the Sun and the largest in the Solar System. It is a gas giant with "
        "a mass more than two and a half times that of all the other planets in the Solar System combined.\n\n"
        "Key Feature: The Great Red Spot\n"
        "The Great Red Spot is a persistent high-pressure storm in Jupiter's atmosphere, producing winds up to "
        "432 km/h. It is larger than Earth and has been observed continuously for at least 300 years, "
        "dating back to the 17th century."
    )
    
    pdf.output(output_path)
    print(f"Generated PDF: {output_path}")

def generate_random_txt(output_path):
    content = """TRADITIONAL TAMIL NADU RECIPES

=== RECIPE 1: TRADITIONAL SAMBAR ===
Sambar is a lentil-based vegetable stew, cooked with pigeon peas (toor dal) and tamarind broth. It is popular in South Indian cuisines.

Ingredients:
- Toor Dal: 1 cup
- Tamarind paste: 1 tablespoon
- Sambar Powder: 2 tablespoons
- Vegetables: Drumstick, Shallots (Sambar onions), Tomato, Carrot
- Mustard Seeds, Curry Leaves, and Asafoetida (for tempering)

Instructions:
1. Pressure cook the toor dal until soft and mash it.
2. Boil the vegetables in tamarind water along with turmeric and sambar powder.
3. Once the vegetables are tender, add the mashed dal and simmer for 5 minutes.
4. Prepare the tempering by heating oil, adding mustard seeds, curry leaves, and asafoetida, then pour it over the sambar.

=== RECIPE 2: SOUTH INDIAN FILTER COFFEE ===
South Indian filter coffee is a sweet milky coffee made from dark roasted coffee beans and chicory.

Ingredients:
- Fresh Coffee Powder (roasted chicory blend): 3 tablespoons
- Boiling water: 1 cup
- Whole milk: 1 cup
- Sugar: to taste

Instructions:
1. Place the coffee powder in the upper compartment of the brass filter.
2. Press down gently and pour boiling water over it. Let the decoction drip for 15 minutes.
3. Heat milk to a boil.
4. Mix 1-2 tablespoons of coffee decoction with hot milk and sugar, frothing it using a dabarah and tumbler.
"""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generated TXT: {output_path}")

def generate_random_csv(output_path):
    headers = ["Movie Title", "Year", "Director", "Rating", "Box Office (Millions USD)"]
    rows = [
        ["Inception", "2010", "Christopher Nolan", "8.8", "836"],
        ["The Dark Knight", "2008", "Christopher Nolan", "9.0", "1006"],
        ["Interstellar", "2014", "Christopher Nolan", "8.6", "701"],
        ["The Matrix", "1999", "Lana Wachowski", "8.7", "467"],
        ["Avatar", "2009", "James Cameron", "7.8", "2923"]
    ]
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)
    print(f"Generated CSV: {output_path}")

def main():
    target_dir = os.path.join("backend", "documents", "test_fixtures", "user_tests")
    create_directory(target_dir)
    
    # Generate domain-specific test files
    generate_domain_pdf(os.path.join(target_dir, "sample_workflow_isd.pdf"))
    generate_domain_docx(os.path.join(target_dir, "sample_land_rules.docx"))
    generate_domain_txt(os.path.join(target_dir, "sample_faq.txt"))
    generate_domain_csv(os.path.join(target_dir, "sample_applications.csv"))
    
    # Generate random test files
    generate_random_pdf(os.path.join(target_dir, "random_topic_astronomy.pdf"))
    generate_random_txt(os.path.join(target_dir, "random_topic_cooking.txt"))
    generate_random_csv(os.path.join(target_dir, "random_topic_movies.csv"))
    
    print("\nSuccessfully generated all test files inside: " + target_dir)

if __name__ == "__main__":
    main()
