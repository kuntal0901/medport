"""Turn an uploaded report into rows: pages of text, lab observations, and items
(conditions, medications, allergies).

Two engines:
- "llm": a local open model via Ollama reads each page into JSON.
- "rules": a regex parser for common lab-report layouts, used when no model is
  running. It only keeps tests it recognises, so it misses things but rarely
  invents them.
Every value the model returns is checked against the page text, and dropped if
it isn't printed there.
"""
import io
import os
import re
import shutil
import time
from datetime import date, datetime

from . import llm, patient
from .store import get_profile
from .normalize import canonical, flag_for, parse_number, parse_range

DATE_ORDER = os.environ.get("MEDPORT_DATE_ORDER", "DMY").upper()  # DMY (India/UK) or MDY (US)

# ---------------------------------------------------------------- text + OCR


def _layout_text(page) -> str:
    """Rebuild visual rows from word positions so table rows stay on one line."""
    words = page.get_text("words")  # x0, y0, x1, y1, word, block, line, wno
    if not words:
        return ""
    words.sort(key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines, current, cy = [], [], None
    for w in words:
        mid = (w[1] + w[3]) / 2
        if cy is not None and abs(mid - cy) > 3:
            lines.append(current)
            current = []
        current.append(w)
        cy = mid if len(current) == 1 else cy
    if current:
        lines.append(current)
    out, prev_y = [], None
    for ln in lines:
        ln.sort(key=lambda w: w[0])
        y, hgt = ln[0][1], ln[0][3] - ln[0][1]
        if prev_y is not None and y - prev_y > 1.9 * hgt:
            out.append("")  # visual gap: keep paragraphs apart
        prev_y = y
        parts, prev_x1 = [], None
        for w in ln:
            if prev_x1 is not None:
                parts.append("  " if w[0] - prev_x1 > 8 else " ")
            parts.append(w[4])
            prev_x1 = w[2]
        out.append("".join(parts))
    return "\n".join(out)


def ocr_image(png: bytes) -> tuple[str, str]:
    """Returns (text, engine). Prefers the local vision model, then Tesseract."""
    st = llm.status()
    if st["running"] and st["has_vision"]:
        return llm.transcribe_image(png), "vision:" + llm.VISION_MODEL
    if shutil.which("tesseract"):
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(io.BytesIO(png)), config="--psm 6"), "tesseract"
        except ImportError:
            pass
    return "", "none"


def read_pages(path: str, mime: str) -> tuple[list[dict], list[str]]:
    """Returns ([{page, text, ocr}], notes)."""
    import pymupdf as fitz

    notes, pages = [], []
    if mime == "application/pdf":
        doc = fitz.open(path)
        for i, page in enumerate(doc, start=1):
            text = _layout_text(page)
            if len(text.strip()) >= 30:
                pages.append({"page": i, "text": text, "ocr": 0})
                continue
            png = page.get_pixmap(dpi=200).tobytes("png")
            text, engine = ocr_image(png)
            if engine == "none":
                notes.append(f"Page {i} is a scan and no OCR is available (install a vision model or Tesseract).")
            pages.append({"page": i, "text": text, "ocr": 1})
    else:
        with open(path, "rb") as f:
            raw = f.read()
        img = fitz.open(stream=raw, filetype=mime.split("/")[-1])
        png = img[0].get_pixmap(dpi=200).tobytes("png") if mime != "image/png" else raw
        text, engine = ocr_image(png)
        if engine == "none":
            notes.append("This is a photo and no OCR is available (install a vision model or Tesseract).")
        pages.append({"page": 1, "text": text, "ocr": 1})
    return pages, notes


# ---------------------------------------------------------------- dates

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _mkdate(y, m, d) -> str | None:
    try:
        y = int(y)
        if y < 100:
            y += 2000
        return date(y, int(m), int(d)).isoformat()
    except (ValueError, TypeError):
        return None


