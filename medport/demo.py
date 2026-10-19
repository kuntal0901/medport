"""Public demo mode: fictional sample reports, the rule parser only, nothing a visitor can add or take away.

    python -m medport --demo --host 0.0.0.0     # e.g. on Render; data lives in a temp folder, wiped on restart
"""
import hashlib
import sys
from pathlib import Path

from fastapi import Request
from fastapi.responses import RedirectResponse

# anything that would accept a visitor's file or hand out data beyond the page itself
BLOCKED = ("/upload", "/vault/", "/m/api/", "/share/pair", "/share/file", "/phone")


def samples_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "samples"


def enable(app_module):
    m = app_module
    m.app.state.demo = True
    m.llm.OLLAMA_URL = "http://127.0.0.1:9"  # never use a model in the demo: the rule parser reads the samples

    @m.app.middleware("http")
    async def demo_guard(request: Request, call_next):
        if request.url.path.startswith(BLOCKED):
            return RedirectResponse("/?error=That+is+switched+off+in+the+public+demo.+Download+MedPort+to+use+your+own+reports.",
                                    status_code=303)
        return await call_next(request)

    m.store.set_profile(m.conn, {"name": "Asha Verma (fictional)", "dob": "1963-03-14", "sex": "Female", "blood_group": "B+",
                                 "emergency_contact": "Rahul Verma (fictional)"})
    for pdf in sorted(samples_dir().glob("20*.pdf")):
        data = pdf.read_bytes()
        stored = f"demo-{pdf.name}"
        (m.store.DOCS_DIR / stored).write_bytes(data)
        cur = m.conn.execute("INSERT INTO documents (filename, stored_name, mime, uploaded_at, sha256) VALUES (?,?,?,?,?)",
                             (pdf.name, stored, "application/pdf", m.extract.now(), hashlib.sha256(data).hexdigest()))
        m.conn.commit()
        m.extract.process_document(m.conn, cur.lastrowid, str(m.store.DOCS_DIR / stored), "application/pdf", use_llm=False)
    m.conn.execute("UPDATE documents SET note='Read with the rule parser in this demo. The real app reads reports with a local open model.'")
    m.conn.commit()
