"""
Citizen identifiers used by the sis_chatbot_db build: Aadhaar and CAN.

Both layers of the database go through here, so the rules live in one place:

* **Aadhaar** -- the extracts arrive with `aadhaar_number` stripped, so a
  UIDAI-shaped number is derived for each person: 12 digits, never starting
  with 0 or 1, with a valid Verhoeff check digit. It is keyed on the person's
  name, so the same person carries the same number in every extract they
  appear in and a re-run reproduces the database exactly.

* **CAN** -- the Citizen Access Number is present in the extracts but not
  always well formed. Its length names the counter that issued it:

  | digits | issued by | shape in the extracts |
  |--------|-----------|-----------------------|
  | 15 | a Common Service Centre / e-Sevai counter | `133280122203291` |
  | 12 | the citizen on the TN portal              | `202329380999`    |

  The length does **not** decide the submission channel. That comes from two
  other columns of `urban_application_log`:

  | channel       | `source_name`          | `camp_flag`   |
  |---------------|------------------------|---------------|
  | sub_registrar | `-` (nobody keyed it)  | --            |
  | citizen       | a bare mobile number   | `P` (camp)    |
  | CSC           | an operator / VLE code | anything else |

  A placeholder `source_name` means no operator account touched the file: it
  came in unattended from the Sub-Registrar, where IGRS raised the mutation off
  the registered deed. On an attended row `camp_flag = 'P'` marks a special
  revenue camp, where the file is keyed in for the citizen present, so the
  submission is the citizen's own; every other attended row is a CSC counter.

  The citizen route rests on two signals that agree everywhere: the `P` flag,
  and a `source_name` holding ten bare digits (the citizen's mobile) instead of
  a counter code. Either is enough -- see `_looks_self_filed`.

  `CAN_LENGTHS` then bounds what each channel may carry -- `CSC` 15,
  `sub_registrar` 12, `citizen` either, because a camp file can carry the
  number from whichever counter issued it.

  In the extracts the 108 rows with `-` are the same 108 that carry a
  twelve-digit CAN, and each also carries an `igrs_form6_number` equal to that
  CAN -- the deed the application is built on. All 151 fifteen-digit CANs carry
  an operator code.
"""
from __future__ import annotations

import hashlib

# --- Aadhaar --------------------------------------------------------------

_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_check(digits: str) -> int:
    """Verhoeff checksum of `digits`; 0 means the string validates."""
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c


def aadhaar_valid(number: str | None) -> bool:
    """True for a 12-digit UIDAI-shaped number with a good check digit."""
    n = (number or "").strip()
    return (len(n) == 12 and n.isdigit() and n[0] not in "01"
            and verhoeff_check(n) == 0)


def aadhaar_for(identity: str) -> str:
    """Deterministic, checksum-valid 12-digit Aadhaar for a person."""
    h = hashlib.sha256(("aadhaar:" + identity).encode("utf-8")).digest()
    n = int.from_bytes(h[:8], "big")
    first = 2 + (n % 8)                      # 2..9, never 0 or 1
    base = f"{first}{n % 10**10:010d}"       # 11 digits
    return base + str(_INV[verhoeff_check(base + "0")])


# --- CAN ------------------------------------------------------------------

# What lengths a CAN may have on each channel. The length identifies the
# counter that issued the number, not the channel that filed the application:
# an e-Sevai counter issues 15 digits (the `133` series), the TN portal issues
# 12. A camp file is keyed in by an operator on the citizen's behalf, so it can
# carry either.
CAN_LENGTHS = {"CSC": (15,), "citizen": (12, 15), "sub_registrar": (12,)}

# Every CSC-issued CAN in the extracts starts with this series code.
_CSC_SERIES = "133"

# source_name values that mean "no operator handled this" -- the file arrived
# unattended, from the Sub-Registrar's IGRS referral.
_NO_OPERATOR = {"", "-", "--", "na", "n/a", "null"}

# camp_flag values that mark a special revenue camp. Kept for the record and
# for `submission_camp_flag`, but NO LONGER ENOUGH ON ITS OWN to make a row a
# citizen submission -- see can_channel().
#
# camp_flag is weak evidence: the extracts also carry `S` (12 rows), `U` (1)
# and `Y` (2), and every one of those sits on an ordinary counter row -- a
# `TNEFATUT...` VLE code, a public ISP address, a 133-series CAN --
# indistinguishable from the blank-flag CSC rows beside it. `P` was trusted
# only because a second, independent signal was believed to agree with it
# everywhere. In the current extracts it does not: `2022/0153/28/001405`
# carries `P` on `tut_tct_t131_01`, a counter account that files nine other
# rows with no flag at all, with a 133-series CAN and the same public IP that
# `tut_tct_t131_02` (an unambiguous CSC counter, 82 rows) uses. Three signals
# say counter, one says camp.
_CAMP_FLAGS = {"P"}


