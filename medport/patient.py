"""Is this report about the person whose portfolio this is?

Reads the patient line printed on a report (name, age, sex, date of birth) and compares it with
the profile. Names are matched loosely: titles ("Mr.", "Smt."), notes in brackets, initials
("A. Verma") and small spelling slips are tolerated, so only a clearly different person is flagged.
"""
import re
from datetime import date

TITLES = {"mr", "mrs", "ms", "miss", "mx", "dr", "prof", "master", "baby", "smt", "shri", "sri", "shrimati",
          "kum", "kumari", "md", "late", "self"}

_STOP = r"(?=\s{2,}|\s*\||\s+(?:age|sex|gender|uhid|mrn|id|reg|lab\s*no|dob|date|ref|ip|op|bed)\b|$)"
_NAME = re.compile(r"^\s*(?:patient(?:'s)?\s*name|pt\.?\s*name|name\s+of\s+(?:the\s+)?patient|patient|name)"
                   r"\s*[:\-]\s*(?P<name>[A-Za-z][A-Za-z .'()]*?)" + _STOP, re.I | re.M)
_AGE_SEX = re.compile(r"age\s*/\s*(?:sex|gender)\s*[:\-]?\s*(?P<age>\d{1,3})\s*(?:y|yr|yrs|years?)?\.?\s*/\s*(?P<sex>male|female|m|f)\b", re.I)
_AGE = re.compile(r"\bage\s*[:\-]\s*(?P<age>\d{1,3})\s*(?:y|yr|yrs|years?)?\b", re.I)
_SEX = re.compile(r"\b(?:sex|gender)\s*[:\-]\s*(?P<sex>male|female|m|f)\b", re.I)
_DOB = re.compile(r"\b(?:dob|d\.o\.b\.?|date\s+of\s+birth)\s*[:\-]\s*(?P<dob>[0-9A-Za-z/.\- ]{6,20}?)" + _STOP, re.I | re.M)


def _sex(s):
    s = (s or "").strip().lower()
    return "F" if s.startswith("f") else "M" if s.startswith("m") else None


def find_patient(text: str) -> dict:
    """The patient details printed on the report, or None for each one that isn't there."""
    from .extract import parse_date  # avoid a circular import at load time
    out = {"name": None, "age": None, "sex": None, "dob": None}
    m = _NAME.search(text)
    if m and tokens(m.group("name")):
        out["name"] = re.sub(r"\s+", " ", m.group("name")).strip(" .")
    m = _AGE_SEX.search(text)
    if m:
        out["age"], out["sex"] = int(m.group("age")), _sex(m.group("sex"))
    else:
        a, s = _AGE.search(text), _SEX.search(text)
        out["age"] = int(a.group("age")) if a else None
        out["sex"] = _sex(s.group("sex")) if s else None
    m = _DOB.search(text)
    if m:
        out["dob"] = parse_date(m.group("dob"))
    return out


def tokens(name: str) -> list[str]:
    name = re.sub(r"\([^)]*\)", " ", name or "")
    return [t for t in re.findall(r"[a-z]+", name.lower()) if t not in TITLES]


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) == 1 or len(b) == 1:  # an initial
        return a[0] == b[0]
    # spelling slips in vowels only ("Verma" / "Varma"); a different consonant is a different name,
    # so "Ramesh" / "Rakesh" and "Asha" / "Usha" are not treated as the same person
    return a[0] == b[0] and _skeleton(a) == _skeleton(b)


def _skeleton(w: str) -> str:
    w = w.replace("x", "ks").replace("ph", "f").replace("th", "t").replace("sh", "s")
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[aeiouyh]", "", w))


def names_match(a: str, b: str) -> bool | None:
    """None when either name is missing."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return None
    short, long = sorted((ta, tb), key=len)
    if not all(any(_same(x, y) for y in long) for x in short):
        return False
    return any(len(x) > 1 and len(y) > 1 and _same(x, y) for x in ta for y in tb)  # not initials alone


def _age_on(dob: str, when: str) -> int | None:
    try:
        b, d = date.fromisoformat(dob), date.fromisoformat(when)
    except (TypeError, ValueError):
        return None
    return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def check(found: dict, profile: dict, report_date: str | None) -> tuple[str, list[str]]:
    """Returns (status, reasons). status: "match", "mismatch", or "unknown" (nothing to compare)."""
    me = (profile.get("name") or "").strip()
    reasons = []
    same_name = names_match(found.get("name"), me)
    if same_name is False:
        reasons.append(f"the name on the report is {found['name']}, but your profile says {me}")
    my_sex = _sex(profile.get("sex"))
    if found.get("sex") and my_sex and found["sex"] != my_sex:
        word = {"F": "female", "M": "male"}
        reasons.append(f"the report says {word[found['sex']]}, your profile says {word[my_sex]}")
    my_dob = profile.get("dob")
    if found.get("dob") and my_dob:
        if found["dob"] != my_dob:
            reasons.append(f"the date of birth on the report is {found['dob']}, yours is {my_dob}")
    elif found.get("age") is not None and my_dob and report_date:
        expected = _age_on(my_dob, report_date)
        if expected is not None and abs(expected - found["age"]) > 1:
            reasons.append(f"the report gives age {found['age']}, but you would have been {expected}")
    if reasons:
        return "mismatch", reasons
    if same_name:
        return "match", []
    return "unknown", []
