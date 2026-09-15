# -*- coding: utf-8 -*-
"""Score the sweep results and group the failures."""
import json, os, re, sys, html
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
F = json.load(open(os.path.join(HERE, 'facts.json'), encoding='utf-8'))
REAL = {a['application_number'] for a in F['apps']}
BY_EMP = defaultdict(set)
for a in F['apps']:
    if a['employee_id']:
        BY_EMP[a['employee_id']].add(a['application_number'])
APPNUM = re.compile(r'\b\d{4}/\d{4}/\d{2}/\d{6}\b')

STALE = {
    'fmb': re.compile(r'\bFMB\b|field measurement book', re.I),
    'chromadb': re.compile(r'chroma\s*db|chromadb', re.I),
    'vectorstore_dir': re.compile(r'vectorstore/', re.I),
    'old_officer': re.compile(r'arjun[._ ]kumar|priya[._ ]devi|ramesh[._ ]babu|lakshmi[._ ]narayanan', re.I),
}
MUTATION_CLAIM = re.compile(
    r"\b(has been|have been|was|were|is)\s+(cleared|deleted|removed|updated|approved|rejected|reset|truncated)\b"
    r"|\bI (have|'ve) (cleared|deleted|updated|removed|reset)\b", re.I)
NEGATED = re.compile(r"\b(nothing|no data|not|cannot|can't|unable)\b", re.I)
NONANSWER = re.compile(
    r"could not find|couldn't find|i (do not|don't) have|no information|"
    r"please specify|could you (please )?specify|not available|i am unable|"
    r"which application", re.I)


def strip_html(s):
    s = re.sub(r'<[^>]+>', ' ', s or '')
    return html.unescape(re.sub(r'\s+', ' ', s)).strip()


REFUSAL_RE = re.compile(
    r'outside your|not have access|could not read|no application|not found|'
    r'jurisdiction|not recognis|not recogniz|does not exist|check the number',
    re.I)


def load(path):
    rows, seen = [], set()
    for l in open(os.path.join(HERE, path), encoding='utf-8'):
        try:
            r = json.loads(l)
        except Exception:
            continue
        if r['id'] in seen:
            continue
        seen.add(r['id'])
        rows.append(r)
    return rows


def analyse(rows):
    issues = defaultdict(list)
    for r in rows:
        txt = strip_html(r.get('response') or '')
        low = txt.lower()
        if r.get('error'):
            issues['exception'].append(r); continue
        if not txt:
            issues['empty_response'].append(r); continue

        # hallucinated / foreign application numbers
        found = set(APPNUM.findall(txt))
        unknown = found - REAL
        if unknown:
            r['_detail'] = sorted(unknown)[:3]
            issues['invented_app_number'].append(r)
        foreign = (found & REAL) - BY_EMP[r['emp']]
        if (foreign and 'cross_jurisdiction' not in r['tags']
                and 'fake_app' not in r['tags'] and not REFUSAL_RE.search(txt)):
            r['_detail'] = sorted(foreign)[:3]
            issues['foreign_app_leaked'].append(r)

        for k, rx in STALE.items():
            if rx.search(txt) and not (k == 'fmb' and re.search(r'no\s+FMB|not.{0,25}FMB|FMB.{0,30}not', txt, re.I)):
                issues['stale_' + k].append(r)

        if MUTATION_CLAIM.search(txt) and not NEGATED.search(txt) and (
                'crazy' in r['tags']):
            issues['claims_mutation'].append(r)

        # expectations
        exp = r.get('expect') or {}
        if exp.get('intent_in') and r.get('intent') not in exp['intent_in']:
            r['_detail'] = f"want {exp['intent_in']} got {r.get('intent')}"
            issues['wrong_intent'].append(r)
        if exp.get('app') and exp['app'] not in txt and NONANSWER.search(low):
            issues['field_nonanswer'].append(r)
        if exp.get('foreign_app'):
            # asking about someone else's application must be refused, not answered
            if exp['foreign_app'] in txt and not re.search(
                    r'jurisdiction|not (assigned|yours)|outside|permission|அதிகார', txt, re.I):
                issues['cross_jurisdiction_answered'].append(r)
        if exp.get('must_not_be_invented'):
            if not re.search(r'not found|no application|could not|does not exist|invalid|not recognis|not recogniz',
                             low):
                issues['fake_app_not_refused'].append(r)

        if (r.get('ms') or 0) > 8000:
            issues['slow_llm_fallback'].append(r)
        if NONANSWER.search(low) and 'crazy' not in r['tags'] and 'fake_app' not in r['tags']:
            issues['nonanswer'].append(r)
    return issues


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'results.jsonl'
    rows = load(path)
    print(f'{len(rows)} results')
    print('intents:', Counter(r['intent'] for r in rows).most_common(20))
    iss = analyse(rows)
    print()
    for k in sorted(iss, key=lambda k: -len(iss[k])):
        print(f'{len(iss[k]):5d}  {k}')
    json.dump({k: [{'id': r['id'], 'q': r['q'], 'emp': r['emp'], 'intent': r['intent'],
                    'ms': r['ms'], 'detail': r.get('_detail'), 'tags': r['tags'],
                    'history': r.get('history'),
                    'resp': strip_html(r['response'])[:500]} for r in v]
               for k, v in iss.items()},
              open(os.path.join(HERE, 'issues.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n-> issues.json')
