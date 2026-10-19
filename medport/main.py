"""MedPort: a private medical portfolio that runs on your own machine.

    python -m medport            # http://127.0.0.1:8765, this computer only
    python -m medport --lan      # also lets a doctor open a QR share link on the same Wi-Fi
"""
import hashlib
import ipaddress
import json
import mimetypes
import secrets
import socket
import threading
import time
import uuid
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import extract, llm, portfolio, store, vault
from .normalize import canonical, flag_for, parse_number, parse_range

HERE = Path(__file__).parent
app = FastAPI(title="MedPort", docs_url=None, redoc_url=None, openapi_url=None)
app.state.demo = False
app.state.lan = False
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
# browsers cache /static; a new version number after an update makes them fetch the new look
templates.env.globals["asset_v"] = lambda: int(max(p.stat().st_mtime for p in (HERE / "static").iterdir()))
conn = store.connect()

ALLOWED = {"application/pdf", "image/png", "image/jpeg", "image/webp"}
SHARES: dict[str, float] = {}  # token -> expiry (epoch seconds); in memory, gone when the app stops
PORT = 8765
READING: dict[int, str] = {}  # doc_id -> what the background reader is doing now
WAIT_SECS = 3.0  # a fast read finishes before the redirect; a slow model keeps going in the background
# a read that was cut off by the app stopping would otherwise say "reading" forever
conn.execute("UPDATE documents SET status='error', note='Reading stopped when MedPort closed. Press Read again.' "
             "WHERE status='reading'")
conn.commit()


@app.middleware("http")
async def local_only(request: Request, call_next):
    """Even when listening on the LAN, only share links are reachable from other devices."""
    host = request.client.host if request.client else "127.0.0.1"
    try:
        local = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = host in ("testclient", "localhost")
    path = request.url.path
    if getattr(request.app.state, "demo", False):  # the public demo holds only fictional data
        return await call_next(request)
    if not local and not path.startswith(("/s/", "/static/", "/m/", "/phone")):
        return Response("Only share links are available from other devices.", status_code=403)
    return await call_next(request)


def render(request, template, **ctx):
    return templates.TemplateResponse(request, template, ctx)


def _back(url):
    return RedirectResponse(url, status_code=303)


# ------------------------------------------------------------------ portfolio


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return render(request, "portfolio.html", p=portfolio.build(conn), ai=llm.status(), share=None)


@app.get("/test/{name}", response_class=HTMLResponse)
def test_page(request: Request, name: str):
    pts = store.rows(conn, f"""SELECT o.*, d.filename, d.lab FROM observations o JOIN documents d ON d.id=o.doc_id
                              WHERE COALESCE(o.canonical, o.test) = ? AND {portfolio.MINE} ORDER BY o.date, o.id""", name)
    if not pts:
        pts = [o for o in store.rows(conn, f"""SELECT o.*, d.filename, d.lab FROM observations o
                                            JOIN documents d ON d.id=o.doc_id WHERE {portfolio.MINE} ORDER BY o.date, o.id""")
               if (o["canonical"] or o["test"].strip().title()) == name]
    if not pts:
        raise HTTPException(404)
    unit = (pts[-1]["unit"] or "").lower()
    chart = portfolio.trend_chart([p for p in pts if (p["unit"] or "").lower() == unit])
    return render(request, "test.html", name=name, pts=pts, chart=chart)


@app.get("/profile", response_class=HTMLResponse)
def profile_form(request: Request):
    return render(request, "profile.html", profile=store.get_profile(conn))


@app.post("/profile")
def profile_save(name: str = Form(""), dob: str = Form(""), sex: str = Form(""), blood_group: str = Form(""),
                 phone: str = Form(""), emergency_contact: str = Form(""), notes: str = Form("")):
    store.set_profile(conn, dict(name=name.strip(), dob=dob.strip(), sex=sex.strip(), blood_group=blood_group.strip(),
                                 phone=phone.strip(), emergency_contact=emergency_contact.strip(), notes=notes.strip()))
    return _back("/")


# ------------------------------------------------------------------ documents


@app.post("/upload")
async def upload(files: list[UploadFile] = File(...)):
    last = await _ingest(files)
    if last is None:
        return _back("/?error=Only+PDF+or+image+files+are+supported")
    return _back(f"/doc/{last}/review" if len(files) == 1 else "/documents")


