"""
Citizen identifiers used by the sis_chatbot_db build: Aadhaar and CAN.

Both layers of the database go through here, so the rules live in one place:

* **Aadhaar** -- the extracts arrive with `aadhaar_number` stripped, so a
  UIDAI-shaped number is derived for each person: 12 digits, never starting
  with 0 or 1, with a valid Verhoeff check digit. It is keyed on the person's
  name, so the same person carries the same number in every extract they
  appear in and a re-run reproduces the database exactly.

* **CAN** -- the Citizen Access Number is present in the extracts but not
  always well formed. The channel that issued it decides its shape:

  | channel | issued by | digits | shape in the extracts |
  |---------|-----------|--------|-----------------------|
  | CSC     | Common Service Centre / e-Sevai operator | 15 | `133280122203291` |
  | citizen | the citizen on the TN portal             | 12 | `202329380999`    |

  The `source_name` column of `urban_application_log` records which one it
  was: a placeholder (`-`) means the citizen filed it themselves, an operator
  or VLE code (`tut_tct_t131_02`, `TNEFATUT0540-01`, ...) means a CSC did.
  In the extracts the two signals agree on every well-formed row -- all 108
  twelve-digit CANs carry `-` and all 151 fifteen-digit ones carry an operator
  code -- so `source_name` is used to decide the channel and the length rule
  is then enforced against it.
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

CAN_LENGTH = {"CSC": 15, "citizen": 12}

# Every CSC-issued CAN in the extracts starts with this series code.
_CSC_SERIES = "133"

# source_name values that mean "no operator handled this" -- the citizen filed
# it on the portal.
_NO_OPERATOR = {"", "-", "--", "na", "n/a", "null"}


def can_channel(source_name: str | None) -> str:
    """Which channel submitted the application, per `source_name`."""
    return "citizen" if (source_name or "").strip().lower() in _NO_OPERATOR else "CSC"


def normalize_can(raw: str | None, channel: str) -> str | None:
    """Return the CAN in the length its channel mandates, or None.

    Well-formed values pass through untouched. The extracts also carry a few
    damaged ones, and each is repaired only where the repair is unambiguous:

    * a CSC number one or two digits short (`13328018014908`) -- the series
      code is intact and the tail lost its leading zeros, so it is re-padded;
    * a value carrying a prefix (`ESVU202407000005329`) -- the CAN is the
      digits;
    * anything else, such as a mobile number typed into the field
      (`9894689631`), is not a CAN and becomes NULL rather than a guess.
    """
    want = CAN_LENGTH.get(channel)
    if want is None:
        return None
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if len(digits) == want:
        return digits
    if channel == "CSC":
        if 12 < len(digits) < 15 and digits.startswith(_CSC_SERIES):
            # 13328|018014908 -> 13328|0018014908
            return digits[:5] + digits[5:].rjust(10, "0")
        if len(digits) > 15:
            return digits[-15:]
    return None


def can_valid(number: str | None, channel: str | None) -> bool:
    """True when `number` is all digits and the right length for `channel`."""
    n = (number or "").strip()
    want = CAN_LENGTH.get(channel or "")
    return bool(want) and len(n) == want and n.isdigit()
