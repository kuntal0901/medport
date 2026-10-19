MedPort · as built on 19 Oct 2026

# How MedPort fits together

A private medical portfolio that runs on your laptop. A local open-weight model reads your reports, the laptop keeps the only full copy, and everything you share goes out encrypted or as an expiring link.

Model on localhost Data in \~/.medport Phone copy PIN-encrypted No cloud server, no API key

## The pieces and who talks to whom

Green is your laptop. The browser, the app, Ollama and the data folder all talk over 127.0.0.1. Other devices can reach the laptop only on home Wi-Fi, only while you run `--lan`, and only on the share link, the phone app and the phone setup page. Anything else gets a 403.

## A report, from upload to portfolio

Steps 03 to 09 run on the laptop in a background thread, and the model is reached only at 127.0.0.1. All the slow work happens before anything is written, so a long read never locks the database. "Read again" repeats from step 03 and remembers a Yes you gave at step 09.

## Five ways the portfolio leaves the laptop

The phone's Send to doctor builds the same locked file on the phone itself, from the offline copy, and hands it to WhatsApp or email. So the hospital visit needs neither the laptop nor the internet.

## How the phone gets its copy

1. With `--lan`, MedPort makes its own small certificate authority and signs a certificate for the laptop's Wi-Fi address. The authority is limited to private addresses (192.168.x.x, 10.x.x.x, 172.16–31.x.x), so it can't vouch for any public website. The phone trusts it once.
2. You press **Pair a phone** on the laptop. It shows an 8-letter code and QR that lasts 10 minutes and survives a restart. Five wrong guesses cancel every open code.
3. The phone sends the code and gets a device token. The laptop keeps only a hash of the token, and you can remove a phone at any time.
4. The phone fetches a snapshot: the portfolio page with styles inlined and no links back to the laptop. It stores the snapshot and the token in IndexedDB, encrypted with AES-256-GCM under a key derived from your PIN (PBKDF2-SHA256, 310,000 rounds). The key exists only in memory while the app is unlocked.
5. At home it re-syncs by itself. **Add report** uploads a photo or PDF to the laptop, and the laptop reads it like any other report.

## What stays on-device

| Data | Where it lives | Leaves the device? |
| --- | --- | --- |
| Original reports | Laptop, `~/.medport` | No, except inside a backup you make |
| Database: values, items, profile | Laptop, `medport.db` (SQLite) | No, except inside a backup you make |
| Page text sent to the model | Ollama at 127.0.0.1:11434 | No, it never leaves the laptop |
| Phone copy | Phone IndexedDB, encrypted with your PIN | No |
| Phone token | Hash on the laptop, encrypted on the phone | No |
| HTTPS certificate authority | Laptop, `~/.medport/tls`, owner-only | Only the public certificate, to your phone |
| QR share link | Served by the laptop on home Wi-Fi | To whoever scans it, until it expires or you stop it |
| Locked file or backup | Wherever you send or keep it | Yes, as ciphertext; you tell the passphrase separately |