async def _ingest(files: list[UploadFile]) -> int | None:
    """Stores each new file and starts reading it. Returns the last document id."""
    last = None
    for f in files:
        mime = f.content_type or mimetypes.guess_type(f.filename or "")[0] or ""
        if mime == "image/jpg":
            mime = "image/jpeg"
        if mime not in ALLOWED:
            continue
        data = await f.read()
        digest = hashlib.sha256(data).hexdigest()
        dup = conn.execute("SELECT id FROM documents WHERE sha256=?", (digest,)).fetchone()
        if dup:  # same file uploaded again: don't double-count its values
            last = dup["id"]
            continue
        ext = mimetypes.guess_extension(mime) or ""
        stored = f"{uuid.uuid4().hex}{ext}"
        path = store.DOCS_DIR / stored
        path.write_bytes(data)
        cur = conn.execute("INSERT INTO documents (filename, stored_name, mime, uploaded_at, sha256) VALUES (?,?,?,?,?)",
                           (Path(f.filename or "report").name, stored, mime, extract.now(), digest))
        conn.commit()
        last = cur.lastrowid
        _start_reading(last, str(path), mime)
    return last


def _read(doc_id: int, path: str, mime: str):
    """Runs in a worker thread with its own connection: a local model can take minutes per report."""
    c = store.connect()
    try:
        extract.process_document(c, doc_id, path, mime, progress=lambda text: READING.__setitem__(doc_id, text))
    except Exception as e:
        c.execute("UPDATE documents SET status='error', note=? WHERE id=?", (f"Could not read this file: {e}", doc_id))
        c.commit()
    finally:
        c.close()
        READING.pop(doc_id, None)


def _start_reading(doc_id: int, path: str, mime: str):
    if doc_id in READING:  # already being read: don't start a second copy
        return
    READING[doc_id] = "Starting"
    conn.execute("UPDATE documents SET status='reading' WHERE id=?", (doc_id,))
    conn.commit()
    t = threading.Thread(target=_read, args=(doc_id, path, mime), daemon=True)
    t.start()
    t.join(WAIT_SECS)


@app.get("/doc/{doc_id}/progress")
def doc_progress(doc_id: int):
    d = _doc(doc_id)
    return {"status": d["status"], "reading": doc_id in READING, "step": READING.get(doc_id)}


@app.get("/documents", response_class=HTMLResponse)
def documents(request: Request):
    docs = store.rows(conn, """SELECT d.*, (SELECT COUNT(*) FROM observations o WHERE o.doc_id=d.id) AS n_obs,
                               (SELECT COUNT(*) FROM items i WHERE i.doc_id=d.id) AS n_items
                               FROM documents d ORDER BY report_date DESC, id DESC""")
    return render(request, "documents.html", docs=docs, ai=llm.status())


def _doc(doc_id):
    d = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not d:
        raise HTTPException(404)
    return dict(d)


@app.get("/doc/{doc_id}")
def doc_file(doc_id: int):
    d = _doc(doc_id)
    return FileResponse(store.DOCS_DIR / d["stored_name"], media_type=d["mime"],
                        headers={"Content-Disposition": f'inline; filename="{d["filename"]}"'})


@app.get("/doc/{doc_id}/review", response_class=HTMLResponse)
def review(request: Request, doc_id: int):
    d = _doc(doc_id)
    obs = store.rows(conn, "SELECT * FROM observations WHERE doc_id=? ORDER BY page, id", doc_id)
    items = store.rows(conn, "SELECT * FROM items WHERE doc_id=? ORDER BY kind, id", doc_id)
    pages = store.rows(conn, "SELECT * FROM pages WHERE doc_id=? ORDER BY page", doc_id)
    return render(request, "review.html", d=d, obs=obs, items=items, pages=pages, ai=llm.status(),
                  reading=READING.get(doc_id) if doc_id in READING else None)


@app.post("/doc/{doc_id}/meta")
def doc_meta(doc_id: int, report_date: str = Form(...), title: str = Form(""), lab: str = Form("")):
    _doc(doc_id)
    rd = extract.parse_date(report_date) or report_date
    conn.execute("UPDATE documents SET report_date=?, title=?, lab=? WHERE id=?", (rd, title or None, lab or None, doc_id))
    conn.execute("UPDATE observations SET date=? WHERE doc_id=?", (rd, doc_id))
    conn.execute("UPDATE items SET date=? WHERE doc_id=?", (rd, doc_id))
    conn.commit()
    return _back(f"/doc/{doc_id}/review")


