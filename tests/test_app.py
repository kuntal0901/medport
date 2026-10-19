import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["MEDPORT_DATA"] = str(tmp_path_factory.mktemp("data"))
    if not (SAMPLES / "2025-05-lab.pdf").exists():
        subprocess.run([sys.executable, str(SAMPLES / "make_samples.py")], check=True)
    from medport import store, main
    importlib.reload(store)
    importlib.reload(main)
    from fastapi.testclient import TestClient
    return TestClient(main.app), main


def fake_status(running, model="qwen2.5:3b"):
    return lambda: {"running": running, "has_model": running, "has_vision": False, "model": model, "configured": model,
                    "fallback": False, "vision_model": "y", "url": "http://127.0.0.1:11434", "models": [model] if running else []}


def upload(c, name, salt=b""):
    data = (SAMPLES / name).read_bytes() + salt  # a trailing comment makes a "different" file
    return c.post("/upload", files={"files": (name, data, "application/pdf")}, follow_redirects=False)


def test_rules_engine_end_to_end(client, monkeypatch):
    c, main = client
    monkeypatch.setattr(main.llm, "status", fake_status(running=False))
    for n in ["2024-08-lab.pdf", "2025-01-lab.pdf", "2025-05-lab.pdf", "2025-02-discharge.pdf"]:
        r = upload(c, n)
        assert r.status_code == 303 and "/review" in r.headers["location"]
    html = c.get("/").text
    assert "HbA1c" in html and "Penicillin" in html and "Metformin" in html
    obs = main.store.rows(main.conn, "SELECT * FROM observations WHERE canonical='HbA1c' ORDER BY date")
    assert [o["value"] for o in obs] == [7.9, 7.4, 6.8]
    assert all(o["flag"] == "high" for o in obs)
    plt = main.store.rows(main.conn, "SELECT value FROM observations WHERE canonical='Platelet count'")
    assert plt[0]["value"] == 210000  # Indian digit grouping "2,10,000"
    d = main.store.rows(main.conn, "SELECT report_date FROM documents WHERE filename='2025-02-discharge.pdf'")
    assert d[0]["report_date"] == "2025-02-06"  # discharge date, not admission


def test_trend_page(client):
    c, _ = client
    r = c.get("/test/HbA1c")
    assert r.status_code == 200 and "<svg" in r.text and "7.9" in r.text


def test_edit_and_confirm(client):
    c, main = client
    o = main.store.rows(main.conn, "SELECT * FROM observations WHERE canonical='Creatinine' ORDER BY date DESC LIMIT 1")[0]
    r = c.post(f"/obs/{o['id']}", data={"test": "Creatinine", "value_text": "1.05", "unit": "mg/dL", "ref_text": "0.6 - 1.1"}, follow_redirects=False)
    assert r.status_code == 303
    o2 = main.store.rows(main.conn, "SELECT * FROM observations WHERE id=?", o["id"])[0]
    assert o2["value"] == 1.05 and o2["flag"] == "normal" and o2["verified"] == 1
    c.post(f"/doc/{o['doc_id']}/confirm")
    assert main.store.rows(main.conn, "SELECT status FROM documents WHERE id=?", o["doc_id"])[0]["status"] == "done"


def test_llm_engine_drops_values_not_on_page(client, monkeypatch):
    c, main = client
    monkeypatch.setattr(main.llm, "status", fake_status(running=True))
    fake = {
        "report_date": "2024-08-12", "report_title": "Lipid profile", "lab_name": "Sunrise Diagnostics",
        "labs": [
            {"test": "Total Cholesterol", "value": "228", "unit": "mg/dL", "reference_range": "< 200"},
            {"test": "Vitamin B12", "value": "999", "unit": "pg/mL", "reference_range": "200 - 900"},  # invented
        ],
        "conditions": [{"name": "Hypothyroidism", "detail": None}],  # invented
        "medications": [], "allergies": [],
    }
    monkeypatch.setattr(main.extract.llm, "extract_page", lambda text, model=None: fake)
    r = upload(c, "2024-08-lab.pdf", salt=b"\n%llm-test\n")
    doc_id = int(r.headers["location"].split("/")[2])
    d = main.store.rows(main.conn, "SELECT * FROM documents WHERE id=?", doc_id)[0]
    assert d["engine"] == "llm:qwen2.5:3b" and d["lab"] == "Sunrise Diagnostics"
    tests = {o["canonical"] for o in main.store.rows(main.conn, "SELECT canonical FROM observations WHERE doc_id=?", doc_id)}
    assert "Total cholesterol" in tests and "Vitamin B12" not in tests
    assert "HbA1c" in tests  # skipped by the model, added back from the rule parser
    assert not main.store.rows(main.conn, "SELECT * FROM items WHERE doc_id=?", doc_id)
    assert "Dropped 2" in d["note"]
    c.post(f"/doc/{doc_id}/delete")