def _looks_self_filed(source_name: str | None) -> bool:
    """True when `source_name` is a citizen's mobile number, not a counter code.

    Every attended row in the extracts names the account that keyed the file
    in, and those are operator / VLE codes: `tut_tct_t131_02`,
    `TNEFATUT0540-01`, `O961`, `TNTUTVLE_1807`. Four rows break the pattern and
    carry ten bare digits instead -- a mobile number, i.e. the citizen's own
    identifier rather than a counter's. Those four rows are also the only ones
    whose CAN is not a counter number (`ESVU202407000005329`, or the mobile
    repeated), and they carry `camp_flag = 'P'`.

    This is now the SOLE citizen signal. The reverse implication does not hold:
    `camp_flag = 'P'` also appears on counter-coded rows, where it means the
    file was taken at a camp desk run by that counter, not that the citizen
    filed it themselves.
    """
    value = (source_name or "").strip()
    return value.isdigit() and 9 <= len(value) <= 12


def can_channel(source_name: str | None, camp_flag: str | None = None) -> str:
    """Which channel submitted the application.

    Two columns of `urban_application_log`:

    * `source_name` -- a placeholder (`-`) means no operator account touched
      the file: it came in unattended from the Sub-Registrar, where IGRS raised
      the mutation off the registered deed. An operator / VLE code
      (`tut_tct_t131_02`, `TNEFATUT0540-01`, ...) means someone keyed it in at
      a counter.
    * `camp_flag` -- `P` marks a file taken at a special revenue camp. It is
      recorded (`submission_camp_flag`) but does not decide the channel: a camp
      desk is still run by a counter, and the flag appears on counter-coded
      rows.

    Anything else keyed in by an operator is a CSC / e-Sevai submission.

    `camp_flag` is deliberately still a parameter. Dropping it would silently
    change every call site's meaning; keeping it documents that the column was
    considered and is not the deciding signal.
    """
    if (source_name or "").strip().lower() in _NO_OPERATOR:
        return "sub_registrar"
    # The shape of source_name is the whole rule. It used to be OR-ed with
    # camp_flag == 'P', on the stated ground that the two signals named the
    # same rows; in the current extracts they do not, and where they disagree
    # the camp flag is the one contradicted by everything else on the row (see
    # _CAMP_FLAGS). A bare mobile in the operator column cannot be anything but
    # the citizen's own identifier, so that is what is trusted.
    #
    # This is the reading the built database already holds: re-deriving every
    # row under this rule reproduces `applications.submission_channel` exactly,
    # while the OR rule disagreed with the stored value on 2022/0153/28/001405.
    if _looks_self_filed(source_name):
        return "citizen"
    return "CSC"


def normalize_can(raw: str | None, channel: str) -> str | None:
    """Return the CAN in a length its channel admits, or None.

    Well-formed values pass through untouched. The extracts also carry a few
    damaged ones, and each is repaired only where the repair is unambiguous:

    * a CSC number one or two digits short (`13328018014908`) -- the series
      code is intact and the tail lost its leading zeros, so it is re-padded;
    * a value carrying a prefix (`ESVU202407000005329`) -- the CAN is the
      digits;
    * anything else, such as a mobile number typed into the field
      (`9894689631`), is not a CAN and becomes NULL rather than a guess.
    """
    want = CAN_LENGTHS.get(channel)
    if not want:
        return None
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if len(digits) in want:
        return digits
    if 15 in want:
        if 12 < len(digits) < 15 and digits.startswith(_CSC_SERIES):
            # 13328|018014908 -> 13328|0018014908
            return digits[:5] + digits[5:].rjust(10, "0")
        if len(digits) > 15:
            return digits[-15:]
    return None


def can_valid(number: str | None, channel: str | None) -> bool:
    """True when `number` is all digits and a length `channel` admits."""
    n = (number or "").strip()
    want = CAN_LENGTHS.get(channel or "")
    return bool(want) and len(n) in want and n.isdigit()
