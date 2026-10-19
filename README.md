# MedPort

A private medical portfolio that runs on your own computer. Drop in lab reports and discharge
summaries; a local open-weight model reads them into a one-page summary you can hand to any doctor:
allergies, conditions, current medications, out-of-range results, and trend charts across labs and years.
Your reports never leave your machine.

## Install (Windows)

1. Download `MedPort-windows.zip` from the latest release, unzip it, and double-click `MedPort.exe`.
   Your browser opens MedPort; keep the black window open while you use it. (Not code-signed yet: if
   SmartScreen appears, choose More info -> Run anyway.)
2. Optional but recommended: install [Ollama](https://ollama.com/download) and run `ollama pull qwen2.5:3b`,
   so a local open model reads your reports. MedPort shows these steps on first run.

Your data stays in `%USERPROFILE%\.medport`. Nothing is uploaded anywhere.

## Run it from source

```bash
pip install -r requirements.txt

# 1. Install Ollama (https://ollama.com) and pull a small open model
ollama pull qwen2.5:3b          # text extraction (~2 GB)
ollama pull qwen2.5vl:3b        # optional: reads phone photos and scanned PDFs

# 2. Start MedPort
python -m medport               # open http://127.0.0.1:8765
```

If the default model isn't pulled, MedPort uses any other text model Ollama has (e.g. `llama3.2`, `gemma3`).
The model is the main extractor: long pages are sent in chunks, it falls back to plain JSON mode on older
Ollama versions, and rows the rule parser finds that the model skipped are added back.

No model yet? It still runs, using a built-in rule parser for common lab layouts. Start Ollama later
and press **Read again** on any report.

Reading runs in the background: the review page shows which page the model is on and fills in when it is done. Thinking models (gemma4, qwen3) have thinking switched off, which makes them about 3x faster on a CPU.

Try it with fictional data: `python samples/make_samples.py`, then upload the PDFs in `samples/`.

| Setting | Default | |
|---|---|---|
| `MEDPORT_MODEL` | `qwen2.5:3b` | any Ollama model: `gemma3:4b`, `llama3.2:3b`, `medgemma:4b`… |
| `MEDPORT_VISION_MODEL` | `qwen2.5vl:3b` | used as OCR for photos and scans |
| `MEDPORT_DATA` | `~/.medport` | where the database and original files live |
| `MEDPORT_DATE_ORDER` | `DMY` | set `MDY` for US-style dates |
| `OLLAMA_HOST` | `127.0.0.1:11434` | |
| `MEDPORT_TIMEOUT` | `180` | seconds per model call before that page falls back to the rule parser |
| `MEDPORT_BUDGET` | `600` | seconds of model time per report; later pages use the rule parser |
| `--port` | `8765` | with `--lan`, the phone app uses the next port (8766) over HTTPS |

## Phone app (offline at the clinic)

The laptop reads reports; your phone keeps an encrypted copy of the portfolio, so at a hospital you can
show it or send it to a doctor with no laptop and no internet. It updates whenever the phone is on your home Wi-Fi.

1. Start MedPort with `py -m medport --lan` (Windows asks once: allow **Private networks** only).
2. On the laptop open **Share & backup → Phone app** and scan the first QR code with the phone. That page walks you
   through trusting MedPort's home-Wi-Fi certificate (once) and adding the app to the home screen.
3. Press **Pair a phone** and scan the second QR code, or type its 8-letter code into the app. Choose a PIN.

On the phone: **Send to doctor** makes a passphrase-locked file on the phone itself and opens the share sheet
(WhatsApp, email); **Print / PDF** prints or saves it; **Add report** sends a photo or PDF to the laptop when you're home.

Why HTTPS with a home-made certificate: phones only run installable offline apps and in-browser encryption over
HTTPS. MedPort creates a small certificate authority restricted (X.509 name constraints) to private addresses
(10.x, 172.16-31.x, 192.168.x) and localhost, so it can't be used to impersonate any public website. It lives in
`~/.medport/tls/`. The phone copy is AES-256-GCM encrypted with a key derived from your PIN (PBKDF2, 310k rounds);
only paired phones can sync, and you can remove a phone on the laptop at any time.

## Public demo

`python -m medport --demo --host 0.0.0.0` serves a read-only copy with the fictional sample reports and the rule
parser only: uploads, backups, sharing and the phone app are switched off, and the data is wiped on restart. It is
meant for a hosted preview (it honours the `PORT` variable, as on Render); the real app is meant to run on your
own computer.

## Build the Windows download

```
py -m pip install -r requirements.txt pyinstaller
py build_windows.py          # -> dist/MedPort-windows.zip
```

## How it works

1. **Read**: PyMuPDF pulls text from PDFs, rebuilding table rows from word positions. Pages with no text
   (scans) and photos go to the local vision model, or Tesseract if installed.
2. **Extract**: each page goes to the local model with a JSON schema (Ollama structured output, temperature 0)
   asking only for what is printed: lab values, diagnoses, medications, allergies. No interpretation.
3. **Ground**: every value and name the model returns is checked against the page text and dropped if it
   isn't there. Small models occasionally invent values; this catches it.
4. **Normalize**: a lookup table maps ~40 common tests ("SGPT", "ALT (SGPT)", "Alanine transaminase") to one
   name and LOINC code, so results from different labs line up on one trend line. Flags come from the
   report's own reference range.
5. **Review**: you see the original next to the extracted values, fix anything, and confirm.
   Unchecked values are marked "unreviewed" on the portfolio.
6. **Share**: print / save as PDF, a passphrase-locked HTML file (AES-256-GCM, opens in any browser with no
   app), or a QR code a doctor's phone can scan on the same Wi-Fi (`python -m medport --lan`). In LAN mode only
   the expiring share link is reachable from other devices; everything else stays localhost-only.
7. **Backup / move devices**: an encrypted `.medvault` file you can keep in your own iCloud/Drive/USB.

## Why open-weight models

- **Privacy is the product.** Medical records are the data people least want on someone else's server. A
  local model is what makes "never leaves your laptop" true rather than a policy promise. Works offline.
- **Cost.** Zero per-report inference cost, so a one-time or low monthly price works.
- **Swap and tune.** One env var switches models; a medical-tuned model (MedGemma) can be A/B tested, and
  the extraction task is narrow enough to fine-tune a 1–3B model on corrected reviews later.
- **No lock-in.** No API key, no pricing change can break it.

## Not medical advice

MedPort organizes what your reports already say. It does not interpret results, diagnose or recommend
treatment. Always check values against the original report.

## Tests

```bash
pip install pytest httpx reportlab
python -m pytest -q
```
