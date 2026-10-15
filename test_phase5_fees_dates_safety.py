"""Targeted regression test suite for Phase 5: Fees, IDs, Dates, and DB Safety defects.

Covers:
5.1 Fee lookup isolation (_fee_lookup_data, _fee_lookup_text)
5.2 Full long numeric identifiers (_unidentified_number_answer)
5.3 Comprehensive invalid calendar dates (_invalid_calendar_date)
5.4 District/Taluk safety (_dc_special)
5.5 Missing submission date safety in _age_sla_answer
"""
import sys
import datetime
from unittest.mock import AsyncMock, patch

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
# 5.1 Fee lookup isolation
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.chat_smalltalk import _fee_lookup_text

sd_with_only_code = {
    "fee_lookup": {
        "groups": [],
        "channel": None,
        "asked_types": [],
        "code_fee_infos": [{
            "service_code": "0158",
            "service_name": "Patta Name Transfer - Full Field",
            "fee_display": "Rs 100",
            "total_fee": "Rs 100",
            "sla_days": "15 working days",
            "sla_days_ta": "15 வேலை நாட்கள்"
        }]
    }
}
text_res = _fee_lookup_text(sd_with_only_code, is_tamil=False)
check("5.1: Specific code fee lookup mentions Service 0158",
      "Service 0158" in text_res and "Rs 100" in text_res, text_res)
check("5.1: Specific code fee lookup does NOT contaminate with ISD/NISD groups",
      "ISD" not in text_res and "NISD" not in text_res, text_res)


# ─────────────────────────────────────────────────────────────────────────────
# 5.2 Long numeric identifiers
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.chat_smalltalk import _unidentified_number_answer

ans_10 = _unidentified_number_answer("what is 9876543210?")
check("5.2: 10-digit number '9876543210' is fully preserved",
      "'9876543210'" in ans_10, ans_10)

ans_12 = _unidentified_number_answer("tell me about 123456789012")
check("5.2: 12-digit number '123456789012' is fully preserved",
      "'123456789012'" in ans_12, ans_12)


# ─────────────────────────────────────────────────────────────────────────────
# 5.3 Comprehensive invalid calendar dates
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.chat_smalltalk import _invalid_calendar_date

check("5.3: '31st February 2025' detected as invalid",
      _invalid_calendar_date("applications on 31st February 2025") == ("31", "February"))

check("5.3: 'Feb 31' (Month-Day) detected as invalid",
      _invalid_calendar_date("what happened on Feb 31?") == ("31", "February"))

check("5.3: 'April 31' (Month-Day) detected as invalid",
      _invalid_calendar_date("status on April 31") == ("31", "April"))

check("5.3: ISO date '2026-02-29' (non-leap year) detected as invalid",
      _invalid_calendar_date("what about 2026-02-29?") == ("29", "February"))

check("5.3: ISO date '2025-04-31' detected as invalid",
      _invalid_calendar_date("submitted on 2025-04-31") == ("31", "April"))

check("5.3: Valid ISO leap year '2024-02-29' is valid",
      _invalid_calendar_date("submitted on 2024-02-29") is None)

check("5.3: Application number '2025/0154/28/000001' is NOT treated as invalid date",
      _invalid_calendar_date("2025/0154/28/000001") is None)


# ─────────────────────────────────────────────────────────────────────────────
# 5.4 District/Taluk safe lookup
# ─────────────────────────────────────────────────────────────────────────────
import asyncio
from backend.services.chatbot.chat_smalltalk import _dc_special

class DummyOfficer:
    jurisdiction_name = "Unknown Jurisdiction"
    jurisdiction_ids = []

async def test_dc():
    dummy_db = AsyncMock()
    # Call _dc_special with unknown district name — must not raise KeyError
    res = await _dc_special(dummy_db, DummyOfficer(), "what is the district code?", None, "en")
    return res

dc_res = asyncio.run(test_dc())
check("5.4: _dc_special handles unknown jurisdiction without KeyError",
      dc_res is None or isinstance(dc_res, str))


# ─────────────────────────────────────────────────────────────────────────────
# 5.5 Missing submission date safety in _age_sla_answer
# ─────────────────────────────────────────────────────────────────────────────
from backend.services.chatbot.chat_smalltalk import _age_sla_answer

today = datetime.date(2026, 4, 15)

# Missing submission_date (None)
sd_none = {"type": "ISD", "status": "pending", "submission_date": None}
ans_none = _age_sla_answer("2025/0154/28/000001", sd_none, today, is_tamil=False)
check("5.5: _age_sla_answer with submission_date=None returns graceful message",
      "no submission date recorded" in ans_none, ans_none)

# Malformed submission_date
sd_bad = {"type": "ISD", "status": "pending", "submission_date": "not-a-date"}
ans_bad = _age_sla_answer("2025/0154/28/000001", sd_bad, today, is_tamil=False)
check("5.5: _age_sla_answer with malformed date returns graceful message",
      "no submission date recorded" in ans_bad, ans_bad)

# Valid submission_date
sd_valid = {"type": "ISD", "status": "pending", "submission_date": "2026-03-01"}
ans_valid = _age_sla_answer("2025/0154/28/000001", sd_valid, today, is_tamil=False)
check("5.5: _age_sla_answer with valid date returns age statement",
      "2026-03-01" in ans_valid and "ISD" in ans_valid, ans_valid)

# Tamil graceful message for missing date
ans_none_ta = _age_sla_answer("2025/0154/28/000001", sd_none, today, is_tamil=True)
check("5.5: _age_sla_answer with submission_date=None returns Tamil graceful message",
      "சமர்ப்பிப்பு தேதி பதிவேட்டில் இல்லை" in ans_none_ta, ans_none_ta)


print(f"\nPhase 5 tests: {total - len(bad)}/{total} passed")
if bad:
    print("FAILED tests:")
    for b, d in bad:
        print(f"  - {b}: {d}")
    sys.exit(1)
