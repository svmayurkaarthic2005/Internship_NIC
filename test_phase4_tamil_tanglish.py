"""Targeted regression test suite for Phase 4: Tamil / Tanglish terminology defects.

Covers:
4.1 Acronym Tamil suffixes (token_with_tamil_suffix_pattern, _SERVICE_CODE_ISD_RE, _SERVICE_CODE_NISD_RE)
4.2 Domain gibberish whitelist (_is_gibberish, TSLR, DTCP, HQDT, FMB)
4.3 Tanglish vocabulary detection (_TANGLISH_WORDS, detect_language)
"""
import sys
import re

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

total = 0
bad = []

def check(name, condition, detail=""):
    global total
    total += 1
    if condition:
        print(f"ok   {name}")
    else:
        bad.append((name, detail))
        print(f"FAIL {name}" + (f" -- {detail}" if detail else ""))


# ─────────────────────────────────────────────────────────────────────────────
# 4.1 Acronym Tamil suffixes
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.chat_shared import token_with_tamil_suffix_pattern
from backend.services.chatbot.chat_response_formatting import _SERVICE_CODE_ISD_RE, _SERVICE_CODE_NISD_RE

pat_isd = re.compile(token_with_tamil_suffix_pattern("isd"), re.IGNORECASE)
pat_nisd = re.compile(token_with_tamil_suffix_pattern("nisd"), re.IGNORECASE)

check("4.1: pat_isd matches bare 'ISD'",
      bool(pat_isd.search("ISD applications")))

check("4.1: pat_isd matches 'ISD-க்கு'",
      bool(pat_isd.search("ISD-க்கு கட்டணம் என்ன?")))

check("4.1: pat_isd matches 'ISDக்கு'",
      bool(pat_isd.search("ISDக்கு கட்டணம் என்ன?")))

check("4.1: pat_isd matches 'ISDயில்'",
      bool(pat_isd.search("ISDயில் எத்தனை உள்ளன?")))

check("4.1: pat_isd does NOT match inside 'NISD'",
      not bool(pat_isd.search("NISD applications")))

check("4.1: pat_isd does NOT match inside 'NISDக்கு'",
      not bool(pat_isd.search("NISDக்கு வரைபடம் தேவையா?")))

check("4.1: pat_nisd matches 'NISD-க்கு'",
      bool(pat_nisd.search("NISD-க்கு வரைபடம் தேவையா?")))

check("4.1: pat_nisd matches 'NISDக்கு'",
      bool(pat_nisd.search("NISDக்கு வரைபடம் தேவையா?")))

check("4.1: _SERVICE_CODE_ISD_RE matches 'ISD-க்கு கட்டணம்'",
      bool(_SERVICE_CODE_ISD_RE.search("ISD-க்கு கட்டணம்")))

check("4.1: _SERVICE_CODE_ISD_RE does NOT match 'NISD-க்கு'",
      not bool(_SERVICE_CODE_ISD_RE.search("NISD-க்கு கட்டணம்")))

check("4.1: _SERVICE_CODE_NISD_RE matches 'NISD-க்கு கட்டணம்'",
      bool(_SERVICE_CODE_NISD_RE.search("NISD-க்கு கட்டணம்")))

# Disallow slash in application numbers
pat_0154 = re.compile(token_with_tamil_suffix_pattern("0154", disallow_slash=True), re.IGNORECASE)
check("4.1: disallow_slash matches standalone '0154-க்கு'",
      bool(pat_0154.search("0154-க்கு எவ்வளவு?")))
check("4.1: disallow_slash does NOT match inside application number '2025/0154/28/000001'",
      not bool(pat_0154.search("2025/0154/28/000001")))


# ─────────────────────────────────────────────────────────────────────────────
# 4.2 Domain gibberish whitelist
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.chat_smalltalk import _is_gibberish

check("4.2: 'TSLR' is not gibberish",
      _is_gibberish("TSLR") is False)

check("4.2: 'DTCP' is not gibberish",
      _is_gibberish("DTCP") is False)

check("4.2: 'HQDT' is not gibberish",
      _is_gibberish("HQDT") is False)

check("4.2: 'TSLR DTCP' is not gibberish",
      _is_gibberish("TSLR DTCP") is False)

check("4.2: 'asdfghjk' is gibberish",
      _is_gibberish("asdfghjk") is True)

check("4.2: 'qwertyuiop' is gibberish",
      _is_gibberish("qwertyuiop") is True)


# ─────────────────────────────────────────────────────────────────────────────
# 4.3 Tanglish vocabulary detection
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.rag_language import _TANGLISH_WORDS, detect_language

tanglish_new_tokens = [
    "panna", "thirumba", "kedaikkum", "kedaikuma", "kidaikkum",
    "poidum", "poiduma", "poyidum", "azhiyum", "aagum", "aagumaa"
]
for tok in tanglish_new_tokens:
    check(f"4.3: '{tok}' present in _TANGLISH_WORDS", tok in _TANGLISH_WORDS)

check("4.3: detect_language classifies 'status thirumba kedaikuma?' as tanglish",
      detect_language("status thirumba kedaikuma?") == "tanglish")

check("4.3: detect_language classifies 'idhu poiduma?' as tanglish",
      detect_language("idhu poiduma?") == "tanglish")

check("4.3: detect_language classifies 'change aagum' as tanglish",
      detect_language("change aagum") == "tanglish")


print(f"\nPhase 4 tests: {total - len(bad)}/{total} passed")
if bad:
    print("FAILED tests:")
    for b, d in bad:
        print(f"  - {b}: {d}")
    sys.exit(1)