def test_vault_roundtrip(client):
    c, main = client
    before = main.store.rows(main.conn, "SELECT COUNT(*) n FROM observations")[0]["n"]
    blob = c.post("/vault/export", data={"passphrase": "correct horse"}).content
    assert blob.startswith(b"MEDVAULT1") and b"HbA1c" not in blob
    r = c.post("/vault/import", files={"file": ("b.medvault", blob)}, data={"passphrase": "wrong pass"}, follow_redirects=False)
    assert "Wrong+passphrase" in r.headers["location"]
    main.conn.execute("DELETE FROM observations")
    main.conn.commit()
    r = c.post("/vault/import", files={"file": ("b.medvault", blob)}, data={"passphrase": "correct horse"}, follow_redirects=False)
    assert "Restored" in r.headers["location"]
    assert main.store.rows(main.conn, "SELECT COUNT(*) n FROM observations")[0]["n"] == before
    docs = main.store.rows(main.conn, "SELECT stored_name FROM documents")
    assert all((main.store.DOCS_DIR / d["stored_name"]).exists() for d in docs)


def test_share_file_is_encrypted(client):
    c, _ = client
    r = c.post("/share/file", data={"passphrase": "doctor123"})
    assert r.status_code == 200 and "crypto.subtle" in r.text and "HbA1c" not in r.text


def test_share_link_and_lan_guard(client):
    c, main = client
    r = c.post("/share/link", data={"hours": 1}, follow_redirects=False)
    token = r.headers["location"].split("token=")[1]
    assert "HbA1c" in c.get(f"/s/{token}").text
    assert c.get("/s/not-a-token").status_code == 404
    from fastapi.testclient import TestClient
    remote = TestClient(main.app, client=("192.168.1.50", 5000))
    assert remote.get("/").status_code == 403
    assert remote.get("/vault/export").status_code == 403
    assert remote.get(f"/s/{token}").status_code == 200
    c.post("/share/revoke")
    assert remote.get(f"/s/{token}").status_code == 404


def test_duplicate_upload_is_ignored(client):
    c, main = client
    n = main.store.rows(main.conn, "SELECT COUNT(*) n FROM documents")[0]["n"]
    upload(c, "2025-05-lab.pdf")
    assert main.store.rows(main.conn, "SELECT COUNT(*) n FROM documents")[0]["n"] == n


# ---- reports whose table cells wrap onto several lines (from a real report, anonymised)

FIXTURES = Path(__file__).parent / "fixtures"


def test_rule_parser_pairs_value_on_its_own_line():
    from medport.extract import rule_labs
    labs = rule_labs((FIXTURES / "split_row_report.txt").read_text())
    assert len(labs) == 1  # the "VITAMIN D TOTAL" panel heading is not a second result
    lab = labs[0]
    assert (lab["test"], lab["value"], lab["unit"]) == ("TOTAL VITAMIN D", "15.90", "ng/mL")
    assert "Sufficient: 30-100" in lab["reference_range"]


def test_labelled_reference_bands_use_the_normal_band():
    from medport.normalize import parse_range
    assert parse_range("Deficient:<20; Insufficient: 20-30; Sufficient: 30-100; Potentially toxic: >100") == (30, 100)
    assert parse_range("Normal: <5.7; Prediabetes: 5.7-6.4; Diabetes: >=6.5") == (None, 5.7)
    assert parse_range("70 - 100") == (70, 100)


