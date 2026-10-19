"""Talks to a local Ollama server. Nothing here ever leaves localhost by default.

Any open-weight model Ollama can run works; swap it with MEDPORT_MODEL.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.request



def _ollama_url(host: str) -> str:
    """OLLAMA_HOST is also the *server's* bind setting, often "0.0.0.0" or ":11434";
    a client can't connect to that (it fails on Windows), so point it at localhost."""
    host = host.strip().rstrip("/") or "127.0.0.1:11434"
    if not host.startswith("http"):
        host = "http://" + host
    scheme, rest = host.split("://", 1)
    if rest.startswith(":"):
        rest = "127.0.0.1" + rest
    rest = rest.replace("0.0.0.0", "127.0.0.1")
    if not re.search(r":\d+$", rest):
        rest += ":11434"
    return f"{scheme}://{rest}"


OLLAMA_URL = _ollama_url(os.environ.get("OLLAMA_HOST", ""))
MODEL = os.environ.get("MEDPORT_MODEL", "qwen2.5:3b")
VISION_MODEL = os.environ.get("MEDPORT_VISION_MODEL", "qwen2.5vl:3b")
# per model call; a page that takes longer falls back to the rule parser instead of hanging
TIMEOUT = float(os.environ.get("MEDPORT_TIMEOUT", "180"))

SCHEMA = {
    "type": "object",
    "properties": {
        "report_date": {"type": ["string", "null"], "description": "Date the sample was collected or report issued, YYYY-MM-DD"},
        "report_title": {"type": ["string", "null"], "description": "Name of the test or panel, e.g. Liver Function Test; not a department heading like BIOCHEMISTRY"},
        "lab_name": {"type": ["string", "null"], "description": "Name of the laboratory or hospital that issued the report"},
        "patient_name": {"type": ["string", "null"], "description": "The patient's name as printed"},
        "labs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "test": {"type": "string"},
                    "value": {"type": "string"},
                    "unit": {"type": ["string", "null"]},
                    "reference_range": {"type": ["string", "null"]},
                },
                "required": ["test", "value", "unit", "reference_range"],
            },
        },
        "conditions": {
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "detail": {"type": ["string", "null"]}}, "required": ["name"]},
        },
        "medications": {
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "dose": {"type": ["string", "null"]}, "frequency": {"type": ["string", "null"]}}, "required": ["name"]},
        },
        "allergies": {
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}, "reaction": {"type": ["string", "null"]}}, "required": ["name"]},
        },
    },
    "required": ["report_date", "report_title", "lab_name", "labs", "conditions", "medications", "allergies"],
}

SYSTEM = """You extract data from one page of a patient's medical document.
Copy values exactly as printed. Never compute, guess, interpret or diagnose.
- labs: every measured test result on the page, with its value, unit and reference range as printed.
- conditions: only diagnoses explicitly written on the page (e.g. under Diagnosis, Impression, History).
- medications: only drugs explicitly prescribed or listed as current.
- allergies: only allergies explicitly stated.
Lab tables are often broken across lines: a test's value and unit may sit on the line below its name,
and a reference interval may span several labelled lines (e.g. "Deficient:<20", "Sufficient: 30-100").
Pair each test with its own value, and copy every line of its reference interval joined with "; ".
A panel heading (e.g. "LIPID PROFILE", "VITAMIN D TOTAL" above "TOTAL VITAMIN D") is not a separate test.
- report_title: the test or panel name (e.g. "Liver Function Test"), not a department heading such as BIOCHEMISTRY or PATHOLOGY.
- lab_name: the laboratory or hospital that issued the report, usually in the letterhead.
- patient_name: the patient's name exactly as printed (not the doctor's).
Use empty lists when the page has none. Use null for anything not printed.
Write report_date as YYYY-MM-DD.

Example. Text:
  TOTAL BILIRUBIN
  0.80  mg/dL  0.1 - 1.2  Normal
  (Method:Colorimetric Diazo)
  A : G RATIO
  1.37  0.8 - 2
gives labs:
  [{"test": "TOTAL BILIRUBIN", "value": "0.80", "unit": "mg/dL", "reference_range": "0.1 - 1.2"},
   {"test": "A : G RATIO", "value": "1.37", "unit": null, "reference_range": "0.8 - 2"}]
Method, sample type and signature lines are not tests."""