@app.post("/doc/{doc_id}/confirm")
def doc_confirm(doc_id: int):
    if _doc(doc_id).get("patient_check") == "mismatch":
        return _back(f"/doc/{doc_id}/review?error=First+choose+whether+this+report+belongs+in+your+portfolio")
    conn.execute("UPDATE observations SET verified=1 WHERE doc_id=?", (doc_id,))
    conn.execute("UPDATE documents SET status='done' WHERE id=?", (doc_id,))
    conn.commit()
    return _back("/")


@app.post("/doc/{doc_id}/reprocess")
def doc_reprocess(doc_id: int):
    d = _doc(doc_id)
    _start_reading(doc_id, str(store.DOCS_DIR / d["stored_name"]), d["mime"])
    return _back(f"/doc/{doc_id}/review")


@app.post("/doc/{doc_id}/patient")
def doc_patient(doc_id: int, decision: str = Form(...)):
    """The report names someone else: add it to this portfolio anyway, or remove it."""
    d = _doc(doc_id)
    if decision == "add":
        conn.execute("UPDATE documents SET patient_check='accepted' WHERE id=?", (doc_id,))
        conn.commit()
        return _back(f"/doc/{doc_id}/review?ok=Added+to+your+portfolio")
    if decision == "discard":
        _remove(d)
        return _back("/documents?ok=Report+removed;+nothing+from+it+was+added")
    raise HTTPException(400)


def _remove(d):
    conn.execute("DELETE FROM documents WHERE id=?", (d["id"],))
    conn.commit()
    (store.DOCS_DIR / d["stored_name"]).unlink(missing_ok=True)


@app.post("/doc/{doc_id}/delete")
def doc_delete(doc_id: int):
    d = _doc(doc_id)
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit()
    (store.DOCS_DIR / d["stored_name"]).unlink(missing_ok=True)
    return _back("/documents")


# ------------------------------------------------------------------ edits


def _obs_fields(test, value_text, unit, ref_text):
    name, loinc, _ = canonical(test)
    value = parse_number(value_text)
    low, high = parse_range(ref_text)
    return dict(test=test.strip(), canonical=name, loinc=loinc, value=value, value_text=value_text.strip(),
                unit=unit.strip() or None, ref_low=low, ref_high=high, ref_text=ref_text.strip() or None,
                flag=flag_for(value, low, high))


