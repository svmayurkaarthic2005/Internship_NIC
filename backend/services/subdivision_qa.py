"""Questions about an application's sub-division plots.

An ISD application splits one parcel into plots (1/T1, 1/T2, ...). The extract
records, per plot: the temporary and the new sub-division number, the existing
and the new patta number, area as hectare + ares + square metres, land type and
land use, tax, soil, plot number and type, and the "group" numbers of a clubbed
split. `application_sub_divisions` projects only the numbers, so every other
column used to be answered "not held in your register" even though it is on file.

This module reads the plot rows (read-only) and answers those questions:

    attributes(message)   which fields a message asks for, tolerant of spelling
                          slips and of Tamil / Tanglish wording
    claims(attrs)         whether this module should answer, or leave the
                          question to the general field handlers
    load_plots(...)       the plot rows for one application
    answer(...)           the rendered answer, every figure read from the rows

Nothing here is generated: a field with no value is reported as not recorded (or
not assigned yet, when the file is still open) rather than guessed.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from backend.utils.fuzzy import is_token_typo_match
from backend.utils.logger import get_logger

logger = get_logger(__name__)

_APP_NO = re.compile(r"\d{4}/\d{3,4}/\d{1,3}/\d+")

# ── Tamil / Tanglish cues -> canonical English tokens ─────────────────────────
# Tamil is matched as a substring, never with \b: the virama and vowel signs are
# not word characters, so a trailing \b never closes (the same trap the rest of
# the app documents).
_TAMIL_CUES = [
    ("தற்காலிக", " temporary "), ("உட்பிரிவு", " subdivision "), ("உட்பிரிவு", " subdivision "),
    ("புதிய", " new "), ("இறுதி", " final "), ("தற்போதுள்ள", " existing "),
    ("தற்போதைய", " existing "), ("ஏற்கனவே", " existing "), ("பழைய", " existing "),
    ("பட்டா", " patta "), ("சர்வே", " survey "), ("கணக்கெண்", " survey "),
    ("நில அளவை", " survey "), ("பரப்பளவு", " area "), ("பரப்பு", " area "),
    ("ஹெக்டேர்", " hectare "), ("எக்டேர்", " hectare "), ("ஏர்ஸ்", " ares "),
    ("சதுர மீட்டர்", " sqm "), ("சதுரமீட்டர்", " sqm "), ("நில வகை", " land type "),
    ("நிலத்தின் வகை", " land type "), ("நில பயன்பாடு", " land use "),
    ("நிலப் பயன்பாடு", " land use "), ("நில உபயோகம்", " land use "),
    ("வரி", " tax "), ("மனை", " plot "), ("மண்", " soil "), ("முதன்மை", " primary "),
    ("இரண்டாம்", " secondary "), ("குழு", " group "), ("சமர்ப்பி", " submit "),
    ("தேதி", " date "), ("விவரங்கள்", " details "), ("விவரம்", " details "),
    ("முழு", " complete "), ("உரிமையாளர்", " owner "), ("சொந்தக்காரர்", " owner "),
    ("எண்", " number "), ("பெயர்", " name "), ("நிலை", " status "),
    ("வகை", " type "), ("விகிதம்", " rate "),
]
_TANGLISH_CUES = [
    (r"thatkalika|tharkalika|thatkaalika", "temporary"), (r"puthiya|pudhiya|putiya", "new"),
    (r"irudhi|iruthi|final", "final"), (r"pazhaya|pazhaiya|palaya|pazhaia", "existing"), (r"urimaiyalar|urimaiyaalar|sondhakkarar", "owner"),
    (r"parappalavu|parappu|paratpalavu", "area"), (r"ullpirivu|utpirivu|udpirivu", "subdivision"),
    (r"nila\s*vagai|nilavagai", "land type"), (r"nila\s*payanpaadu|nilam\s*payanpaadu", "land use"),
    (r"vivaram|vivarangal", "details"), (r"enna|enn", ""),
]

# ── vocabulary for typo correction (exact words always win) ──────────────────
_EXACT = {
    "temp": "temporary", "tmp": "temporary", "temporary": "temporary", "provisional": "temporary",
    "interim": "temporary",
    "no": "number", "nos": "number", "nmbr": "number", "numbr": "number", "nmber": "number",
    "nbr": "number", "nubmer": "number", "aria": "area", "arae": "area", "areaa": "area", "num": "number", "number": "number", "numbers": "number",
    "ares": "ares", "aares": "ares",
    "hectare": "hectare", "hectares": "hectare", "hectar": "hectare", "hector": "hectare",
    "hectors": "hectare", "hec": "hectare", "ha": "hectare", "hect": "hectare",
    "sqm": "sqm", "sqmtr": "sqm", "sqmt": "sqm",
    "old": "existing", "current": "existing", "existing": "existing",
    "assigned": "final", "final": "final", "fresh": "new",
    "usage": "use", "uses": "use", "utilisation": "use", "utilization": "use",
    "submission": "submit", "submitted": "submit", "submit": "submit", "filed": "submit",
    "detail": "details", "details": "details", "info": "details", "information": "details",
    "owner": "owner", "owners": "owner", "ownership": "owner",
    "entire": "complete", "full": "complete", "complete": "complete", "whole": "complete",
    "sy": "survey", "typ": "type", "tpe": "type", "typs": "type", "types": "type",
}
_VOCAB = ["temporary", "existing", "survey", "number", "patta", "details", "status", "land", "type",
          "area", "hectares", "hectare", "square", "meters", "metres", "tax", "rate", "plot", "soil",
          "primary", "secondary", "group", "submit", "submitted", "date", "owner", "name", "complete",
          "final", "subdivision", "application", "including", "available"]
_STOP = frozenset("""what is the a an for of on in to me tell give show please pls can you
which whose linked there any this that its it be are was were do does did and also with
about from by as at has have had get need want know see check let""".split())
_SPLIT = re.compile(r"\s*(?:,|;|&|\band\b|\bas\s+well\s+as\b|\balong\s+with\b|\bplus\b|\bincluding\b|"
                    r"\bwith\b|\balso\b)\s*", re.IGNORECASE)


def _canon(token: str) -> str:
    if token in _EXACT:
        return _EXACT[token]
    if not token.isascii() or not token.isalpha() or len(token) < 4:
        return token
    hits = {w for w in _VOCAB if is_token_typo_match(token, w)}
    # "hectors" is a typo of both "hectares" and "hectare"; they mean the same.
    hits = {"hectare" if h in ("hectares", "hectare") else "submit" if h in ("submit", "submitted")
            else "metre" if h in ("meters", "metres") else h for h in hits}
    return next(iter(hits)) if len(hits) == 1 else token


def normalise(message: str) -> str:
    """The message as canonical English tokens: Tamil / Tanglish cues translated,
    abbreviations expanded, spelling slips corrected, the application number and
    filler dropped."""
    t = _APP_NO.sub(" ", message or "").lower()
    t = t.replace("முகவரி", " address ")  # contains the cue "வரி" (tax)
    for ta, en in _TAMIL_CUES:
        t = t.replace(ta, en)
    for rx, en in _TANGLISH_CUES:
        t = re.sub(rx, f" {en} ", t)
    # "sub div", "sub-division", "subdivison", "sub divsion", "subdiv" -> one word
    t = re.sub(r"\bsub[\s\-_.]*d[a-z]*v[a-z]*", " subdivision ", t)
    # "square metres / meters / metters / sq.m / sqmtr" -> sqm (typos included)
    t = re.sub(r"\bsq[a-z]*\.?\s*(?:m[a-z]*|mtr[a-z]*)\b", " sqm ", t)
    t = re.sub(r"[?!.:()\"']", " ", t)
    out: List[str] = []
    for tok in re.findall(r"[a-z]+|[^\sa-z]+", t):
        out.append(_canon(tok))
    return re.sub(r"\s+", " ", " ".join(out)).strip()


# ── attribute detection ──────────────────────────────────────────────────────
TEMP_SUB, NEW_SUB, SUB_DETAILS = "temp_sub_div", "new_sub_div", "subdivision_details"
EXIST_PATTA, NEW_PATTA = "existing_patta", "new_patta"
SURVEY, STATUS, SUBMIT, OWNER, FULL = "survey", "status", "submit_date", "owner", "full_details"
AREA, AREA_HA, AREA_ARES, AREA_SQM = "area_all", "area_hec", "area_ares", "area_sqm"
LAND_TYPE, LAND_USE = "land_type", "land_use"
TAX_RATE, TAX_HECT = "tax_rate", "tax_hect"
PLOT_NO, PLOT_TYPE = "plot_no", "plot_type"
SOIL_P, SOIL_S = "soil_primary", "soil_secondary"
G_SURVEY, G_SUB, G_TEMP, G_PATTA = "group_survey", "group_sub_div", "group_temp_sub_div", "group_patta"

# A single one of these is already answered well by the general field handlers;
# a question that asks for only one of them is left to them.
_PLAIN = frozenset({SURVEY, STATUS, SUBMIT})


def _clause_attrs(toks: Sequence[str], whole: Sequence[str]) -> List[str]:
    s = set(toks)
    out: List[str] = []
    num = "number" in s

    if "group" in s:
        if "survey" in s:
            return [G_SURVEY]
        if "patta" in s:
            return [G_PATTA]
        if "subdivision" in s:
            return [G_TEMP] if "temporary" in s else [G_SUB]
        return [G_SURVEY, G_SUB, G_TEMP, G_PATTA]
    if "plot" in s:
        if "type" in s and not num:
            return [PLOT_TYPE]
        if num and "type" not in s:
            return [PLOT_NO]
        return [PLOT_NO, PLOT_TYPE]
    if "soil" in s or (("primary" in s or "secondary" in s) and "soil" in whole):
        if "primary" in s and "secondary" not in s:
            return [SOIL_P]
        if "secondary" in s and "primary" not in s:
            return [SOIL_S]
        return [SOIL_P, SOIL_S]
    if "tax" in s:
        return [TAX_HECT] if "hectare" in s else [TAX_RATE]
    if "patta" in s:
        if "new" in s:
            return [NEW_PATTA]
        out.append(EXIST_PATTA)
        if "survey" in s:
            out.insert(0, SURVEY)
        return out
    if "subdivision" in s:
        if "survey" in s:
            out.append(SURVEY)
        if "temporary" in s:
            out.append(TEMP_SUB)
        elif "new" in s or "final" in s:
            out.append(NEW_SUB)
        elif num:
            out += [TEMP_SUB, NEW_SUB]
        else:
            out.append(SUB_DETAILS)
        return out
    units = [u for u, key in (("hectare", AREA_HA), ("ares", AREA_ARES), ("sqm", AREA_SQM)) if u in s]
    if units and ("area" in s or "hectare" in s or "ares" in s or "sqm" in s):
        return [{"hectare": AREA_HA, "ares": AREA_ARES, "sqm": AREA_SQM}[u] for u in units]
    if "area" in s:
        return [AREA]
    if "land" in s:
        if "use" in s or ("details" in s and "type" not in s):
            return [LAND_USE]
        if "type" in s:
            return [LAND_TYPE]
        return [LAND_TYPE, LAND_USE]
    if "owner" in s:
        return [OWNER]
    if "survey" in s:
        return [SURVEY]
    if "submit" in s:
        out.append(SUBMIT)
        if "status" in s:
            out.append(STATUS)
        return out
    if "status" in s:
        return [STATUS]
    if "details" in s and "complete" in s:
        return [FULL]
    return out


def attributes(message: str) -> List[str]:
    """The fields a message asks for, in the order asked. [] when it asks for none."""
    text = normalise(message)
    if not text:
        return []
    whole = text.split()
    out: List[str] = []
    for clause in _SPLIT.split(text):
        toks = [w for w in clause.split() if w not in _STOP]
        for a in _clause_attrs(toks, whole):
            if a not in out:
                out.append(a)
    # "complete details ... including survey, sub-division, patta, area and land
    # use" names its parts; the bare "complete details" is the whole record.
    if "complete" in whole and "details" in whole and len(out) <= 1 and FULL not in out:
        out.append(FULL)
    if FULL in out and len(out) > 1:
        out.remove(FULL)
    # a status question that also names submission wants both facts
    return out


def claims(attrs: Sequence[str]) -> bool:
    """True when this module should answer rather than the general field handlers."""
    if not attrs:
        return False
    return len(attrs) > 1 or attrs[0] not in _PLAIN


# ── data ────────────────────────────────────────────────────────────────────
_PLOT_SQL = """
    SELECT temp_sub_div_no, new_sub_div_no, existing_patta_no, new_patta_no,
           area_hec, area_ares, area_sqmtr, survey_no, ward_code, block_code,
           land_type, land_use, descriptive_landuse, tax_rate, tax_hect,
           soil_typ_p, soil_typ_s, plot_type, plot_no, govt_pri,
           group_survey_no, group_sub_div_no, group_temp_sub_div_no, group_patta_no,
           submit_dt, update_dt
    FROM areg_temp_subdivclub WHERE appl_id = :app ORDER BY row_id"""

_OWNER_SQL = """
    SELECT asd.temporary_sub_division_no AS plot, o.name, o.name_tamil,
           o.relationship_type, o.relative_name, o.ownership_share
    FROM application_sub_division_owners o
    JOIN application_sub_divisions asd ON asd.id = o.application_sub_division_id
    JOIN applications a ON a.id = asd.application_id
    WHERE a.application_number = :app
    ORDER BY asd.temporary_sub_division_no, o.owner_no"""


async def _rows(db, sql: str, app: str) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    try:
        # A savepoint, so a missing table cannot poison the caller's transaction.
        async with db.begin_nested():
            res = await db.execute(text(sql), {"app": app})
            return [dict(r) for r in res.mappings().all()]
    except Exception as exc:  # table not present in this deployment
        logger.warning(f"subdivision_qa: could not read plot rows ({type(exc).__name__}); falling back")
        return []


async def load_plots(db, application_number: str) -> List[Dict[str, Any]]:
    return await _rows(db, _PLOT_SQL, application_number)


async def load_owners(db, application_number: str) -> List[Dict[str, Any]]:
    return await _rows(db, _OWNER_SQL, application_number)


# ── values ──────────────────────────────────────────────────────────────────
def _v(x: Any) -> Optional[str]:
    """A recorded value, or None for blank / '-' / NULL."""
    if x is None:
        return None
    s = str(x).strip()
    return None if s in ("", "-", "None", "null") else s


def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _num(x: float) -> str:
    return f"{x:,.2f}".rstrip("0").rstrip(".") if x != int(x) else f"{int(x):,}"


def total_sqm(p: Dict[str, Any]) -> float:
    """Hectare + ares + square metres -> square metres (1 ha = 10,000; 1 are = 100).
    The square-metre column holds only the remainder below one are."""
    return _f(p.get("area_hec")) * 10000 + _f(p.get("area_ares")) * 100 + _f(p.get("area_sqmtr"))


def _hec(x: float) -> str:
    return f"{x / 10000:.4f}".rstrip("0").rstrip(".")


# ── text ────────────────────────────────────────────────────────────────────
_NOT_REC = ("not recorded", "பதிவு செய்யப்படவில்லை")


def _t(ta: bool, en: str, tamil: str) -> str:
    return tamil if ta else en


def _plot_vals(plots, key, fmt=_v):
    return [(p.get("temp_sub_div_no") or "-", fmt(p.get(key))) for p in plots]


def _per_plot(plots, key, label: str, ta: bool, fmt=_v, none_en: str = None, none_ta: str = None) -> str:
    vals = _plot_vals(plots, key, fmt)
    got = [(pl, v) for pl, v in vals if v is not None]
    if not got:
        return (none_ta or f"{label}: {_NOT_REC[1]}.") if ta else (none_en or f"{label}: {_NOT_REC[0]}.")
    if len(plots) == 1 or len({v for _p, v in got}) == 1 and len(got) == len(plots):
        return f"{label}: {got[0][1]}"
    parts = [f"{pl}: {v if v is not None else _t(ta, _NOT_REC[0], _NOT_REC[1])}" for pl, v in vals]
    return f"{label}: " + "; ".join(parts)


def answer(attrs: Sequence[str], app: str, sd: Dict[str, Any], plots: List[Dict[str, Any]],
           owners: List[Dict[str, Any]], owner_rows: Optional[Dict[str, Any]], ta: bool) -> Optional[str]:
    """The answer for `attrs`, or None when there is nothing useful to say."""
    status = (sd.get("status") or sd.get("application_status") or "").lower()
    stage = sd.get("stage") or sd.get("workflow_state") or ""
    is_open = status in ("pending", "in_progress", "escalated")
    is_nisd = str(sd.get("type") or "").upper() == "NISD"
    temps = [p.get("temp_sub_div_no") for p in plots if p.get("temp_sub_div_no")] or [
        x.strip() for x in str(sd.get("temporary_subdivision_number") or "").split(",") if x.strip()]
    temps = sorted(dict.fromkeys(temps))

    if not plots and is_nisd and any(a in attrs for a in (
            TEMP_SUB, NEW_SUB, SUB_DETAILS, AREA, AREA_HA, AREA_ARES, AREA_SQM, PLOT_NO, PLOT_TYPE)):
        return _t(ta,
                  f"{app} is a NISD application, which does not split the parcel, so it carries no "
                  f"sub-division plots.",
                  f"{app} ஒரு NISD விண்ணப்பம்; நிலம் பிரிக்கப்படுவதில்லை, எனவே உட்பிரிவு மனைகள் எதுவும் இல்லை.")

    lines: List[str] = []
    for a in attrs:
        if a == SURVEY:
            lines.append(_t(ta, f"Survey number: {sd.get('survey_no') or sd.get('survey_number') or _NOT_REC[0]}"
                                f" (ward {sd.get('ward_number', '-')}, block {sd.get('block_number', '-')})",
                            f"சர்வே எண்: {sd.get('survey_no') or sd.get('survey_number') or _NOT_REC[1]}"
                            f" (வார்டு {sd.get('ward_number', '-')}, தொகுதி {sd.get('block_number', '-')})"))
        elif a == TEMP_SUB:
            lines.append((f"Temporary sub-division number{'s' if len(temps) != 1 else ''}: " + ", ".join(temps))
                         if temps and not ta else
                         (f"தற்காலிக உட்பிரிவு எண்: " + ", ".join(temps)) if temps else
                         _t(ta, "Temporary sub-division number: not recorded.", "தற்காலிக உட்பிரிவு எண்: பதிவு செய்யப்படவில்லை."))
        elif a == NEW_SUB:
            finals = [x for x in (_v(p.get("new_sub_div_no")) for p in plots) if x]
            if finals:
                lines.append(_t(ta, "New (final) sub-division number: " + ", ".join(finals),
                                "புதிய (இறுதி) உட்பிரிவு எண்: " + ", ".join(finals)))
            elif is_open:
                tn = ", ".join(temps) or "-"
                lines.append(_t(
                    ta,
                    f"No new (final) sub-division number has been assigned yet — the file is {status} "
                    f"(stage {stage}). It runs under the temporary number{'s' if len(temps) != 1 else ''} {tn} "
                    f"until it is approved.",
                    f"புதிய (இறுதி) உட்பிரிவு எண் இன்னும் வழங்கப்படவில்லை — விண்ணப்பம் {status} நிலையில் "
                    f"({stage}) உள்ளது. அங்கீகாரம் வரை தற்காலிக எண் {tn} பயன்படுத்தப்படும்."))
            else:
                lines.append(_t(ta, "New (final) sub-division number: not recorded.",
                                "புதிய (இறுதி) உட்பிரிவு எண்: பதிவு செய்யப்படவில்லை."))
        elif a == EXIST_PATTA:
            vals = _plot_vals(plots, "existing_patta_no")
            real = [(pl, v) for pl, v in vals if v]
            if real:
                shown = sorted({v for _p, v in real})
                lines.append(_t(ta, "Existing patta number: " + ", ".join(shown)
                                + (" (the parent parcel's patta; the other new plot(s) have none yet)"
                                   if len(real) < len(vals) else ""),
                                "தற்போதுள்ள பட்டா எண்: " + ", ".join(shown)))
            elif sd.get("patta_number"):
                lines.append(_t(ta, f"Existing patta number: {sd['patta_number']}",
                                f"தற்போதுள்ள பட்டா எண்: {sd['patta_number']}"))
            else:
                lines.append(_t(ta, "Existing patta number: not recorded.",
                                "தற்போதுள்ள பட்டா எண்: பதிவு செய்யப்படவில்லை."))
        elif a == NEW_PATTA:
            news = [x for x in (_v(p.get("new_patta_no")) for p in plots) if x] or (
                [sd["new_patta_number"]] if sd.get("new_patta_number") else [])
            if news:
                lines.append(_t(ta, "New patta number: " + ", ".join(news), "புதிய பட்டா எண்: " + ", ".join(news)))
            elif is_open:
                lines.append(_t(ta, f"No new patta number has been issued yet — the file is {status} "
                                    f"(stage {stage}); it is generated when the transfer order is approved.",
                                f"புதிய பட்டா எண் இன்னும் வழங்கப்படவில்லை — விண்ணப்பம் {status} நிலையில் "
                                f"({stage}) உள்ளது; ஆணை அங்கீகரிக்கப்படும்போது உருவாக்கப்படும்."))
            else:
                lines.append(_t(ta, "New patta number: not recorded.", "புதிய பட்டா எண்: பதிவு செய்யப்படவில்லை."))
        elif a in (AREA, AREA_HA, AREA_ARES, AREA_SQM):
            if not plots:
                lines.append(_t(ta, "Area: no sub-division area is recorded for this application.",
                                "பரப்பளவு: இந்த விண்ணப்பத்திற்கு உட்பிரிவு பரப்பளவு பதிவில் இல்லை."))
                continue
            rows = []
            for p in plots:
                tot = total_sqm(p)
                ha, ar, sq = _num(_f(p.get("area_hec"))), _num(_f(p.get("area_ares"))), _num(_f(p.get("area_sqmtr")))
                if a == AREA_HA:
                    rows.append(f"{p.get('temp_sub_div_no') or '-'}: {_hec(tot)} " + _t(ta, "ha in total", "ஹெக்டேர் (மொத்தம்)")
                                + f" (recorded hectare part: {ha})" if not ta else
                                f"{p.get('temp_sub_div_no') or '-'}: {_hec(tot)} ஹெக்டேர் (பதிவான ஹெக்டேர் பகுதி: {ha})")
                elif a == AREA_ARES:
                    rows.append(f"{p.get('temp_sub_div_no') or '-'}: {ar} " + _t(ta, "ares (recorded)", "ஏர்ஸ் (பதிவானது)"))
                elif a == AREA_SQM:
                    rows.append(f"{p.get('temp_sub_div_no') or '-'}: {_num(tot)} " + _t(ta, "sq.m in total", "ச.மீ (மொத்தம்)")
                                + _t(ta, f" ({ha} ha + {ar} ares + {sq} sq.m)", f" ({ha} ஹெ + {ar} ஏர்ஸ் + {sq} ச.மீ)"))
                else:
                    rows.append(_t(ta, f"{p.get('temp_sub_div_no') or '-'}: {ha} ha, {ar} ares, {sq} sq.m "
                                       f"= {_num(tot)} sq.m ({_hec(tot)} ha)",
                                   f"{p.get('temp_sub_div_no') or '-'}: {ha} ஹெ, {ar} ஏர்ஸ், {sq} ச.மீ "
                                   f"= {_num(tot)} ச.மீ ({_hec(tot)} ஹெ)"))
            grand = sum(total_sqm(p) for p in plots)
            head = {AREA_HA: _t(ta, "Area in hectares", "பரப்பளவு (ஹெக்டேர்)"),
                    AREA_ARES: _t(ta, "Area in ares", "பரப்பளவு (ஏர்ஸ்)"),
                    AREA_SQM: _t(ta, "Area in square metres", "பரப்பளவு (சதுர மீட்டர்)"),
                    AREA: _t(ta, "Area", "பரப்பளவு")}[a]
            tail = (_t(ta, f"Total across {len(plots)} plots: {_num(grand)} sq.m ({_hec(grand)} ha). "
                           f"Area is recorded as hectares + ares + sq.m (1 are = 100 sq.m).",
                       f"{len(plots)} மனைகளின் மொத்தம்: {_num(grand)} ச.மீ ({_hec(grand)} ஹெ).")
                    if len(plots) > 1 else "")
            lines.append(head + ":\n  " + "\n  ".join(rows) + (("\n  " + tail) if tail else ""))
        elif a == LAND_TYPE:
            parcel = sd.get("land_type")
            own = [x for x in (_v(p.get("land_type")) for p in plots) if x]
            use = _land_use_list(plots)
            bits = []
            if own:
                bits.append(", ".join(sorted(set(own))))
            elif parcel:
                bits.append(_t(ta, f"{parcel} (parcel register, survey {sd.get('survey_no', '-')})",
                               f"{parcel} (நிலப்பதிவேடு, சர்வே {sd.get('survey_no', '-')})"))
            lines.append(_t(ta, "Land type: " + (bits[0] if bits else "not recorded"),
                            "நில வகை: " + (bits[0] if bits else "பதிவு செய்யப்படவில்லை"))
                         + (_t(ta, f". The sub-division record's land use: {use}.",
                               f". உட்பிரிவு பதிவில் நில பயன்பாடு: {use}.") if use else ""))
        elif a == LAND_USE:
            use = _land_use_list(plots)
            lines.append(_t(ta, f"Land use: {use}", f"நில பயன்பாடு: {use}") if use else
                         _t(ta, "Land use: not recorded.", "நில பயன்பாடு: பதிவு செய்யப்படவில்லை."))
        elif a == TAX_RATE:
            lines.append(_per_plot(plots, "tax_rate", _t(ta, "Tax rate", "வரி விகிதம்"), ta))
        elif a == TAX_HECT:
            lines.append(_per_plot(plots, "tax_hect", _t(ta, "Tax per hectare", "ஹெக்டேருக்கு வரி"), ta))
        elif a == PLOT_NO:
            lines.append(_per_plot(plots, "plot_no", _t(ta, "Plot number", "மனை எண்"), ta))
        elif a == PLOT_TYPE:
            lines.append(_per_plot(plots, "plot_type", _t(ta, "Plot type", "மனை வகை"), ta))
        elif a == SOIL_P:
            lines.append(_per_plot(plots, "soil_typ_p", _t(ta, "Primary soil type (code)", "முதன்மை மண் வகை (குறியீடு)"), ta))
        elif a == SOIL_S:
            lines.append(_per_plot(plots, "soil_typ_s", _t(ta, "Secondary soil type (code)", "இரண்டாம் நிலை மண் வகை (குறியீடு)"), ta))
        elif a == G_SURVEY:
            lines.append(_group_line(plots, "group_survey_no", _t(ta, "Group survey number", "குழு சர்வே எண்"), ta))
        elif a == G_SUB:
            lines.append(_group_line(plots, "group_sub_div_no", _t(ta, "Group sub-division number", "குழு உட்பிரிவு எண்"), ta))
        elif a == G_TEMP:
            lines.append(_group_line(plots, "group_temp_sub_div_no", _t(ta, "Group temporary sub-division number", "குழு தற்காலிக உட்பிரிவு எண்"), ta))
        elif a == G_PATTA:
            lines.append(_group_line(plots, "group_patta_no", _t(ta, "Group patta number", "குழு பட்டா எண்"), ta))
        elif a == SUBMIT:
            d = sd.get("submission_date") or sd.get("application_date")
            lines.append(_t(ta, f"Submitted on: {d}" if d else "Submission date: not recorded.",
                            f"சமர்ப்பிக்கப்பட்ட தேதி: {d}" if d else "சமர்ப்பிப்பு தேதி: பதிவு செய்யப்படவில்லை."))
        elif a == STATUS:
            lines.append(_t(ta, f"Current status: {status or 'not recorded'}" + (f" (stage {stage})" if stage else ""),
                            f"தற்போதைய நிலை: {status or 'பதிவு இல்லை'}" + (f" (கட்டம் {stage})" if stage else "")))
        elif a == OWNER:
            lines.append(_owner_text(sd, owners, owner_rows, ta))
        elif a == SUB_DETAILS or a == FULL:
            lines.append(_details_text(sd, plots, status, stage, ta, full=(a == FULL)))
    lines = [ln for ln in lines if ln]
    if not lines:
        return None
    if len(lines) == 1 and "\n" not in lines[0]:
        return f"{lines[0]}  [{app}]"
    head = _t(ta, f"For application {app}:", f"விண்ணப்பம் {app}:")
    return head + "\n" + "\n".join(f"• {ln}" for ln in lines)


def _land_use_list(plots) -> str:
    out = []
    for p in plots:
        code, desc = _v(p.get("land_use")), _v(p.get("descriptive_landuse"))
        if code or desc:
            out.append(f"{p.get('temp_sub_div_no') or '-'}: " + (f"code {code}" if code else "")
                       + (f" — {desc}" if desc and code else (desc or "")))
    return "; ".join(out)


def _group_line(plots, key: str, label: str, ta: bool) -> str:
    vals = [x for x in (_v(p.get(key)) for p in plots) if x]
    if vals:
        return f"{label}: " + ", ".join(sorted(set(vals)))
    return _t(ta, f"{label}: not recorded — this sub-division is not part of a grouped (clubbed) split.",
              f"{label}: பதிவு செய்யப்படவில்லை — இந்த உட்பிரிவு குழு பிரிவின் பகுதி அல்ல.")


def _owner_text(sd, owners, owner_rows, ta: bool) -> str:
    parts: List[str] = []
    record = (owner_rows or {}).get("owners") or []
    if record:
        names = []
        for o in record:
            n = (o.get("name") or "").strip()
            sh = o.get("ownership_share")
            if n and n not in [x[0] for x in names]:
                names.append((n, sh))
        if names:
            parts.append(_t(ta, f"Owner of record (survey {sd.get('survey_no', '-')}): ",
                            f"பதிவேட்டில் உரிமையாளர் (சர்வே {sd.get('survey_no', '-')}): ")
                         + ", ".join(f"{n}" + (f" ({_num(_f(sh))}%)" if sh not in (None, "") else "") for n, sh in names))
    if owners:
        prop = []
        for o in owners:
            who = (o.get("name_tamil") or o.get("name") or "").strip()
            rel = f" ({o.get('relationship_type')} {o.get('relative_name')})" if o.get("relationship_type") and o.get("relative_name") else ""
            prop.append(f"{o.get('plot') or '-'} → {who}{rel}")
        parts.append(_t(ta, "Proposed owner(s) after the sub-division: ", "உட்பிரிவுக்குப் பின் முன்மொழியப்பட்ட உரிமையாளர்: ")
                     + "; ".join(prop))
    if sd.get("applicant_name"):
        parts.append(_t(ta, f"Applicant: {sd['applicant_name']}", f"விண்ணப்பதாரர்: {sd['applicant_name']}"))
    if not parts:
        return _t(ta, "No owner name is recorded for this application.", "இந்த விண்ணப்பத்திற்கு உரிமையாளர் பெயர் பதிவில் இல்லை.")
    return "\n  ".join(parts)


def _details_text(sd, plots, status, stage, ta: bool, full: bool) -> str:
    out = []
    if full:
        out.append(_t(ta, f"Survey {sd.get('survey_no', '-')}, ward {sd.get('ward_number', '-')}, block "
                          f"{sd.get('block_number', '-')}; {sd.get('type', '')} application, {status} (stage {stage}); "
                          f"submitted {sd.get('submission_date', '-')}.",
                      f"சர்வே {sd.get('survey_no', '-')}, வார்டு {sd.get('ward_number', '-')}, தொகுதி "
                      f"{sd.get('block_number', '-')}; {sd.get('type', '')} விண்ணப்பம், {status} ({stage}); "
                      f"சமர்ப்பிப்பு {sd.get('submission_date', '-')}."))
    if not plots:
        out.append(_t(ta, "No sub-division plots are recorded.", "உட்பிரிவு மனைகள் பதிவில் இல்லை."))
        return "\n  ".join(out)
    for p in plots:
        tot = total_sqm(p)
        bits = [
            _t(ta, f"temporary no. {p.get('temp_sub_div_no') or '-'}", f"தற்காலிக எண் {p.get('temp_sub_div_no') or '-'}"),
            _t(ta, f"new no. {_v(p.get('new_sub_div_no')) or 'not assigned yet'}",
               f"புதிய எண் {_v(p.get('new_sub_div_no')) or 'இன்னும் இல்லை'}"),
            _t(ta, f"existing patta {_v(p.get('existing_patta_no')) or 'none (new plot)'}",
               f"தற்போதைய பட்டா {_v(p.get('existing_patta_no')) or 'இல்லை (புதிய மனை)'}"),
            _t(ta, f"new patta {_v(p.get('new_patta_no')) or 'not issued yet'}",
               f"புதிய பட்டா {_v(p.get('new_patta_no')) or 'இன்னும் இல்லை'}"),
            _t(ta, f"area {_num(_f(p.get('area_hec')))} ha {_num(_f(p.get('area_ares')))} ares "
                   f"{_num(_f(p.get('area_sqmtr')))} sq.m = {_num(tot)} sq.m",
               f"பரப்பளவு {_num(_f(p.get('area_hec')))} ஹெ {_num(_f(p.get('area_ares')))} ஏர்ஸ் "
               f"{_num(_f(p.get('area_sqmtr')))} ச.மீ = {_num(tot)} ச.மீ"),
        ]
        lu = _land_use_list([p])
        if lu:
            bits.append(_t(ta, f"land use {lu.split(': ', 1)[-1]}", f"நில பயன்பாடு {lu.split(': ', 1)[-1]}"))
        out.append(", ".join(bits))
    if len(plots) > 1:
        grand = sum(total_sqm(p) for p in plots)
        out.append(_t(ta, f"{len(plots)} plots, {_num(grand)} sq.m in total.", f"{len(plots)} மனைகள், மொத்தம் {_num(grand)} ச.மீ."))
    return "\n  ".join(out)
