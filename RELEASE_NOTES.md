# MedPort v1.0.0: your medical portfolio, read by a local open model

MedPort turns a pile of lab reports and discharge summaries into a one-page medical portfolio you can hand to any
doctor: allergies, conditions, current medications, out-of-range results and trends across labs and years.
It runs on your own computer. Your reports never leave it.

## Download

**Windows:** `MedPort-windows.zip`. Unzip it and double-click `MedPort.exe`; your browser opens MedPort.
The app isn't code-signed yet, so if SmartScreen appears choose *More info → Run anyway*.

For the AI reader (optional, free): install [Ollama](https://ollama.com/download) and run `ollama pull qwen2.5:3b`.
Without it MedPort still works, using its built-in rule parser for common lab layouts.

## What's in it

- **Reads reports locally** with an open-weight model through Ollama (Qwen 2.5, Gemma, Llama; your choice). Every value
  the model returns is checked against the page and dropped if it isn't printed there.
- **Review side by side** with the original report, fix anything, confirm.
- **Is this your report?** MedPort compares the name, sex and date of birth on each report with your profile and
  asks before adding someone else's report to your portfolio.
- **Your own entries:** add or remove medications, conditions and allergies yourself, labelled "Added by you".
- **Share with a doctor:** print/PDF, a passphrase-locked file that opens in any browser, or an expiring QR link.
- **Phone app:** an encrypted offline copy on your phone (PIN-protected), so at the clinic you can show or send your
  portfolio with no laptop or internet. It syncs on your home Wi-Fi (`MedPort.exe --lan`).
- **Encrypted backup** you can keep anywhere.

## Why open models

Medical records are the data people least want on someone else's server. A local open model is what makes
"never leaves your laptop" true rather than a policy promise; it costs nothing per report, works offline, and
you can swap models or fine-tune for your country's report formats.

## Known limits

- Reading a page takes about 20 to 60 seconds on a laptop CPU; reading runs in the background with progress shown.
- The phone app needs a one-time certificate step on the phone (explained on the setup page).
- Windows only for the packaged download; macOS and Linux can run from source (`python -m medport`).