@app.post("/obs/{obs_id}")
def obs_update(obs_id: int, test: str = Form(...), value_text: str = Form(...), unit: str = Form(""),
               ref_text: str = Form(""), action: str = Form("save")):
    row = conn.execute("SELECT doc_id FROM observations WHERE id=?", (obs_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    if action == "delete":
        conn.execute("DELETE FROM observations WHERE id=?", (obs_id,))
    else:
        f = _obs_fields(test, value_text, unit, ref_text)
        conn.execute("""UPDATE observations SET test=:test, canonical=:canonical, loinc=:loinc, value=:value,
                        value_text=:value_text, unit=:unit, ref_low=:ref_low, ref_high=:ref_high, ref_text=:ref_text,
                        flag=:flag, verified=1 WHERE id=:id""", {**f, "id": obs_id})
    conn.commit()
    return _back(f"/doc/{row['doc_id']}/review#obs")


@app.post("/doc/{doc_id}/obs")
def obs_add(doc_id: int, test: str = Form(...), value_text: str = Form(...), unit: str = Form(""), ref_text: str = Form("")):
    d = _doc(doc_id)
    f = _obs_fields(test, value_text, unit, ref_text)
    conn.execute("""INSERT INTO observations (doc_id, test, canonical, loinc, value, value_text, unit, ref_low, ref_high,
                    ref_text, flag, date, verified) VALUES (:doc_id,:test,:canonical,:loinc,:value,:value_text,:unit,
                    :ref_low,:ref_high,:ref_text,:flag,:date,1)""", {**f, "doc_id": doc_id, "date": d["report_date"]})
    conn.commit()
    return _back(f"/doc/{doc_id}/review#obs")


@app.post("/item/{item_id}")
def item_update(item_id: int, name: str = Form(""), detail: str = Form(""), action: str = Form("save"),
                back: str = Form("")):
    row = conn.execute("SELECT doc_id FROM items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    if action == "delete":
        conn.execute("DELETE FROM items WHERE id=?", (item_id,))
    elif action == "stop":
        conn.execute("UPDATE items SET active=0 WHERE id=?", (item_id,))
    elif name.strip():
        conn.execute("UPDATE items SET name=?, detail=? WHERE id=?", (name.strip(), detail.strip() or None, item_id))
    conn.commit()
    if back == "portfolio" or not row["doc_id"]:
        return _back("/?ok=Removed#summary" if action == "delete" else "/#summary")
    return _back(f"/doc/{row['doc_id']}/review#items")


@app.post("/items")
def item_add(kind: str = Form(...), name: str = Form(...), detail: str = Form(""), doc_id: int | None = Form(None)):
    if kind not in ("condition", "medication", "allergy") or not name.strip():
        raise HTTPException(400)
    d = _doc(doc_id) if doc_id else None
    conn.execute("INSERT INTO items (doc_id, kind, name, detail, date) VALUES (?,?,?,?,?)",
                 (doc_id, kind, name.strip(), detail.strip() or None, d["report_date"] if d else date.today().isoformat()))
    conn.commit()
    return _back(f"/doc/{doc_id}/review#items" if doc_id else f"/?ok=Added+{kind}#summary")


@app.post("/items/{item_id}/stop")
def item_stop(item_id: int):
    conn.execute("UPDATE items SET active=0 WHERE id=?", (item_id,))
    conn.commit()
    return _back("/")


# ------------------------------------------------------------------ backup & share


@app.get("/share", response_class=HTMLResponse)
def share_page(request: Request, token: str | None = None):
    now = time.time()
    for t in [t for t, exp in SHARES.items() if exp < now]:
        SHARES.pop(t)
    link = qr = None
    if token and token in SHARES:
        link = f"http://{_lan_ip()}:{PORT}/s/{token}"
        qr = _qr_svg(link)
    lan = getattr(app.state, "lan", False)
    phone = None
    if lan:
        urls = _phone_urls()
        pair = _pair_code(request.query_params.get("pair") or "")
        row = _code_row(pair) if pair else None
        pair_ok = row is not None and not row["device_id"] and row["expires"] > now
        phone = {"urls": urls, "setup_qr": _qr_svg(urls["setup"]),
                 "pair_qr": _qr_svg(f"{urls['app']}#pair={pair}") if pair_ok else None,
                 "pair_code": pair if pair_ok else None, "pair_expires": row["expires"] if pair_ok else None}
    devices = store.rows(conn, "SELECT * FROM devices ORDER BY id DESC")
    return render(request, "share.html", link=link, qr=qr, lan=lan, phone=phone, devices=devices,
                  expires=SHARES.get(token), now=now, active=len(SHARES))


@app.post("/share/link")
def share_link(hours: int = Form(2)):
    token = secrets.token_urlsafe(16)
    SHARES[token] = time.time() + max(1, min(hours, 48)) * 3600
    return _back(f"/share?token={token}")


@app.post("/share/revoke")
def share_revoke():
    SHARES.clear()
    return _back("/share")


def _check_share(token):
    exp = SHARES.get(token)
    if not exp or exp < time.time():
        raise HTTPException(404, "This share link has expired.")


@app.get("/s/{token}", response_class=HTMLResponse)
def shared_view(request: Request, token: str):
    _check_share(token)
    return render(request, "portfolio.html", p=portfolio.build(conn), ai=None, share={"token": token})


@app.get("/s/{token}/doc/{doc_id}")
def shared_doc(token: str, doc_id: int):
    _check_share(token)
    return doc_file(doc_id)


@app.post("/share/file")
def share_file(request: Request, passphrase: str = Form(...)):
    if len(passphrase) < 6:
        return _back("/share?error=Use+a+passphrase+of+at+least+6+characters")
    css = (HERE / "static" / "style.css").read_text()
    html = templates.get_template("portfolio.html").render(
        request=request, p=portfolio.build(conn), ai=None, share={"file": True}, inline_css=css)
    out = vault.share_file(html, passphrase)
    name = f"medical-portfolio-{date.today().isoformat()}.html"
    return Response(out, media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/vault/export")
def vault_export(passphrase: str = Form(...)):
    if len(passphrase) < 8:
        return _back("/share?error=Use+a+passphrase+of+at+least+8+characters+for+backups")
    blob = vault.export_vault(store.DB_PATH, store.DOCS_DIR, passphrase)
    name = f"medport-{date.today().isoformat()}.medvault"
    return Response(blob, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/vault/import")
async def vault_import(file: UploadFile = File(...), passphrase: str = Form(...)):
    global conn
    blob = await file.read()
    try:
        conn.close()
        backup = vault.import_vault(blob, passphrase, store.DB_PATH, store.DOCS_DIR)
    except Exception as e:
        conn = store.connect()
        msg = "Wrong passphrase or damaged file." if e.__class__.__name__ == "InvalidTag" else str(e)
        return _back("/share?error=" + msg.replace(" ", "+"))
    conn = store.connect()
    return _back("/share?ok=Restored.+Your+previous+data+was+saved+in+" + backup.name)


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outgoing interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def _qr_svg(text: str) -> str:
    import qrcode
    import qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    return img.to_string(encoding="unicode")


# ------------------------------------------------------------------ phone app
#
# The laptop reads reports; a paired phone keeps an encrypted offline copy of the portfolio so it can be
# shown or sent at a clinic with no laptop or internet. The phone pulls a fresh copy whenever it is on
# the same Wi-Fi. Everything below /m/api/ needs the device token handed out at pairing.

CODE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # easy to type on a phone: no 0/O, 1/I/L
PAIR_FAILS = [0]


def _pair_code(raw: str) -> str:
    return "".join(c for c in raw.upper() if c in CODE_CHARS)
HTTPS_PORT = PORT + 1


def _phone_urls():
    ip = _lan_ip()
    return {"ip": ip, "setup": f"http://{ip}:{PORT}/phone", "app": f"https://{ip}:{HTTPS_PORT}/m/"}


def _tls_dir():
    return store.DATA_DIR / "tls"


def _code_row(code):
    return conn.execute("SELECT * FROM pair_codes WHERE code=?", (code,)).fetchone()


@app.post("/share/pair")
def share_pair():
    conn.execute("DELETE FROM pair_codes WHERE expires < ?", (time.time() - 3600,))
    code = "".join(secrets.choice(CODE_CHARS) for _ in range(8))
    conn.execute("INSERT INTO pair_codes (code, expires) VALUES (?,?)", (code, time.time() + 600))
    conn.commit()
    return _back(f"/share?pair={code}#phone")


@app.get("/share/pair/{code}")
def share_pair_status(code: str):
    """Polled by the Share page so it can say "paired" or "expired" instead of a stale QR code."""
    row = _code_row(_pair_code(code))
    if row and row["device_id"]:
        d = conn.execute("SELECT name FROM devices WHERE id=?", (row["device_id"],)).fetchone()
        return {"state": "paired", "name": d["name"] if d else "phone"}
    if not row or row["expires"] < time.time():
        return {"state": "expired"}
    return {"state": "waiting", "seconds": int(row["expires"] - time.time())}


@app.post("/share/devices/{device_id}/revoke")
def device_revoke(device_id: int):
    conn.execute("DELETE FROM devices WHERE id=?", (device_id,))
    conn.commit()
    return _back("/share?ok=Phone+removed.+It+keeps+its+last+copy+but+can+no+longer+sync#phone")


@app.get("/phone", response_class=HTMLResponse)
def phone_setup(request: Request):
    """Step 1 on the phone, over plain http: trust this computer's certificate, then open the app."""
    from . import tls
    return render(request, "phone_setup.html", urls=_phone_urls(), fingerprint=tls.ca_fingerprint(_tls_dir()),
                  share=True)


@app.get("/phone/medport-ca.crt")
def phone_ca():
    from . import tls
    return Response(tls.ca_der(_tls_dir()), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="medport-home-wifi.crt"'})


@app.get("/m/", response_class=HTMLResponse)
def phone_app(request: Request):
    return templates.TemplateResponse(request, "phone.html", {}, headers={"Cache-Control": "no-cache"})


@app.get("/m/sw.js")
def phone_sw():
    return FileResponse(HERE / "static" / "phone-sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/m/manifest.webmanifest")
def phone_manifest():
    return Response(json.dumps({
        "name": "MedPort", "short_name": "MedPort", "description": "Your medical portfolio, offline on your phone",
        "start_url": "/m/", "scope": "/m/", "display": "standalone", "background_color": "#f8fafc",
        "theme_color": "#0d9488",
        "icons": [{"src": f"/m/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any maskable"}
                  for n in (192, 512)],
    }), media_type="application/manifest+json")


@app.get("/m/icon-{size}.png")
def phone_icon(size: int):
    if size not in (180, 192, 512):
        raise HTTPException(404)
    return Response(_icon_png(size), media_type="image/png", headers={"Cache-Control": "max-age=86400"})


_ICONS: dict[int, bytes] = {}


def _icon_png(size: int) -> bytes:
    """The app icon, drawn from the same heart-and-pulse mark as the header logo."""
    if size not in _ICONS:
        import pymupdf
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24">'
               '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#14b8a6"/>'
               '<stop offset="1" stop-color="#0284c7"/></linearGradient></defs><rect width="24" height="24" fill="url(#g)"/>'
               '<g transform="translate(4.8 4.8) scale(.6)" fill="none" stroke="#fff" stroke-width="2.4" '
               'stroke-linecap="round" stroke-linejoin="round"><path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3'
               'c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7Z"/>'
               '<path d="M3.2 12h4.3l1.5-3 3 6 1.5-3h7.3"/></g></svg>')
        page = pymupdf.open(stream=svg.encode(), filetype="svg")[0]
        _ICONS[size] = page.get_pixmap(alpha=False).tobytes("png")
    return _ICONS[size]


def _device(request: Request) -> int:
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(401)
    row = conn.execute("SELECT id FROM devices WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row:
        raise HTTPException(401, "This phone is no longer paired. Pair it again from MedPort on your computer.")
    conn.execute("UPDATE devices SET last_seen=? WHERE id=?", (extract.now(), row["id"]))
    conn.commit()
    return row["id"]


@app.post("/m/api/pair")
async def phone_pair(request: Request):
    body = await request.json()
    raw = str(body.get("code") or "")
    code, name = _pair_code(raw), str(body.get("name") or "Phone").strip()[:40] or "Phone"
    row = _code_row(code)
    shown = f"{code[:4]}-{code[4:]}" if code else repr(raw)
    if not row or row["device_id"] or row["expires"] < time.time():
        why = ("already used" if row and row["device_id"] else "expired" if row else "not one MedPort made")
        print(f"Phone pairing refused: code {shown} is {why}.")
        PAIR_FAILS[0] += 1
        if PAIR_FAILS[0] >= 5:  # someone guessing: cancel every open code
            conn.execute("UPDATE pair_codes SET expires=0 WHERE device_id IS NULL")
            conn.commit()
            PAIR_FAILS[0] = 0
        msg = {"already used": "This pairing code was already used.",
               "expired": "This pairing code has expired (they last 10 minutes)."}.get(
            why, "MedPort doesn't recognise this pairing code. If you restarted MedPort or pressed Pair again, use the newest code.")
        raise HTTPException(403, msg + " Press Pair a phone on your computer and enter the new code.")
    PAIR_FAILS[0] = 0
    token = secrets.token_urlsafe(32)
    cur = conn.execute("INSERT INTO devices (name, token_hash, created_at) VALUES (?,?,?)",
                       (name, hashlib.sha256(token.encode()).hexdigest(), extract.now()))
    conn.execute("UPDATE pair_codes SET device_id=? WHERE code=?", (cur.lastrowid, code))
    conn.commit()
    print(f"Phone paired: {name}")
    return {"token": token}


@app.get("/m/api/snapshot")
def phone_snapshot(request: Request):
    """Everything the phone needs to work offline: the portfolio page (styles inlined, no links back to
    this computer) and the wrapper used to make a passphrase-locked file on the phone itself."""
    _device(request)
    p = portfolio.build(conn)
    css = (HERE / "static" / "style.css").read_text(encoding="utf-8")
    html = templates.get_template("portfolio.html").render(request=None, p=p, ai=None, share={"file": True}, inline_css=css)
    return {"html": html, "share_template": vault.SHARE_TEMPLATE, "rounds": vault.ROUNDS,
            "generated": extract.now(), "name": p["profile"].get("name") or "", "reports": len(p["docs"]),
            "to_review": p["unreviewed"] + len(p["held"])}


@app.post("/m/api/upload")
async def phone_upload(request: Request, files: list[UploadFile] = File(...)):
    _device(request)
    last = await _ingest(files)
    if last is None:
        raise HTTPException(400, "Only PDF or image files are supported.")
    return {"ok": True, "doc_id": last}