CHUNK_CHARS = int(os.environ.get("MEDPORT_CHUNK_CHARS", "3500"))
_NOT_CHAT = re.compile(r"embed|vl\b|vl:|llava|vision|minicpm-v|moondream|bakllava|clip", re.I)


_think_ok = True


def _post(path: str, payload: dict, timeout: float | None = None) -> dict:
    """Thinking models (gemma4, qwen3, deepseek-r1) otherwise write a long hidden essay before the
    JSON: 3x slower on a CPU for no better extraction. Older Ollama versions reject the flag."""
    global _think_ok
    if _think_ok:
        payload = {**payload, "think": False}
    req = urllib.request.Request(
        OLLAMA_URL + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 400 and "think" in payload and "think" in e.read().decode(errors="replace").lower():
            _think_ok = False
            return _post(path, {k: v for k, v in payload.items() if k != "think"}, timeout)
        raise


def status() -> dict:
    """Which local models are installed, so the UI can say what will be used.
    If the configured model isn't pulled, any installed text model is used instead."""
    base = {"running": False, "model": MODEL, "configured": MODEL, "has_model": False, "fallback": False,
            "vision_model": VISION_MODEL, "has_vision": False, "url": OLLAMA_URL, "models": []}
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/tags", timeout=3) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return base

    def find(m):
        return next((n for n in names if n == m or n == m + ":latest" or (":" not in m and n.split(":")[0] == m)), None)

    chosen = find(MODEL)
    fallback = False
    if not chosen:
        chosen = next((n for n in names if not _NOT_CHAT.search(n)), None)
        fallback = chosen is not None
    return {**base, "running": True, "models": names, "model": chosen or MODEL, "has_model": chosen is not None,
            "fallback": fallback, "has_vision": find(VISION_MODEL) is not None}


def _chunks(text: str, limit: int | None = None) -> list[str]:
    """Split on line boundaries so small models with short context see whole rows.
    Consecutive chunks share 3 lines so a test name and its value aren't separated."""
    limit = limit or CHUNK_CHARS
    lines = text.splitlines()
    out, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) + 1 > limit:
            out.append("\n".join(cur))
            cur = cur[-3:]
            size = sum(len(x) + 1 for x in cur)
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def _parse_json(content: str) -> dict:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)  # model wrapped the JSON in prose or code fences
        if not m:
            raise
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("model did not return an object")
    for key in ("labs", "conditions", "medications", "allergies"):
        v = data.get(key)
        data[key] = [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []
    return data


_schema_ok = True


def _chat_json(model: str, text: str) -> dict:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "PAGE TEXT:\n" + text}]
    global _schema_ok
    last_err = None
    # 1) JSON schema (Ollama 0.5+), 2) plain JSON mode for older Ollama or models that reject the schema
    for fmt in ((SCHEMA, "json") if _schema_ok else ("json",)):
        try:
            resp = _post("/api/chat", {"model": model, "stream": False, "format": fmt,
                                       "options": {"temperature": 0, "num_ctx": 8192}, "messages": messages})
            return _parse_json(resp["message"]["content"])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise RuntimeError(f"model '{model}' is not pulled; run: ollama pull {model}") from e
            if fmt is SCHEMA:
                _schema_ok = False  # don't pay for the failed attempt on every chunk
            last_err = e
        except (ValueError, KeyError) as e:
            last_err = e
    raise last_err


def extract_page(text: str, model: str | None = None) -> dict:
    model = model or MODEL
    merged = {"report_date": None, "report_title": None, "lab_name": None,
              "labs": [], "conditions": [], "medications": [], "allergies": []}
    for chunk in _chunks(text):
        data = _chat_json(model, chunk)
        for key in ("report_date", "report_title", "lab_name"):
            merged[key] = merged[key] or data.get(key)
        for key in ("labs", "conditions", "medications", "allergies"):
            merged[key].extend(data[key])
    return merged


def transcribe_image(image_bytes: bytes) -> str:
    """Use a local vision model as OCR for photos and scanned pages."""
    resp = _post("/api/chat", {
        "model": VISION_MODEL,
        "stream": False,
        "options": {"temperature": 0},
        "messages": [{
            "role": "user",
            "content": "Transcribe all text in this medical document exactly as written, line by line. "
                       "Keep table rows on one line with columns separated by two spaces. Do not add commentary.",
            "images": [base64.b64encode(image_bytes).decode()],
        }],
    })
    return resp["message"]["content"]
