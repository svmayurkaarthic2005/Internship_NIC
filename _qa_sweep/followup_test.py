# -*- coding: utf-8 -*-
"""Multi-turn follow-ups over a REAL session, so `chat_messages.structured_data`
is actually written and read — the sweep's synthetic history could not do that."""
import asyncio, json, os, re, sys, uuid, html
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from driver import AsyncSessionLocal, officers, ask   # noqa
from backend.models import ChatSession                # noqa

F = json.load(open(os.path.join(HERE, 'facts.json'), encoding='utf-8'))
APPNUM = re.compile(r'\b\d{4}/\d{4}/\d{2}/\d{6}\b')
REAL = {a['application_number'] for a in F['apps']}
BY_EMP = {}
for a in F['apps']:
    BY_EMP.setdefault(a['employee_id'], set()).add(a['application_number'])


def sh(s):
    return html.unescape(re.sub(r'<[^>]+>', ' ', s or '')).strip()


CHAINS = [
    ("pending list", ["show my pending applications",
                      "which one is oldest?", "how many of them are approved?",
                      "which of them are ISD?", "do they have an IGRS number?",
                      "what is the total fee?", "show only NISD"]),
    ("csc list", ["show applications from CSC",
                  "how many are approved", "which is oldest?",
                  "do they have an IGRS number?", "list their CAN numbers"]),
    ("tamil follow-up", ["show my pending applications", "எது பழையது?",
                         "edhu pazhusu?", "எத்தனை ஒப்புதல் பெற்றவை?"]),
    ("approved list", ["show my approved applications",
                       "which took the longest?", "what is the total fee?"]),
    ("single app", ["status of {APP}", "when was it approved?",
                    "when was it submitted?", "which block is it from?",
                    "what is the CAN number?", "who is the applicant?",
                    "is it ISD or NISD?", "what is the fee?",
                    "was it returned?", "who is handling it?",
                    "இது எந்த வார்டு?"]),
    ("survey then field", ["details of survey {SURVEY}",
                           "what is the patta number?",
                           "what is the soil type?", "is there litigation?"]),
    ("no context at all", ["which is oldest?"]),
    ("no context tamil", ["எது பழையது?"]),
]


async def new_session(db, officer):
    sid = uuid.uuid4()
    db.add(ChatSession(id=sid, officer_id=officer.officer_id,
                       session_token=str(sid), is_active=True,
                       started_at=datetime.now(timezone.utc),
                       last_activity=datetime.now(timezone.utc)))
    await db.commit()
    return str(sid)


async def main():
    out = []
    bad = 0
    async with AsyncSessionLocal() as db:
        offs = {o.employee_id: o for o in await officers(db)}
        for emp in ('SIS-001', 'SIS-002'):
            officer = offs[emp]
            app = sorted(a for a in BY_EMP[emp])[0]
            survey = next(a['survey_no'] for a in F['apps']
                          if a['application_number'] == app)
            for name, turns in CHAINS:
                sid = await new_session(db, officer)
                hist = []
                print(f'\n=== [{emp}] {name} ===')
                for t in turns:
                    q = t.replace('{APP}', app).replace('{SURVEY}', str(survey))
                    r = await ask(q, officer, db, history=list(hist), session_id=sid)
                    a = sh(r.get('response'))
                    hist.append({'role': 'user', 'content': q})
                    hist.append({'role': 'assistant', 'content': r.get('response') or ''})
                    flags = []
                    if (r.get('_ms') or 0) > 8000:
                        flags.append('SLOW/LLM')
                    if r.get('intent') == 'general_query':
                        flags.append('LLM-FALLBACK')
                    found = set(APPNUM.findall(a))
                    if found - REAL:
                        flags.append(f'INVENTED {sorted(found - REAL)[:2]}')
                    if (found & REAL) - BY_EMP[emp]:
                        flags.append('FOREIGN-APP')
                    bad += len(flags)
                    print(f'  [{r.get("intent")}] {r.get("_ms")}ms {" ".join(flags)}\n    Q: {q}\n    A: {a[:260]}')
                    out.append({'emp': emp, 'chain': name, 'q': q,
                                'intent': r.get('intent'), 'ms': r.get('_ms'),
                                'flags': flags, 'a': a[:1200]})
    json.dump(out, open(os.path.join(HERE, 'followup_transcript.json'), 'w',
                        encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'\n==== {len(out)} turns, {bad} flagged ====')

asyncio.run(main())
