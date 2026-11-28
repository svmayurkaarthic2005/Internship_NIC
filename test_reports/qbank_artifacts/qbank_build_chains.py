# -*- coding: utf-8 -*-
import json, os

CHAINS = {
"Chain1_045470": [
    "I am reviewing application 2026/0153/02/045470. The register identifies it as a transfer relating to survey number 110, subdivision 2 and old patta 75100. I want to understand why this application ended the way it did rather than just seeing the current status. Please trace it from submission through the SIS desk to the final desk, include the dates of each transition, the recommendations and the remarks, and explain the actual reason recorded for its rejection. Also distinguish the application status from the transaction remarks.",
    "Who handled it immediately before it reached the final desk, and on what date?",
    "What exactly was missing? Was the problem with the original ownership document, a linked document or something else?",
    "Can you compare the final remark in the transfer table with the final remark in the workflow history? Are they describing the same problem?",
    "What was the registration document number, where was it registered and on what date? Don't confuse that with the application submission date."
],
"Chain4_fieldvisit_contradiction": [
    "Consider 2026/0153/02/045470. This is an NISD application, which normally follows a workflow without a mandatory field inspection. However, I can see a field_visit_dt value in one workflow entry. Can you inspect the raw workflow evidence and the service type separately, and explain whether the stored date actually establishes a required field visit?",
    "Which workflow transition contains that date?",
    "Does a populated date column automatically mean that the service requires an inspection?",
    "Explain the difference between a field visit being required by the service design and a date merely being present in a raw record."
],
"Chain5_012547": [
    "Please investigate application 2026/0154/02/012547. It relates to survey number 7 and existing patta 8232. I want a complete picture of the subdivision process: first explain the original ownership records, then identify the temporary subdivision recorded for this application, and finally show which proposed owners are associated with that subdivision. Also check the application status, rejection remarks and proposed field-visit information.",
    "How many old-owner records are associated with the application?",
    "Who appears in the proposed subdivision-owner records, and how are their relationships represented?",
    "Does the proposed subdivision have an existing patta number or is that field blank?",
    "What does the Tahsildar's recorded return remark say needs to be submitted?"
],
"Chain7_012766": [
    "Please examine 2026/0154/02/012766. This one appears to have reached Order Generated, but I want to understand what happened to its original survey 76, old patta 3908 and the two temporary subdivisions. Identify the new subdivision numbers, corresponding new patta numbers, adopted areas and owner records. Present each proposed subdivision separately and avoid mixing the old owner with the proposed owners.",
    "What are the two temporary subdivision identifiers?",
    "Which new subdivision numbers were assigned?",
    "Which patta number is associated with each resulting subdivision?",
    "How do the proposed ownership records differ between the two subdivisions?"
],
"Chain9_0158_000002": [
    "Investigate application 2026/0158/02/000002, associated with survey number 137 and patta 4295. This appears in both the correction-modification workflow and the UAREG modification records. Trace the chronological desk recommendations, remarks and dates, identify the final approval information, and compare it with the modification details. I want to know whether the application workflow and parcel modification evidence agree.",
    "When did the SIS recommendation occur?",
    "What happened after that at the ZDT, SD and DIS stages?",
    "Which later stage recorded the final approval, and on what date?",
    "Can you identify the approval file reference from the UAREG table and compare its date with the final workflow date?"
],
"Chain10_0158_001804": [
    "I am looking at 2026/0158/02/001804, related to survey number 161. The main register shows an open status, but the correction-modification table contains several positive recommendations and an Approved remark at a later desk. Please reconcile the sources. Give me the register status, latest workflow event, desk-level recommendations and remarks, and explain whether the presence of an Approved remark is enough to conclude that the entire application is complete.",
    "Which was the latest workflow transition?",
    "What does the correction-modification table say about the Tahsildar's remark?",
    "Why should we not automatically interpret that remark as proof of final application closure?",
    "Is there more than one UAREG modification row associated with this application? Show their operation values separately."
],
"Chain14_045472_owners": [
    "I need to inspect the ownership information for 2026/0153/02/045472. There are separate old-owner and IGRS-owner extracts, and I don't want their rows mixed up. Please count the rows in each source, identify the fields they share, compare the available owner and relationship information, and distinguish source records from the applicant-information record.",
    "How many IGRS-owner records are linked to this application?",
    "Does the old-owner table contain the same number of rows?",
    "Can you explain why these two tables should not automatically be treated as identical?"
],
"Chain19_list_filter_compare": [
    "Show me the applications under service code 0154, but I am mainly interested in applications involving more than one temporary subdivision. For each matching application, include the application number, survey number, existing patta, number of subdivision rows, application status and whether the proposed owner information is present. Do not count owner rows as separate applications.",
    "Now show only the rejected ones.",
    "Which one has the largest total recorded subdivision area?",
    "Show the owner details for that application, grouped by temporary subdivision."
],
"StandaloneSample_A": [
    "Give me all NISD applications which have a rejected status, but separate those rejected due to missing linked documents from those with other recorded remarks. Include the survey number, subdivision, old patta number, final update date and workflow history. Don't invent a rejection reason when the remarks are blank."
],
"StandaloneSample_B": [
    "The application contains multiple subdivision numbers, so how many applications are there?"
],
"StandaloneSample_C": [
    "What is the actual reason code for the rejection of 2026/0153/02/045586? Translate it into the reason."
],
"StandaloneSample_D": [
    "Are there applications where SIS recommended approval but a later desk recorded a rejection or a different recommendation? Compare the SIS and subsequent desk remarks without treating a recommendation as a final approval."
],
"Chain13_fline_000267": [
    "Please inspect F-Line application 2026/0178/02/000267. The application-information table shows a proposed field-visit date, an amount, payment details and an Order Generated status. Cross-check those with the main application log and workflow history, and explain which date represents the proposed visit, which represents the workflow action and which represents the application update. Don't treat them as interchangeable.",
    "What is the proposed field-visit date?",
    "What does the actual workflow record say happened on that date?",
    "What amount and payment mode appear in the application-information record?"
],
"Chain17_servicecode0165": [
    "Look at application 2026/0165/02/000043. I want its application information, current register status and workflow history. Then check whether there is a specialized correction-modification, UAREG, Natham, F-Line or TSLR detail record for this particular application. Don't invent module-specific details if the source table does not hold a matching row.",
    "Which source tables actually contain this application?",
    "Which specialized detail sources have no matching record?",
    "Can you still provide a register-level application answer without pretending a module-specific record exists?"
],
"AmbiguousFollowups_block1": [
    "Compare 2026/0153/02/045470 and 2026/0153/02/045472. Both appear to have been submitted around the same time. Compare their survey numbers, subdivisions, old patta numbers, registration deed references, final statuses and rejection remarks.",
    "What about the other one?",
    "Which one was submitted first?",
    "Was that the rejected one?",
    "What was the reason for that?",
    "No, I meant the update date, not submission date."
],
"ConfusingQuestions_block1": [
    "Investigate 2026/0154/02/012766.",
    "The application contains multiple subdivision numbers, so how many applications are there?",
    "There is a blank new patta number. Does that mean the application was rejected?",
    "The application shows a visit date but I cannot see a completed visit. Did someone actually visit?"
],
"Tamil_block1": [
    "2026/0154/02/012715 application-la rendu temporary subdivision irukka? Rendu area-vum thaniya kaami.",
    "Andha application-la old owner details-um new subdivision owner details-um compare pannunga.",
    "Ippo second subdivision-ku enna new subdivision number assign pannirukanga?",
    "Adhoda patta number enna?"
],
"Tamil_block2": [
    "2026/0153/02/045470-la application submit date-um registration date-um enna difference?",
    "Andha application reject aana exact reason enna? Workflow remarks-um transfer remarks-um compare pannu."
],
"TestB_endtoend": [
    "Investigate 2026/0158/02/001804. The register says it is still open, but the correction-modification information has positive recommendations and an Approved remark.",
    "Tell me which table says what.",
    "Which was the last workflow action?",
    "What did the Tahsildar record?",
    "Does the Approved remark mean the full application is closed?",
    "Check the UAREG table too.",
    "Why are there two operation rows?",
    "Which value is the current survey number?",
    "Is there an approval-file reference?",
    "What information is still missing before you can definitively say it is completed?"
],
}

outdir = os.path.join(os.path.dirname(__file__), "qbank_chains")
os.makedirs(outdir, exist_ok=True)
for cid, qs in CHAINS.items():
    with open(os.path.join(outdir, cid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"chain_id": cid, "questions": qs}, f, ensure_ascii=False, indent=2)
print("wrote", len(CHAINS), "chain files")
