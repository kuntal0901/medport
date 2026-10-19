"""Encrypted vault export/import and the passphrase-locked share file.

Both use PBKDF2-SHA256 (310k rounds) + AES-256-GCM, which the browser's
WebCrypto can also do, so a doctor can open a share file with no app.
"""
import base64
import io
import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

MAGIC = b"MEDVAULT1"
ROUNDS = 310_000


def _key(passphrase: str, salt: bytes) -> bytes:
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ROUNDS).derive(passphrase.encode())


def encrypt(data: bytes, passphrase: str) -> tuple[bytes, bytes, bytes]:
    salt, iv = os.urandom(16), os.urandom(12)
    return salt, iv, AESGCM(_key(passphrase, salt)).encrypt(iv, data, None)


def decrypt(salt: bytes, iv: bytes, ct: bytes, passphrase: str) -> bytes:
    return AESGCM(_key(passphrase, salt)).decrypt(iv, ct, None)


# ------------------------------------------------------------------ vault


def export_vault(db_path: Path, docs_dir: Path, passphrase: str) -> bytes:
    buf = io.BytesIO()
    src = sqlite3.connect(db_path)
    tmp = sqlite3.connect(":memory:")
    src.backup(tmp)
    src.close()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("medport.sql", "\n".join(tmp.iterdump()))
        for f in sorted(Path(docs_dir).glob("*")):
            z.write(f, "documents/" + f.name)
        z.writestr("meta.json", json.dumps({"exported_at": datetime.now().isoformat(timespec="seconds"), "format": 1}))
    tmp.close()
    salt, iv, ct = encrypt(buf.getvalue(), passphrase)
    return MAGIC + salt + iv + ct


def import_vault(blob: bytes, passphrase: str, db_path: Path, docs_dir: Path) -> Path:
    """Replaces local data with the vault's. The current data is kept in a backup folder first."""
    if not blob.startswith(MAGIC):
        raise ValueError("Not a .medvault file")
    body = blob[len(MAGIC):]
    plain = decrypt(body[:16], body[16:28], body[28:], passphrase)  # raises InvalidTag on wrong passphrase
    data_dir = Path(db_path).parent
    backup = data_dir / ("backup-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir(parents=True)
    if Path(db_path).exists():
        shutil.copy2(db_path, backup / Path(db_path).name)
    if Path(docs_dir).exists():
        shutil.copytree(docs_dir, backup / "documents")
    with zipfile.ZipFile(io.BytesIO(plain)) as z:
        sql = z.read("medport.sql").decode()
        # empty the database in place rather than deleting the file: on Windows a file that any
        # connection still has open can't be deleted
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = OFF")
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            conn.execute(f'DROP TABLE "{name}"')
        conn.commit()
        conn.executescript(sql)
        conn.close()
        shutil.rmtree(docs_dir, ignore_errors=True)
        Path(docs_dir).mkdir(parents=True)
        for name in z.namelist():
            if name.startswith("documents/") and not name.endswith("/"):
                target = Path(docs_dir) / Path(name).name  # flatten: no path traversal from the zip
                target.write_bytes(z.read(name))
    return backup


# ------------------------------------------------------------------ share file

SHARE_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Medical portfolio (locked)</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f7f9;color:#1d2330;display:grid;place-items:center;min-height:100vh;margin:0}
form{background:#fff;padding:28px;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);max-width:360px;width:calc(100% - 32px)}
h1{font-size:18px;margin:0 0 6px}p{color:#5b6475;font-size:14px;margin:0 0 16px}
input{width:100%;box-sizing:border-box;padding:10px;font-size:16px;border:1px solid #c9ced8;border-radius:8px}
button{margin-top:12px;width:100%;padding:10px;font-size:16px;border:0;border-radius:8px;background:#1f6feb;color:#fff;cursor:pointer}
#err{color:#b42318;font-size:14px;margin-top:10px;min-height:1em}
</style></head><body>
<form id="f"><h1>Medical portfolio</h1><p>This file is encrypted. Enter the passphrase the patient gave you. Nothing is sent anywhere; it opens in this browser.</p>
<input id="p" type="password" autocomplete="off" placeholder="Passphrase" autofocus><button>Open</button><div id="err"></div></form>
<script>
const D={data};
const b=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
document.getElementById('f').onsubmit=async e=>{e.preventDefault();
 document.getElementById('err').textContent='Unlocking…';
 try{const pass=new TextEncoder().encode(document.getElementById('p').value);
  const base=await crypto.subtle.importKey('raw',pass,'PBKDF2',false,['deriveKey']);
  const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt:b(D.salt),iterations:D.rounds,hash:'SHA-256'},base,{name:'AES-GCM',length:256},false,['decrypt']);
  const pt=await crypto.subtle.decrypt({name:'AES-GCM',iv:b(D.iv)},key,b(D.ct));
  const html=new TextDecoder().decode(pt);document.open();document.write(html);document.close();
 }catch(_){document.getElementById('err').textContent='Wrong passphrase.';}};
</script></body></html>"""


def share_file(html: str, passphrase: str) -> str:
    salt, iv, ct = encrypt(html.encode(), passphrase)
    data = json.dumps({"salt": base64.b64encode(salt).decode(), "iv": base64.b64encode(iv).decode(),
                       "ct": base64.b64encode(ct).decode(), "rounds": ROUNDS})
    return SHARE_TEMPLATE.replace("{data}", data)
