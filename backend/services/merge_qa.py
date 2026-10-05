"""Merge links between applications (`applications.merged_application_id`, the lead
application of a group of ISD files filed together).

The forward lookup ("merge id of <app>") is answered from the application record by
the field handlers; this module reads the two shapes they cannot:

* reverse  -- "which applications are merged with <app>"
* overview -- "which applications have a merge id", "how many merged applications"

Both are listed only from applications the officer can see: the candidate numbers come
from the table, and every one is re-checked against the officer's jurisdiction before it
is shown. A parent outside the jurisdiction is never confirmed to exist."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

_APP = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")
_MERGE = re.compile(r"\bmerg\w*|இணைக்கப்பட்ட|இணைப்பு|merge", re.IGNORECASE)
_LIST = re.compile(r"\b(?:which|list|show|how\s+many|count|any|all|number\s+of|evlo|ethanai)\b|எத்தனை|எவை|எவ்வளவு|எந்த",
                   re.IGNORECASE)
_REVERSE = re.compile(r"\bmerged\s+(?:with|into|to)\b|\bchild(?:ren)?\b|\bmerge\s+group\b|\bmerged\s+under\b|"
                      r"\bmerge\s+parent\s+of\b|\bsame\s+group\b", re.IGNORECASE)
_NOT_THIS = re.compile(r"service\s*code|\bupload|\bpdf\b|\bcan\s+i\b|\bhow\s+(?:do|to)\b|\btype\b", re.IGNORECASE)


def parse(message: str) -> Optional[Tuple[str, Optional[str]]]:
    """("reverse", number) | ("overview", None) | None."""
    m = message or ""
    if not _MERGE.search(m) or _NOT_THIS.search(m):
        return None
    nums = _APP.findall(m)
    if nums and _REVERSE.search(m) and re.search(r"\b(?:which|list|show|how\s+many|what|any)\b|எவை|எந்த", m, re.IGNORECASE):
        return ("reverse", nums[0])
    if not nums and re.search(r"\b(?:it|this|its|that|athu|idhu)\b|இது|அது", m, re.IGNORECASE) and not re.search(r"\bapplications\b|விண்ணப்பங்க", m, re.IGNORECASE):
        return None  # "with which application is it merged": a field of the application on screen
    if not nums and _LIST.search(m) and re.search(r"merge[sd]?\b|merge\s+(?:id|applid|application)", m, re.IGNORECASE):
        return ("overview", None)
    return None


async def linked(db, officer, parent: Optional[str]) -> List[dict]:
    """The officer's own applications that carry a merge id (of `parent`, when given)."""
    from sqlalchemy import text
    from backend.services.postgres import get_applications_by_numbers
    sql = "SELECT application_number FROM applications WHERE merged_application_id IS NOT NULL AND merged_application_id <> ''"
    args = {}
    if parent:
        sql += " AND merged_application_id = :p"
        args["p"] = parent
    pairs = (await db.execute(text(sql.replace("SELECT application_number", "SELECT application_number, merged_application_id")
                                    + " ORDER BY application_number"), args)).all()
    if not pairs:
        return []
    got = await get_applications_by_numbers(db, officer, [r[0] for r in pairs], include_rejected=True)
    parent_of = {r[0]: r[1] for r in pairs}
    out = []
    for r in (got or {}).get("applications") or []:
        out.append({**r, "merged_with": parent_of.get(r.get("application_number"))})
    return out


async def answer(db, officer, message: str, ta: bool):
    """(text, rows) or None"""
    p = parse(message)
    if not p:
        return None
    kind, parent = p
    rows = await linked(db, officer, parent)
    if kind == "reverse":
        if not rows:
            return ((f"{parent}-உடன் இணைக்கப்பட்ட விண்ணப்பம் உங்கள் அதிகார எல்லையில் பதிவில்லை." if ta else
                     f"No application in your jurisdiction is recorded as merged with {parent}."), [])
        head = (f"{parent}-உடன் இணைக்கப்பட்ட {len(rows)} விண்ணப்பம்(கள்):" if ta else
                f"{len(rows)} application(s) in your jurisdiction merged with {parent}:")
    else:
        if not rows:
            return (("உங்கள் அதிகார எல்லையில் இணைப்பு எண் உள்ள விண்ணப்பம் எதுவும் இல்லை." if ta else
                     "None of your applications carries a merge id."), [])
        head = (f"இணைப்பு எண் உள்ள {len(rows)} விண்ணப்பம்(கள்):" if ta else
                f"{len(rows)} of your application(s) carry a merge id:")
    w = "இணைந்தது" if ta else "merged with"
    lines = [f"• {r.get('application_number')} — {r.get('type')}, {r.get('status')}; {w} {r.get('merged_with')}" for r in rows[:25]]
    return head + "\n" + "\n".join(lines), rows