def parse_date(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _mkdate(*m.groups())
    m = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
    if m:
        a, b, y = m.groups()
        d, mo = (a, b) if DATE_ORDER == "DMY" else (b, a)
        if int(mo) > 12 >= int(d):
            d, mo = mo, d
        return _mkdate(y, mo, d)
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-/]*([A-Za-z]{3})[A-Za-z]*[\s\-/,]*(\d{2,4})", s)
    if m and m.group(2).lower() in MONTHS:
        return _mkdate(m.group(3), MONTHS[m.group(2).lower()], m.group(1))
    m = re.search(r"([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        return _mkdate(m.group(3), MONTHS[m.group(1).lower()], m.group(2))
    return None


_DATE_HINT = re.compile(r"(collect|sample|report|date|registered|received|admi|discharg|visit)", re.I)


def find_report_date(text: str) -> str | None:
    """Prefer a date on a line that says collected/reported/date; else the first date."""
    first = None
    for line in text.splitlines():
        # "Admission: 02/02  Discharge: 06/02" -> the last date on the line wins
        found = [d for d in (parse_date(part) for part in re.split(r"\s{2,}", line)) if d]
        d = found[-1] if found else None
        if not d:
            continue
        if _DATE_HINT.search(line) and not re.search(r"birth|dob", line, re.I):
            return d
        if not re.search(r"birth|dob", line, re.I):
            first = first or d
    return first


# ---------------------------------------------------------------- rules engine

_LAB_LINE = re.compile(
    r"^\s*(?P<test>[A-Za-z][A-Za-z0-9 ()/%,.\-+']*?)\s*[:]?\s+"
    r"(?P<pre>\b(?:H|L|High|Low)\b\s+)?"
    r"(?P<value>[<>]?\s?\d[\d,]*(?:\.\d+)?)"
    r"(?:\s+(?P<post>\b(?:H|L|High|Low)\b))?"
    r"(?:\s+(?P<unit>(?!\d)[^\s]+(?:\s?/\s?[^\s]+)?))?"
    r"(?:\s+(?P<range>.*\d.*))?$"
)


def rule_labs(text: str) -> list[dict]:
    lines = text.splitlines()
    out, used = [], set()
    for i, line in enumerate(lines):
        m = _LAB_LINE.match(line.strip())
        if not m:
            continue
        test = m.group("test").strip(" :-")
        name, _, _ = canonical(test)
        if not name:
            continue
        unit = m.group("unit")
        rng = m.group("range")
        # a unit-less row like "TSH 2.1 0.4 - 4.0": the "unit" grabbed the range start
        if unit and re.match(r"^[<>≤≥]?\d", unit):
            rng, unit = f"{unit} {rng or ''}".strip(), None
        out.append({"test": test, "value": m.group("value").replace(" ", ""), "unit": unit, "reference_range": rng})
        used.add(i)
    return out + _split_row_labs(lines, used)


_RANGE_CHUNK = re.compile(r"^[<>≤≥]=?\s*\d[\d.]*$|^\d[\d.]*\s*(?:-|–|to)\s*\d[\d.]*$|^(?:up\s*to|upto)\s*\d[\d.]*$", re.I)
_FLAG_CHUNK = re.compile(r"^(?:H|L|HH|LL|High|Low|Normal|Abnormal|Critical|\*)$", re.I)


def _value_line(line: str) -> dict | None:
    """A row that starts with the result, its name printed on the line above:
    "15.90  ng/mL", "0.80  mg/dL  0.1 - 1.2  Normal", "1.37  0.8 - 2"."""
    m = re.match(r"^\s*(?P<value>[<>]?\d[\d,]*(?:\.\d+)?)(?=\s|$)(?P<rest>.*)$", line)
    if not m:
        return None
    unit = rng = None
    for c in _chunks(m.group("rest")):
        if _RANGE_CHUNK.match(c) and rng is None:
            rng = c
        elif _FLAG_CHUNK.match(c):
            continue
        elif unit is None and rng is None and not re.match(r"^\d", c) and len(c) <= 20:
            unit = c
        else:
            return None  # something else (a date line, an address): not a result row
    return {"value": m.group("value"), "unit": unit, "reference_range": rng}
# A labelled band of a multi-line reference interval: "Sufficient: 30-100", "Deficient:<20"
_BAND = re.compile(r"^[A-Za-z][A-Za-z ()]*:\s*[<>≤≥]?\s*\d[\d.]*(?:\s*-\s*\d[\d.]*)?$")


def _chunks(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]


def _split_row_labs(lines: list[str], used: set[int]) -> list[dict]:
    """Tables whose cells wrap: the test name, its value and a multi-line reference
    interval land on different text lines, e.g.

        Deficient:<20
        TOTAL VITAMIN D  Insufficient: 20-30
        15.90  ng/mL
        (Method:CLIA)  Sufficient: 30-100

    Pair each known test name that has no value with the next value-only line,
    and collect the labelled bands printed around them as the reference range.
    """
    out = []
    taken = set(used)
    for i, line in enumerate(lines):
        if i in taken:
            continue
        chunks = _chunks(line)
        if not chunks:
            continue
        test = chunks[0].strip(" :-")
        name, _, _ = canonical(test)
        if not name or re.search(r"\d", test):
            continue
        # look a few lines down for the value; give up at the next test name or a blank line
        vi = None
        for j in range(i + 1, min(i + 5, len(lines))):
            if not lines[j].strip():
                break
            if _value_line(lines[j]) and j not in taken:
                vi = j
                break
            first = _chunks(lines[j])[0].strip(" :-")
            if canonical(first)[0] and not re.search(r"\d", first):
                break  # "VITAMIN D TOTAL" panel header followed by the real "TOTAL VITAMIN D" row
        if vi is None:
            continue
        val = _value_line(lines[vi])
        bands = []
        lo, hi = i, vi
        while lo - 1 >= 0 and lo - 1 not in taken and lines[lo - 1].strip() and i - (lo - 1) <= 3:
            lo -= 1
        while hi + 1 < len(lines) and hi + 1 not in taken and lines[hi + 1].strip() and (hi + 1) - vi <= 3:
            hi += 1
        for k in range(lo, hi + 1):
            if k == vi:
                continue
            for c in _chunks(lines[k])[(1 if k == i else 0):]:
                if _BAND.match(c) or re.fullmatch(r"[<>≤≥]?\s*\d[\d.]*\s*-\s*\d[\d.]*", c):
                    bands.append(c)
        taken.update({i, vi})
        out.append({"test": test.strip(" .:-"), "value": val["value"], "unit": val["unit"],
                    "reference_range": val["reference_range"] or "; ".join(bands) or None})
    return out


_SECTION = re.compile(r"^\s*(diagnosis|final diagnosis|provisional diagnosis|impression|known case of|"
                      r"medications?|current medications?|discharge medications?|rx|treatment advised|advice on discharge|"
                      r"allerg(?:y|ies)|drug allerg(?:y|ies))\s*[:\-]?\s*(.*)$", re.I)
_DRUG_PREFIX = re.compile(r"^\s*(?:\d+[.)]\s*|[-•*]\s*)?(tab|tablet|cap|capsule|inj|injection|syp|syrup|oint|drops?)\.?\s+(.+)$", re.I)


def rule_items(text: str) -> dict:
    res = {"conditions": [], "medications": [], "allergies": []}
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            section = None
            continue
        m = _SECTION.match(line)
        if m:
            head, rest = m.group(1).lower(), m.group(2).strip()
            section = ("allergies" if head.startswith(("allerg", "drug allerg")) else
                       "medications" if head.startswith(("medic", "current medic", "discharge medic", "rx", "treatment", "advice")) else
                       "conditions")
            if rest:
                _add_item(res, section, rest)
            continue
        drug = _DRUG_PREFIX.match(line)
        if drug:
            _add_item(res, "medications", line)
        elif section:
            _add_item(res, section, line)
    return res


def _add_item(res, section, text):
    text = re.sub(r"^\s*(?:\d+[.)]|[-•*])\s*", "", text).strip()
    if not text or len(text) > 160:
        return
    if section == "allergies":
        for part in re.split(r"[,;]", text):
            part = part.strip()
            if part and part.lower() not in ("none", "nil", "nkda", "no known drug allergies", "na", "n/a"):
                res["allergies"].append({"name": part, "reaction": None})
    elif section == "medications":
        m = re.match(r"^(?:tab|tablet|cap|capsule|inj|injection|syp|syrup)?\.?\s*([A-Za-z][A-Za-z0-9\- ]*?)\s+(\d+(?:\.\d+)?\s*(?:mg|mcg|g|ml|iu|units?)\b)?\s*(.*)$", text, re.I)
        if m:
            res["medications"].append({"name": m.group(1).strip(), "dose": (m.group(2) or "").strip() or None,
                                       "frequency": m.group(3).strip() or None})
    else:
        for part in re.split(r";|,(?![^()]*\))", text):
            part = part.strip(" .")
            if part and part.lower() not in ("nil", "none", "na"):
                res["conditions"].append({"name": part, "detail": None})


# ---------------------------------------------------------------- grounding


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.]", "", s.lower())


