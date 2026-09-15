# Sample upload files for testing chat attachments

These files exercise `backend/services/doc_extract.py`, `attachment_store.py`,
`csv_ops.py` and `attachment_qa.py` (see the "Chat attachments" section of
CLAUDE.md). Upload them through the chat UI's attach button, one or more at a
time, and try the prompts below. All confirmed to extract cleanly with
`doc_extract.extract()` on this checkout.

## Domain files (SIS-relevant — should be answered with citations)

| file | what it's for |
|---|---|
| `patta_transfer_order_2026_0154_28_001280.pdf` | 3-page order copy — page citations, ISD workflow, fee, patta number |
| `sale_deed_extract_1123_2025.pdf` | second PDF — forces "which file did you mean?" when both are open |
| `field_inspection_report_2026_0153_28_000980.docx` | headings + a 4-row table — paragraph/table citations |
| `applications_register.csv` | 8 rows, mixed status/type/ward/fee — deterministic count/sum/avg/group, plus one formula-looking cell (`=SUM(F2:F6)`) to check it's treated as text, never evaluated |
| `workflow_notes.txt` | 10 numbered lines — line-range citations |

Try:
1. Upload `patta_transfer_order_2026_0154_28_001280.pdf` alone.
   - "What is the total fee in this order?" → should cite a page and answer ₹530.
   - Follow-up with no filename: "which page is that on?" / "and who approved it?" — tests whether it keeps referencing *this* document across turns.
2. Upload `applications_register.csv` alone.
   - "How many applications are ISD?" / "what's the total fee for approved applications?" / "which ward has the most applications?" — all should be computed, not narrated, with row numbers.
   - "what does row 6's submission_date column say?" — should report it as text/unparseable rather than evaluating the formula-looking cell.
3. Upload both PDFs together.
   - "what is the survey number?" — both files have one; should ask which file.
   - "what is the survey number in the sale deed?" — should resolve without asking.
4. Upload `field_inspection_report_2026_0153_28_000980.docx`.
   - "which documents are still pending?" → should cite the table and name the Encumbrance Certificate.
5. Upload `workflow_notes.txt`.
   - "what did the notes say about the CAN number?" → should cite line 5.
   - Then ask an unrelated register question in the same turn (no filename): "how many applications do I have?" — should NOT answer from the file; it should fall back to the officer's real register (per `targets_attachment()` routing rules).

## Off-topic / random files (should NOT be answered as if they were SIS records)

| file | what it's for |
|---|---|
| `grandma_filter_coffee_recipe.txt` | pure off-topic text |
| `random_trivia_notes.docx` | off-topic, multiple paragraphs |
| `random_cricket_scores.csv` | off-topic CSV — tests that csv_ops still computes correctly on non-SIS data, and that an SIS-shaped question against it gets a "not found" instead of a fabricated record |
| `weekly_weather_report.pdf` | off-topic PDF |

Try:
1. Upload `grandma_filter_coffee_recipe.txt` and ask "how much coffee powder does this use?" — should answer from the file (evidence exists, topic doesn't matter).
2. With the same file open, ask "how many applications do I have pending?" — should NOT try to answer from the recipe; should route to the real register.
3. Upload `random_cricket_scores.csv` and ask "which team won the most matches?" — deterministic group/count, unrelated to SIS.
4. With no file open at all, ask an out-of-scope question directly: "what's the weather like today?" or "write me a poem" — should get the deterministic scope refusal, not a generated answer (`_is_out_of_scope`).
5. Upload `weekly_weather_report.pdf` and ask "what is the CAN number of this applicant?" — no such content exists in the file, so it should say it could not find this in the document (`UPLOAD_MIN_EVIDENCE_SCORE` refusal), not invent one.

## Cross-cutting things worth checking

- **Reference across turns without re-naming the file**: after any upload + first answer, ask a bare follow-up ("and the date?", "who signed it?", "evlo?" in Tanglish) and confirm it still resolves against the uploaded document rather than falling through to the LLM ungrounded, or to the officer's database records.
- **Tamil / Tanglish**: ask one of the above questions in Tamil script or Tanglish against an uploaded English document — evidence matching should still work (aliases in `attachment_store.py`).
- **Multiple files, ambiguous question**: with 2+ files uploaded, ask something generic ("what is the application number?") and confirm it asks which file rather than guessing.
- **Session boundary**: start a new chat session (or wait past retention) and confirm an old attachment is no longer answerable — nothing should leak across sessions.
