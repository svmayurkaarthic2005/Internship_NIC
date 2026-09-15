/* Generates self-contained, synthetic attachments for SIS chatbot testing.
 * No project or production records are included. */
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const root = path.join(__dirname, '..', 'backend', 'documents', 'test_fixtures');
fs.mkdirSync(root, { recursive: true });

function write(name, data) {
  fs.writeFileSync(path.join(root, name), data, typeof data === 'string' ? 'utf8' : undefined);
}

function xml(value) {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

const crcTable = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0); t[n] = c >>> 0; }
  return t;
})();
function crc32(buf) { let c = 0xffffffff; for (const b of buf) c = (c >>> 8) ^ crcTable[(c ^ b) & 255]; return (c ^ 0xffffffff) >>> 0; }
function zipStore(entries) {
  const records = [], central = []; let offset = 0;
  for (const [filename, value] of entries) {
    const name = Buffer.from(filename); const data = Buffer.isBuffer(value) ? value : Buffer.from(value, 'utf8'); const crc = crc32(data);
    const local = Buffer.alloc(30); local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(0, 6); local.writeUInt16LE(0, 8); local.writeUInt32LE(crc, 14); local.writeUInt32LE(data.length, 18); local.writeUInt32LE(data.length, 22); local.writeUInt16LE(name.length, 26);
    records.push(local, name, data);
    const cd = Buffer.alloc(46); cd.writeUInt32LE(0x02014b50, 0); cd.writeUInt16LE(20, 4); cd.writeUInt16LE(20, 6); cd.writeUInt16LE(0, 8); cd.writeUInt16LE(0, 10); cd.writeUInt32LE(crc, 16); cd.writeUInt32LE(data.length, 20); cd.writeUInt32LE(data.length, 24); cd.writeUInt16LE(name.length, 28); cd.writeUInt32LE(offset, 42);
    central.push(cd, name); offset += local.length + name.length + data.length;
  }
  const centralSize = central.reduce((n, b) => n + b.length, 0); const end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(entries.length, 8); end.writeUInt16LE(entries.length, 10); end.writeUInt32LE(centralSize, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...records, ...central, end]);
}
function para(text, style) {
  const props = style ? `<w:pPr><w:pStyle w:val="${style}"/></w:pPr>` : '';
  return `<w:p>${props}<w:r><w:t xml:space="preserve">${xml(text)}</w:t></w:r></w:p>`;
}
function table(headers, rows) {
  const cell = text => `<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr>${para(text)}</w:tc>`;
  return `<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr><w:tblGrid>${headers.map(() => '<w:gridCol w:w="2400"/>').join('')}</w:tblGrid><w:tr>${headers.map(cell).join('')}</w:tr>${rows.map(row => `<w:tr>${row.map(cell).join('')}</w:tr>`).join('')}</w:tbl>`;
}
function docx(name, title, sections) {
  const body = [para(title, 'Title')];
  for (const section of sections) {
    body.push(para(section.heading, 'Heading1'));
    for (const text of section.paragraphs || []) body.push(para(text));
    if (section.table) body.push(table(section.table.headers, section.table.rows));
  }
  body.push('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>');
  const document = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${body.join('')}</w:body></w:document>`;
  const styles = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="Aptos" w:hAnsi="Aptos"/><w:sz w:val="22"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:b/><w:sz w:val="32"/><w:color w:val="1F4E79"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/><w:rPr><w:b/><w:sz w:val="26"/><w:color w:val="1F4E79"/></w:rPr></w:style></w:styles>`;
  const entries = [
    ['[Content_Types].xml', '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'],
    ['_rels/.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'],
    ['word/document.xml', document], ['word/styles.xml', styles],
    ['word/_rels/document.xml.rels', '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'],
  ];
  write(name, zipStore(entries));
}
function pdfEscape(text) { return String(text).replace(/\\/g, '\\\\').replace(/\(/g, '\\(').replace(/\)/g, '\\)'); }
function wrap(text, width = 88) { const words = text.split(/\s+/); const lines = []; let current = ''; for (const word of words) { if ((current + ' ' + word).trim().length > width) { if (current) lines.push(current); current = word; } else current = (current + ' ' + word).trim(); } if (current) lines.push(current); return lines; }
function pdf(name, title, pages) {
  const objects = []; const add = value => { objects.push(value); return objects.length; };
  const font = add('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  const pageIds = []; const contentIds = [];
  for (const page of pages) {
    const lines = [title, '', ...page.flatMap(item => wrap(item))];
    const content = `BT\n/F1 15 Tf\n50 760 Td\n(${pdfEscape(lines[0])}) Tj\n/F1 10 Tf\n` + lines.slice(1).map((line, index) => `${index === 0 ? '0 -28 Td' : '0 -15 Td'}\n(${pdfEscape(line)}) Tj`).join('\n') + '\nET';
    contentIds.push(add(`<< /Length ${Buffer.byteLength(content, 'ascii')} >>\nstream\n${content}\nendstream`));
    pageIds.push(add(null));
  }
  const pagesId = add(null);
  pageIds.forEach((id, index) => { objects[id - 1] = `<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 ${font} 0 R >> >> /Contents ${contentIds[index]} 0 R >>`; });
  objects[pagesId - 1] = `<< /Type /Pages /Kids [${pageIds.map(id => `${id} 0 R`).join(' ')}] /Count ${pageIds.length} >>`;
  const catalog = add(`<< /Type /Catalog /Pages ${pagesId} 0 R >>`);
  let result = '%PDF-1.4\n% synthetic fixture\n'; const offsets = [0];
  objects.forEach((value, index) => { offsets.push(Buffer.byteLength(result, 'ascii')); result += `${index + 1} 0 obj\n${value}\nendobj\n`; });
  const xref = Buffer.byteLength(result, 'ascii'); result += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n` + offsets.slice(1).map(o => `${String(o).padStart(10, '0')} 00000 n \n`).join('') + `trailer\n<< /Size ${objects.length + 1} /Root ${catalog} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  write(name, Buffer.from(result, 'ascii'));
}

write('README.txt', `SIS CHATBOT TEST FIXTURES\n=========================\n\nAll records in this folder are fictional test data. They are intended for attachment upload and offline knowledge-ingestion tests only. Do not treat any names, IDs, dates, fees, or survey details as Tamil Nadu land records.\n\nFiles by purpose:\n- sis_workflow_and_document_notes.txt: workflow, document, bilingual, and follow-up questions.\n- synthetic_application_register.csv: deterministic CSV counts, sums, averages, filters, sort, and row lookups.\n- sis_case_review_pack.docx: paragraphs plus a document-check table.\n- sis_field_visit_memo.pdf: two-page, text-layer PDF for page citations.\n- random_topics_notes.txt, library_inventory.csv, community_club_brief.docx, and hobby_event_note.pdf: intentionally non-SIS content for grounding and out-of-scope tests.\n\nSuggested questions:\n1. What does the workflow note say about an ISD application?\n2. Which document is missing for TEST-2026-ISD-001?\n3. How many approved rows are in synthetic_application_register.csv?\n4. What is the total fee for Ward 102?\n5. What does sis_field_visit_memo.pdf say about the eastern boundary?\n6. What is the highest library late fee?\n7. What does hobby_event_note.pdf say about the chess club?\n`);

write('sis_workflow_and_document_notes.txt', `SYNTHETIC SIS WORKFLOW AND DOCUMENT NOTES\n\nPurpose: attachment test data only. No entry below is a live application.\n\nCase reference: TEST-2026-ISD-001\nApplicant: Kavitha Raman (fictional)\nService: ISD / service code 0154. ISD means Involving Sub-Division: a parcel is split, a field inspection is normally required, and an SD sketch is needed before the file can be completed.\nStatus: in_progress at the Senior Draughtsman stage.\nSurvey: Ward 102, Survey 77/3B, area 0.084 hectares.\nJoint owners: Kavitha Raman and Arun Raman, each with a one-half share.\nSale deed: deed number SYN-2026-7781, shown as registered in this fictitious note.\n\nDocument review:\n- Submitted: sale deed copy, patta copy, EC, applicant identity proof.\n- Missing: signed field measurement sketch.\n- Verification note: the sketch must show north arrow, scale, boundary measurements, adjoining survey references, surveyor signature, and date.\n\nWorkflow reference:\n1. SIS records inspection observations and verifies boundary information.\n2. Senior Draughtsman reviews the proposed sub-division sketch.\n3. ZDT or HQDT takes the final approval or rejection action.\nOpen statuses are pending or in_progress; a completed order is approved or rejected.\n\nNISD / service code 0153 is Not Involving Sub-Division: it is a whole-survey patta transfer and normally has no new sub-division or field visit. MERGE / service code 0155 combines sub-divisions and follows the ISD-style review chain.\n\nBilingual reminder:\nவிண்ணப்பம் சோதனை தரவு மட்டுமே. நில அளவை குறிப்புகள் மற்றும் ஆவணங்கள் அனைத்தும் கற்பனையானவை.\nISD விண்ணப்பத்திற்கு கள ஆய்வு மற்றும் துணைப்பிரிவு வரைபடம் தேவைப்படலாம்.\n\nField visit note for TEST-2026-ISD-001:\nVisit scheduled for 18 October 2026 at 10:30 AM. Bring measuring tape, field book, owner notice acknowledgement, and previous sketch. Do not infer a real address from this test file.\n`);

write('synthetic_application_register.csv', `App No,Ward,Service Code,Type,Status,Fee,Applicant,Visit Status,Submitted Date\nTEST-2026-001,102,0154,ISD,Approved,1250.00,Kavitha Raman,Completed,2026-09-02\nTEST-2026-002,102,0153,NISD,Pending,750.00,Arun Raman,Scheduled,2026-09-05\nTEST-2026-003,103,0155,MERGE,In Progress,2100.00,Meera Iyer,Unscheduled,2026-09-07\nTEST-2026-004,102,0154,ISD,Approved,1750.00,Raj Kumar,Completed,2026-09-10\nTEST-2026-005,103,0153,NISD,Rejected,600.00,Sana Ali,Not Required,2026-09-11\nTEST-2026-006,102,0154,ISD,Approved,900.00,Dev Shah,Overdue,2026-09-12\n`);

docx('sis_case_review_pack.docx', 'Synthetic SIS Case Review Pack', [
  { heading: 'Fixture purpose', paragraphs: ['This Word document is synthetic attachment data for chatbot extraction and citation tests. It is not an official order and must not be used for a real land-record decision.'] },
  { heading: 'Case summary', paragraphs: ['Case TEST-2026-ISD-001 is an ISD (service 0154) example. It concerns a fictional sub-division proposal in Ward 102. The stated status is in progress and the fictional field visit is scheduled for 18 October 2026.'] },
  { heading: 'Document review table', table: { headers: ['Document', 'Review result', 'Test note'], rows: [['Sale deed', 'Available - registered', 'Synthetic deed SYN-2026-7781'], ['Patta copy', 'Available', 'Copy only'], ['Encumbrance certificate', 'Available', 'For test coverage'], ['Field measurement sketch', 'Missing', 'Must be signed and dated']] } },
  { heading: 'Boundary observation', paragraphs: ['The fictional eastern boundary is recorded as a public pathway. The northern boundary is Survey 77/4. These labels are invented and exist only to test whether the chatbot cites a Word paragraph or table.'] },
  { heading: 'Officer action', paragraphs: ['Before forwarding the example case, request the missing signed sketch. A real officer must follow the official portal workflow and departmental rules, not this fixture.'] },
]);

pdf('sis_field_visit_memo.pdf', 'Synthetic SIS Field Visit Memo', [
  ['Page 1 - Test-only case: TEST-2026-ISD-001.', 'Visit date: 18 October 2026, 10:30 AM. Ward: 102. Survey: 77/3B. Applicant: Kavitha Raman (fictional).', 'Purpose: verify a proposed ISD boundary before sketch review. The eastern boundary is described as a public pathway. The northern boundary is noted as Survey 77/4.', 'Observation: corner stones were said to be visible at three points. This is an invented observation for chatbot attachment testing, not a field finding.'],
  ['Page 2 - Checklist and follow-up.', 'Carry the field book, measuring tape, owner notice acknowledgement, and the prior sketch. Check that the sketch has a north arrow, scale, boundary measurements, adjoining survey references, surveyor signature, and date.', 'Follow-up: the field measurement sketch is missing from the fictional application pack. Do not approve or reject any real case on the basis of this memo.'],
]);

write('random_topics_notes.txt', `INTENTIONALLY NON-SIS RANDOM TOPICS\n\nThis text is deliberately unrelated to land survey work. It helps test that the chatbot grounds an attachment question in the file, while its normal SIS-only scope rules remain effective for ordinary chat.\n\nTea tasting notes: a fictional club compared jasmine tea, masala chai, and lemon tea. The group preferred jasmine tea for its floral aroma and chose lemon tea for a hot afternoon.\n\nSpace trivia: the fictional science club schedule says its next discussion is about the James Webb Space Telescope, infrared observation, and star formation. This is a conversation prompt, not a scientific source.\n\nChess result: in a mock tournament, player Nila won 3 points, player Omar won 2 points, and player Tara won 1 point.\n`);

write('library_inventory.csv', `Title,Category,Available Copies,Late Fee,Reading Level\nThe Mango Tree,Children,4,5.00,Beginner\nMaps and Mountains,Travel,2,12.50,Intermediate\nChess Patterns,Games,1,20.00,Advanced\nTea Around the World,Food,3,7.50,Beginner\n`);

docx('community_club_brief.docx', 'Fictional Community Club Brief', [
  { heading: 'Purpose', paragraphs: ['This document is intentionally unrelated to SIS work. It tests Word extraction, attachment grounding, and refusal of unsupported inferences.'] },
  { heading: 'Club schedule', table: { headers: ['Club', 'Day', 'Topic'], rows: [['Chess club', 'Saturday', 'Opening patterns'], ['Reading club', 'Sunday', 'Short stories'], ['Science club', 'Wednesday', 'Infrared telescopes']] } },
  { heading: 'Reminder', paragraphs: ['The chess club begins at 4:00 PM in this fictional brief. No meeting location is supplied, so a chatbot should not invent one.'] },
]);

pdf('hobby_event_note.pdf', 'Fictional Hobby Event Note', [
  ['This PDF is unrelated to land records and is supplied only to test a text-layer PDF attachment.', 'The chess club mock event starts at 4:00 PM on Saturday. The reading club mock event starts at 11:00 AM on Sunday.', 'Bring a notebook to the science club discussion on infrared telescopes. No venue, ticket price, or organiser contact is stated in this fixture.'],
]);

console.log(`Created 9 test-fixture files in ${root}`);