def grounded_value(value: str, page_text: str) -> bool:
    num = parse_number(value)
    if num is None:
        return _norm(value) in _norm(page_text)
    flat = page_text.replace(",", "")
    for tok in re.findall(r"-?\d+(?:\.\d+)?", flat):
        if abs(float(tok) - num) < 1e-9:
            return True
    return False


def grounded_name(name: str, page_text: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]{3,}", name.lower())]
    if not words:
        return False
    hay = page_text.lower()
    return sum(w in hay for w in words) >= max(1, len(words) // 2)


# ---------------------------------------------------------------- pipeline


def to_observation(lab: dict, page: int, date_str: str | None) -> dict | None:
    value_text = str(lab.get("value") or "").strip()
    if not value_text:
        return None
    name, loinc, _ = canonical(lab["test"])
    value = parse_number(value_text)
    ref_text = (lab.get("reference_range") or "").strip() or None
    low, high = parse_range(ref_text)
    return {
        "page": page, "test": lab["test"].strip(), "canonical": name, "loinc": loinc,
        "value": value, "value_text": value_text, "unit": (lab.get("unit") or "").strip() or None,
        "ref_low": low, "ref_high": high, "ref_text": ref_text,
        "flag": flag_for(value, low, high), "date": date_str,
    }


BUDGET = float(os.environ.get("MEDPORT_BUDGET", "600"))  # seconds of model time per document


def process_document(conn, doc_id: int, path: str, mime: str, use_llm: bool | None = None,
                     progress=None, budget: float | None = None) -> dict:
    """Reads the file and replaces this document's pages, values and items.
    All the slow work (OCR, the model) happens before the first database write, so a long read
    never holds the database locked. progress(text) is called as each page starts."""
    say = progress or (lambda text: None)
    say("Reading the file")
    pages, notes = read_pages(path, mime)
    st = llm.status()
    if use_llm is None:
        use_llm = st["running"] and st["has_model"]
    model = st["model"]
    if use_llm:
        engine = "llm:" + model
        if st.get("fallback"):
            notes.append(f"{st['configured']} isn't pulled, so {model} was used instead.")
    else:
        engine = "rules"
        if not st["running"]:
            notes.append(f"The local AI model isn't running (nothing answered at {st['url']}), so only the basic "
                         f"rule parser was used. Start Ollama, run: ollama pull {st['configured']}, then press Read again.")
        else:
            notes.append(f"Ollama is running but has no text model. Run: ollama pull {st['configured']}, then press Read again.")
    model_failed = 0
    deadline = time.monotonic() + (BUDGET if budget is None else budget)
    out_of_time = []

    all_text = "\n".join(p["text"] for p in pages)
    report_date = find_report_date(all_text)
    title = lab_name = llm_patient = None
    observations, items, dropped = [], [], 0
    text_pages = [p for p in pages if p["text"].strip()]

    for n, p in enumerate(text_pages, start=1):
        rules = {"labs": rule_labs(p["text"]), **rule_items(p["text"])}
        if use_llm and time.monotonic() > deadline:
            out_of_time.append(p["page"])
            data = rules
        elif use_llm:
            say(f"Reading page {n} of {len(text_pages)} with {model}")
            try:
                data = llm.extract_page(p["text"], model=model)
            except Exception as e:  # model hiccup on one page shouldn't lose the document
                model_failed += 1
                reason = "it took too long" if isinstance(e, TimeoutError) or "timed out" in str(e) else str(e)
                notes.append(f"The model failed on page {p['page']} ({reason}); used the rule parser there.")
                data = rules
            else:
                report_date = report_date or parse_date(data.get("report_date"))
                title = title or data.get("report_title")
                lab_name = lab_name or data.get("lab_name")
                llm_patient = llm_patient or data.get("patient_name")
                # the model leads; rows the rule parser found that the model skipped are added back
                have_names = {canonical(l.get("test") or "")[0] or (l.get("test") or "").lower() for l in data["labs"]}
                extra = [l for l in rules["labs"] if (canonical(l["test"])[0] or l["test"].lower()) not in have_names]
                data = {**data, "labs": data["labs"] + extra}
        else:
            data = rules

        for lab in data.get("labs") or []:
            if not lab.get("test") or not grounded_value(str(lab.get("value", "")), p["text"]):
                dropped += 1
                continue
            obs = to_observation(lab, p["page"], None)
            if obs:
                observations.append(obs)
        for kind, key in (("condition", "conditions"), ("medication", "medications"), ("allergy", "allergies")):
            for it in data.get(key) or []:
                name = (it.get("name") or "").strip()
                if not name or not grounded_name(name, p["text"]):
                    dropped += 1
                    continue
                detail = " ".join(x for x in (it.get("dose"), it.get("frequency"), it.get("detail"), it.get("reaction")) if x) or None
                items.append({"kind": kind, "name": name, "detail": detail, "page": p["page"]})

    if out_of_time:
        notes.append(f"The model ran out of time, so the rule parser read page(s) {', '.join(map(str, out_of_time))}. "
                     f"A smaller model or a faster computer helps (MEDPORT_BUDGET sets the limit).")
    if use_llm and model_failed + len(out_of_time) == len(text_pages) and text_pages:
        engine = "rules (model failed)" if model_failed else "rules (model too slow)"
    report_date = report_date or date.today().isoformat()
    if dropped:
        notes.append(f"Dropped {dropped} extracted value(s) that weren't printed on the page.")

    found = patient.find_patient(all_text)
    found["name"] = found["name"] or llm_patient  # the model can find a name the patterns missed
    check, reasons = patient.check(found, get_profile(conn), report_date)
    prev = conn.execute("SELECT patient_check FROM documents WHERE id=?", (doc_id,)).fetchone()
    if prev and prev[0] == "accepted" and check == "mismatch":
        check = "accepted"  # the user already said this report belongs in their portfolio

    say("Saving")
    conn.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM observations WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM items WHERE doc_id=?", (doc_id,))
    for p in pages:
        conn.execute("INSERT INTO pages (doc_id, page, text, ocr) VALUES (?,?,?,?)", (doc_id, p["page"], p["text"], p["ocr"]))
    seen = set()
    for o in observations:
        key = (o["canonical"] or o["test"].lower(), o["value_text"])
        if key in seen:
            continue
        seen.add(key)
        conn.execute(
            """INSERT INTO observations (doc_id, page, test, canonical, loinc, value, value_text, unit,
               ref_low, ref_high, ref_text, flag, date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (doc_id, o["page"], o["test"], o["canonical"], o["loinc"], o["value"], o["value_text"], o["unit"],
             o["ref_low"], o["ref_high"], o["ref_text"], o["flag"], report_date))
    n_obs = len(seen)
    seen = set()
    for it in items:
        key = (it["kind"], it["name"].lower())
        if key in seen:
            continue
        seen.add(key)
        conn.execute("INSERT INTO items (doc_id, kind, name, detail, date, page) VALUES (?,?,?,?,?,?)",
                     (doc_id, it["kind"], it["name"], it["detail"], report_date, it["page"]))
    conn.execute("""UPDATE documents SET patient_name=?, patient_age=?, patient_sex=?, patient_dob=?, patient_check=?,
                    patient_note=? WHERE id=?""", (found["name"], found["age"], found["sex"], found["dob"], check,
                                                    "; ".join(reasons) or None, doc_id))
    conn.execute("UPDATE documents SET status='review', engine=?, report_date=?, title=COALESCE(title, ?), lab=COALESCE(lab, ?), note=? WHERE id=?",
                 (engine, report_date, title, lab_name, "\n".join(notes) or None, doc_id))
    conn.commit()
    return {"engine": engine, "observations": n_obs, "items": len(seen), "notes": notes, "patient_check": check}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")