def test_wrapped_cell_pdf_end_to_end(client, tmp_path, monkeypatch):
    """Same layout as a PDF: the value sits vertically centred beside a 4-line reference cell."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c, main = client
    monkeypatch.setattr(main.llm, "status", fake_status(running=False))
    pdf = tmp_path / "wrapped.pdf"
    cv = canvas.Canvas(str(pdf), pagesize=A4)
    h = A4[1]
    cv.setFont("Helvetica", 9)
    cv.drawString(40, h - 60, "Sample Collected at : 03/10/2026 | 03:33 PM")
    for x, t in ((40, "Test"), (230, "Result"), (300, "Unit"), (370, "Reference Interval"), (520, "Flag")):
        cv.drawString(x, h - 100, t)
    cv.drawString(40, h - 118, "VITAMIN D TOTAL")
    cv.drawString(40, h - 130, "Sample: SERUM PLAIN")
    for i, t in enumerate(["Deficient:<20", "Insufficient: 20-30", "Sufficient: 30-100", "Potentially toxic: >100"]):
        cv.drawString(370, h - 142 - 12 * i, t)
    cv.drawString(40, h - 154, "TOTAL VITAMIN D")
    cv.drawString(230, h - 160, "15.90")
    cv.drawString(300, h - 160, "ng/mL")
    cv.drawString(40, h - 166, "(Method:CLIA)")
    cv.save()
    r = c.post("/upload", files={"files": ("wrapped.pdf", pdf.read_bytes(), "application/pdf")}, follow_redirects=False)
    doc_id = int(r.headers["location"].split("/")[2])
    obs = main.store.rows(main.conn, "SELECT * FROM observations WHERE doc_id=?", doc_id)
    assert [(o["canonical"], o["value"], o["unit"], o["flag"]) for o in obs] == [("Vitamin D", 15.9, "ng/mL", "low")]
    assert obs[0]["date"] == "2026-10-03"
    c.post(f"/doc/{doc_id}/delete")


def test_rule_parser_liver_panel_value_below_name():
    from medport.extract import rule_labs
    from medport.normalize import canonical
    labs = rule_labs((FIXTURES / "lft_report.txt").read_text())
    got = {canonical(l["test"])[0]: (l["value"], l["unit"], l["reference_range"]) for l in labs}
    assert len(labs) == 11
    assert got["Total bilirubin"] == ("0.80", "mg/dL", "0.1 - 1.2")
    assert got["AST (SGOT)"] == ("72.00", "IU/L", "15 - 41")
    assert got["ALT (SGPT)"] == ("73.00", "U/L", "17 - 63")
    assert got["A/G ratio"] == ("1.37", None, "0.8 - 2")


def test_no_model_running_explains_what_to_do(client, tmp_path, monkeypatch):
    c, main = client
    monkeypatch.setattr(main.llm, "status", fake_status(running=False))
    r = upload(c, "2025-01-lab.pdf", salt=b"\n%no-model\n")
    doc_id = int(r.headers["location"].split("/")[2])
    note = main.store.rows(main.conn, "SELECT note FROM documents WHERE id=?", doc_id)[0]["note"]
    assert "isn't running" in note and "ollama pull" in note
    assert "isn&#39;t running" in c.get(f"/doc/{doc_id}/review").text
    c.post(f"/doc/{doc_id}/delete")


# ---- the real HTTP contract with Ollama, against a stand-in server

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


@pytest.fixture
def fake_ollama(monkeypatch):
    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, {"models": [{"name": "nomic-embed-text:latest"}, {"name": "llama3.2:3b"}]})

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(req)
            if isinstance(req["format"], dict):  # behave like an old Ollama that rejects schemas
                return self._send(400, {"error": "invalid format"})
            text = req["messages"][1]["content"]
            labs = []
            for line_no, (name, value) in enumerate([("TOTAL BILIRUBIN", "0.80"), ("GGT.", "19.00"), ("GLOBULIN", "3.50")]):
                if name in text:
                    labs.append({"test": name, "value": value, "unit": None, "reference_range": None})
            content = "Here you go:\n```json\n" + json.dumps({"report_date": "2026-10-01", "labs": labs}) + "\n```"
            self._send(200, {"message": {"role": "assistant", "content": content}})

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    from medport import llm
    monkeypatch.setattr(llm, "OLLAMA_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setattr(llm, "CHUNK_CHARS", 600)
    monkeypatch.setattr(llm, "_schema_ok", True)
    yield calls
    srv.shutdown()


def test_model_path_against_ollama_api(fake_ollama):
    from medport import llm
    st = llm.status()
    assert st["running"] and st["model"] == "llama3.2:3b" and st["fallback"]  # qwen not pulled, embedder skipped
    data = llm.extract_page((FIXTURES / "lft_report.txt").read_text(), model=st["model"])
    assert len([c for c in fake_ollama if c["format"] == "json"]) > 1  # chunked, and fell back to JSON mode
    assert len([c for c in fake_ollama if isinstance(c["format"], dict)]) == 1  # schema tried once, not per chunk
    assert {l["test"] for l in data["labs"]} == {"TOTAL BILIRUBIN", "GGT.", "GLOBULIN"}
    assert data["report_date"] == "2026-10-01"


def test_ollama_host_bind_address_is_reachable():
    from medport.llm import _ollama_url
    assert _ollama_url("0.0.0.0") == "http://127.0.0.1:11434"
    assert _ollama_url(":11434") == "http://127.0.0.1:11434"
    assert _ollama_url("") == "http://127.0.0.1:11434"


def test_thinking_is_turned_off(fake_ollama):
    from medport import llm
    llm.extract_page("TOTAL BILIRUBIN  0.80  mg/dL", model="llama3.2:3b")
    assert fake_ollama and all(c.get("think") is False for c in fake_ollama)


def test_slow_model_reads_in_background(client, monkeypatch):
    """Read again used to block the browser until every page was done; now it returns at once and shows progress."""
    import time
    c, main = client
    gate = threading.Event()
    monkeypatch.setattr(main.llm, "status", fake_status(running=True))
    monkeypatch.setattr(main.extract.llm, "extract_page", lambda text, model=None: (gate.wait(10), {
        "report_date": None, "report_title": None, "lab_name": None, "labs": [], "conditions": [], "medications": [],
        "allergies": []})[1])
    monkeypatch.setattr(main, "WAIT_SECS", 0.2)
    t = time.monotonic()
    r = upload(c, "2025-01-lab.pdf", salt=b"\n%slow\n")
    assert time.monotonic() - t < 5
    doc_id = int(r.headers["location"].split("/")[2])
    p = c.get(f"/doc/{doc_id}/progress").json()
    assert p["reading"] and p["status"] == "reading" and "with qwen2.5:3b" in p["step"]
    assert "updates by itself" in c.get(f"/doc/{doc_id}/review").text
    c.post(f"/doc/{doc_id}/reprocess")  # pressing Read again mid-read doesn't start a second read
    gate.set()
    for _ in range(100):
        if not c.get(f"/doc/{doc_id}/progress").json()["reading"]:
            break
        time.sleep(0.05)
    d = main.store.rows(main.conn, "SELECT * FROM documents WHERE id=?", doc_id)[0]
    assert d["status"] == "review" and d["engine"] == "llm:qwen2.5:3b"
    assert main.store.rows(main.conn, "SELECT * FROM observations WHERE canonical='HbA1c' AND doc_id=?", doc_id)
    c.post(f"/doc/{doc_id}/delete")


def test_out_of_time_falls_back_to_rules(client, monkeypatch):
    c, main = client
    monkeypatch.setattr(main.llm, "status", fake_status(running=True))
    monkeypatch.setattr(main.extract.llm, "extract_page", lambda *a, **k: pytest.fail("model called after the budget ran out"))
    monkeypatch.setattr(main.extract, "BUDGET", -1)
    r = upload(c, "2025-01-lab.pdf", salt=b"\n%budget\n")
    doc_id = int(r.headers["location"].split("/")[2])
    d = main.store.rows(main.conn, "SELECT * FROM documents WHERE id=?", doc_id)[0]
    assert d["engine"] == "rules (model too slow)" and "ran out of time" in d["note"]
    assert main.store.rows(main.conn, "SELECT * FROM observations WHERE canonical='HbA1c' AND doc_id=?", doc_id)
    c.post(f"/doc/{doc_id}/delete")


# ---- is the report about the profile's owner?

def test_patient_names_match_loosely_but_not_other_people():
    from medport.patient import find_patient, names_match
    assert names_match("Mrs. Asha Verma (fictional)", "Asha Verma")
    assert names_match("A. Verma", "Asha Verma") and names_match("ASHA VARMA", "Asha Verma")
    assert names_match("Mohammad Ali", "Mohammed Ali")
    assert not names_match("Usha Verma", "Asha Verma")  # a relative with the same surname
    assert not names_match("Rakesh Kumar", "Ramesh Kumar")
    assert names_match(None, "Asha Verma") is None
    p = find_patient("Patient Name:Smt. K. Lakshmi  | Age/Gender: 58 Years / Female")
    assert (p["name"], p["age"], p["sex"]) == ("Smt. K. Lakshmi", 58, "F")
    assert find_patient("Lab Name: Sunrise Diagnostics")["name"] is None


def _report_for(name, tmp_path, salt):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    for y, line in ((60, "Sunrise Diagnostics"), (90, f"Patient Name: {name}"), (105, "Age/Sex: 45 Y / M"),
                    (120, "Sample Collected: 10/09/2025"), (160, "HbA1c  5.4  %  4.0 - 5.6"), (180, salt)):
        page.insert_text((40, y), line, fontsize=10)
    path = tmp_path / f"{salt}.pdf"
    doc.save(path)
    return path.read_bytes()


def test_someone_elses_report_waits_for_the_user(client, tmp_path, monkeypatch):
    c, main = client
    monkeypatch.setattr(main.llm, "status", fake_status(running=False))
    c.post("/profile", data={"name": "Asha Verma", "dob": "1963-03-14", "sex": "Female"})
    try:
        before = c.get("/").text.count("5.4")
        r = c.post("/upload", files={"files": ("other.pdf", _report_for("Mr. Ramesh Kumar", tmp_path, "held-1"),
                                               "application/pdf")}, follow_redirects=False)
        doc_id = int(r.headers["location"].split("/")[2])
        d = main.store.rows(main.conn, "SELECT * FROM documents WHERE id=?", doc_id)[0]
        assert d["patient_check"] == "mismatch" and "Ramesh Kumar" in d["patient_note"]
        assert "may belong to someone else" in c.get(f"/doc/{doc_id}/review").text
        home = c.get("/").text
        assert "Is this your report?" in home and home.count("5.4") == before  # its values stay out
        assert "error=" in c.post(f"/doc/{doc_id}/confirm", follow_redirects=False).headers["location"]

        c.post(f"/doc/{doc_id}/patient", data={"decision": "add"})
        assert "Is this your report?" not in c.get("/").text
        c.post(f"/doc/{doc_id}/reprocess")  # reading it again keeps the user's answer
        assert main.store.rows(main.conn, "SELECT patient_check FROM documents WHERE id=?", doc_id)[0]["patient_check"] == "accepted"
        c.post(f"/doc/{doc_id}/delete")

        r = c.post("/upload", files={"files": ("other2.pdf", _report_for("Ramesh Kumar", tmp_path, "held-2"),
                                               "application/pdf")}, follow_redirects=False)
        doc_id = int(r.headers["location"].split("/")[2])
        r = c.post(f"/doc/{doc_id}/patient", data={"decision": "discard"}, follow_redirects=False)
        assert "/documents" in r.headers["location"]
        assert not main.store.rows(main.conn, "SELECT * FROM documents WHERE id=?", doc_id)

        r = c.post("/upload", files={"files": ("mine.pdf", _report_for("Mrs. Asha Verma", tmp_path, "mine-1"),
                                               "application/pdf")}, follow_redirects=False)
        doc_id = int(r.headers["location"].split("/")[2])
        # same name, but the report says male and age 45: still asks
        assert main.store.rows(main.conn, "SELECT patient_check FROM documents WHERE id=?", doc_id)[0]["patient_check"] == "mismatch"
        c.post(f"/doc/{doc_id}/delete")
    finally:
        c.post("/profile", data={})


def test_add_and_remove_your_own_items(client):
    c, main = client
    r = c.post("/items", data={"kind": "allergy", "name": "Peanuts", "detail": "hives"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?ok=")
    home = c.get("/").text
    assert "Peanuts" in home and "Added by you" in home
    item = main.store.rows(main.conn, "SELECT * FROM items WHERE name='Peanuts'")[0]
    assert item["doc_id"] is None
    c.post(f"/item/{item['id']}", data={"action": "delete", "back": "portfolio"})
    assert "Peanuts" not in c.get("/").text
    assert c.post("/items", data={"kind": "allergy", "name": "  "}).status_code == 400


# ---- phone app: pairing, offline copy, upload, and the home-Wi-Fi certificate

def test_phone_pairs_syncs_uploads_and_can_be_removed(client):
    c, main = client
    r = c.post("/share/pair", follow_redirects=False)
    code = r.headers["location"].split("pair=")[1].split("#")[0]
    assert len(code) == 8 and set(code) <= set(main.CODE_CHARS)
    assert "Pair this phone" in c.get("/m/").text and c.get("/m/sw.js").status_code == 200
    assert c.get("/m/manifest.webmanifest").json()["start_url"] == "/m/"
    assert c.get("/m/icon-192.png").content[:8] == b"\x89PNG\r\n\x1a\n"

    assert c.get("/m/api/snapshot").status_code == 401
    assert c.post("/m/api/pair", json={"code": "WRONG123"}).status_code == 403
    typed = (code[:4] + "-" + code[4:]).lower()  # people type it with a dash, in lower case
    token = c.post("/m/api/pair", json={"code": typed, "name": "Test phone"}).json()["token"]
    assert c.post("/m/api/pair", json={"code": code}).status_code == 403  # one use only
    auth = {"Authorization": f"Bearer {token}"}

    snap = c.get("/m/api/snapshot", headers=auth).json()
    assert "HbA1c" in snap["html"] and "/doc/" not in snap["html"]  # works offline: no links back to the laptop
    assert "{data}" in snap["share_template"] and snap["rounds"] == 310_000

    pdf = (SAMPLES / "2025-05-lab.pdf").read_bytes() + b"\n%phone\n"
    up = c.post("/m/api/upload", files={"files": ("photo.pdf", pdf, "application/pdf")}, headers=auth)
    assert up.json()["ok"]
    c.post(f"/doc/{up.json()['doc_id']}/delete")

    dev = main.store.rows(main.conn, "SELECT * FROM devices WHERE name='Test phone'")[0]
    assert dev["last_seen"] and token not in str(dev)  # only a hash of the token is kept
    c.post(f"/share/devices/{dev['id']}/revoke")
    assert c.get("/m/api/snapshot", headers=auth).status_code == 401


def test_wifi_devices_reach_only_the_phone_app_and_share_links(client):
    from fastapi.testclient import TestClient
    _, main = client
    remote = TestClient(main.app, client=("192.168.1.50", 5000))
    assert remote.get("/m/").status_code == 200
    assert remote.get("/phone/medport-ca.crt").status_code == 200
    assert remote.get("/m/api/snapshot").status_code == 401
    assert remote.post("/share/pair", follow_redirects=False).status_code == 403  # pairing starts on the laptop
    assert remote.get("/documents").status_code == 403


def test_home_wifi_certificate_cannot_vouch_for_public_sites(tmp_path):
    import datetime
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    from cryptography.x509.verification import DNSName, IPAddress, PolicyBuilder, Store
    from medport import tls
    cert_p, _ = tls.ensure_server_cert(tmp_path, "192.168.1.17")
    ca = x509.load_pem_x509_certificate((tmp_path / "ca.crt").read_bytes())
    ca_key = serialization.load_pem_private_key((tmp_path / "ca.key").read_bytes(), None)
    verify = lambda name, cert: PolicyBuilder().store(Store([ca])).build_server_verifier(name).verify(cert, [])
    verify(IPAddress(ipaddress.ip_address("192.168.1.17")), x509.load_pem_x509_certificates(cert_p.read_bytes())[0])

    now = datetime.datetime.now(datetime.timezone.utc)
    k = ec.generate_private_key(ec.SECP256R1())
    fake = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "bank")]))
            .issuer_name(ca.subject).public_key(k.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("examplebank.com")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    with pytest.raises(Exception, match="name constraints"):
        verify(DNSName("examplebank.com"), fake)
    with pytest.raises(ValueError):
        tls.ensure_server_cert(tmp_path, "8.8.8.8")  # never issued for a public address


def test_pairing_code_survives_a_restart_and_reports_its_state(client):
    c, main = client
    code = c.post("/share/pair", follow_redirects=False).headers["location"].split("pair=")[1].split("#")[0]
    assert c.get(f"/share/pair/{code}").json()["state"] == "waiting"
    main.conn.commit()
    fresh = main.store.connect()  # what a restarted MedPort would see
    assert fresh.execute("SELECT code FROM pair_codes WHERE code=?", (code,)).fetchone()
    fresh.close()
    r = c.post("/m/api/pair", json={"code": code, "name": "Restart phone"})
    assert r.status_code == 200
    assert c.get(f"/share/pair/{code}").json() == {"state": "paired", "name": "Restart phone"}
    again = c.post("/m/api/pair", json={"code": code})
    assert again.status_code == 403 and "already used" in again.json()["detail"]
    assert "doesn't recognise" in c.post("/m/api/pair", json={"code": "ZZZZ-ZZZZ"}).json()["detail"]
    dev = main.store.rows(main.conn, "SELECT id FROM devices WHERE name='Restart phone'")[0]
    c.post(f"/share/devices/{dev['id']}/revoke")
