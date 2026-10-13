# Harassment Evidence Kit

Turn screenshots of online harassment plus a simple incident log into **one clean,
numbered PDF bundle that a police cyber cell or platform support team can actually
act on** — with a pre-filled complaint draft and proof that the images were not
altered afterwards.

Everything runs on your own device. No accounts, no cloud, no uploads, no
third-party packages (Python 3.8+ standard library only).

```
screenshots/  +  incidents.csv  ──►  evidence_bundle.pdf
                                     complaint_draft.md      (police / cyber cell)
                                     platform_report.txt     (platform report box)
                                     manifest_sha256.txt     (integrity proof)
                                     incidents.csv           (normalised index)
```

---

## Why this exists

Mass-reporting someone's account with fake accounts does **not** work: platforms
discard report floods as coordinated abuse, and running such a bot puts *you* in
legal trouble instead. What actually gets a harasser actioned is an ordered,
dated, verifiable record handed to the platform and to the police. This tool
builds that record from what you already have on your phone.

---

## Install

```bash
# nothing to install
python3 --version        # 3.8 or newer
```

## Use it

```bash
cd harassment-evidence-kit
python3 evidence_kit.py build \
    --photos ./screenshots \
    --incidents incidents.csv \
    --out ./out \
    --name "Your Name" \
    --phone "+91-XXXXXXXXXX" \
    --place "Lucknow"
```

Then print `out/evidence_bundle.pdf`, edit and sign `out/complaint_draft.md`, and
copy the text from `out/platform_report.txt` into the platform's report form.

### Step 1 — collect the screenshots

Put every screenshot in one folder. Keep the originals exactly as your phone
saved them: **do not crop, re-save, brighten or annotate them** — you would
destroy their value. If you want to circle something, make a copy for that.

A screenshot is strong evidence only when it shows the **handle** of the person
and the **date/time**. Prefer full-screen screenshots (chat header visible) over
cropped message bubbles.

### Step 2 — write the incident log

Create `incidents.csv` (or copy `samples/incidents.csv`):

```csv
date,time,platform,handle,description,file
2026-09-02,22:14,Instagram,@their_handle,"Abusive messages after I asked them to stop.",shot_01.png
2026-09-05,09:41,WhatsApp,+91-XXXXXXXXXX,"Threat: 'I will not leave you alone'.",shot_02.png
2026-09-07,18:02,Instagram,@their_backup,"Second account created after I blocked the first.",shot_03.png
```

- `date` — `YYYY-MM-DD` (also accepted: `DD-MM-YYYY`, `DD/MM/YYYY`, `DD.MM.YYYY`).
  **Undated entries are worth very little; date everything.**
- `file` — the image file name. If it is misspelled, the tool tells you. A
  screenshot missing from the log is still included, just flagged.
- Column names are forgiving: `date/datetime`, `handle/username/account`,
  `description/what_happened`, `file/screenshot/image`.

### Step 3 — build

```bash
python3 evidence_kit.py build --photos ./screenshots --incidents incidents.csv --out ./out
```

Optional `--case case.json` (see `samples/case.json`) fills your name, address,
impact statement and the accused's details into the complaint. Any option can be
given on the command line instead: `--name`, `--phone`, `--email`, `--address`,
`--accused_name`, `--impact`, `--place`, `--authority`, `--threats yes|no`.

### Step 4 — file it

| Route | Where | Use for |
|---|---|---|
| Cyber Crime Portal | https://cybercrime.gov.in | Any online harassment; gives an acknowledgement number |
| Police station / cyber cell | Walk in with the printout | Threats, stalking, sextortion, doxxing |
| Dial 112 | Phone | Immediate danger |
| Dial 1930 | Phone | Money was taken |
| Women Helpline 181 / 1091 | Phone | Women in distress |
| NCW | http://ncwapps.nic.in/onlinecomplaintsv2/ | Local police not acting |
| Platform report + India Grievance Officer | In-app, then the platform's grievance portal | Removal of the account/content |

Ask the police to send the platform a **preservation request** early — message,
login and IP records are deleted quickly. That is exactly what the complaint
draft asks for.

### Step 5 — keep the proof

```bash
python3 evidence_kit.py verify --manifest ./out/manifest_sha256.txt --photos ./screenshots
```

Output: `3 file(s) checked · 3 unchanged · 0 changed · 0 missing`. Anyone can
re-run this to confirm the images you filed are byte-for-byte the images you
saved. Keep the originals and the manifest together; do not delete them after
filing.

---

## What the PDF contains

1. **Cover** — complainant, contact, period covered, platforms, handles, how to use it.
2. **About this bundle** — why date + handle + hash is what makes a screenshot hold up, plus platform reporting steps.
3. **Annexure C — index of incidents** — one numbered row per incident.
4. **One page per incident** — the screenshot (unmodified, scaled to fit), captioned with incident number, date, time, platform, handle, description, file name and its SHA-256.
5. **Annexure A — integrity manifest** — every image with its hash.

## Commands

```
python3 evidence_kit.py build  --photos DIR [--incidents FILE] [--out DIR] [--case FILE] [meta flags]
python3 evidence_kit.py verify --manifest FILE [--photos DIR]
```

## Supported screenshots

JPEG (embedded without re-compression, byte-identical) · PNG (RGB, RGBA with
transparency preserved, greyscale, palette) · BMP · GIF.

Not supported, with a clear error message telling you what to do: interlaced or
16-bit PNGs. Open the image in your phone's editor or Preview and re-save as a
normal PNG/JPG.

## Tests

```bash
python3 selftest.py      # generates data, builds a PDF, re-parses it byte by byte
```

20 checks: xref offsets point at real objects, every stream's `/Length` matches
its bytes, every page has `/Contents` and a `/Parent`, embedded images keep the
source dimensions, a tampered image is detected by `verify`, and a missing
incident log does not crash the build.

## Notes and limits

- **Captions are Latin-script only.** The PDF uses the built-in Helvetica fonts,
  so a Hindi/Tamil/Urdu description would print as `?`. The `.md` and `.txt`
  outputs keep your text intact — write those descriptions in English or
  transliteration for the PDF, or paste the local-language text into the draft.
- Captions are intentionally plain: the tool never edits or crops your evidence.
- This is a documentation tool, not legal advice, and it cannot make a case on
  its own — an accurate timeline in your own words matters just as much.
- Nothing here is uploaded anywhere, no telemetry, no network calls.

## Safety

If you are in danger right now: **112**. If you are being blackmailed with
intimate images, do not pay, do not delete the chats — screenshot them, file at
cybercrime.gov.in (there is a specific category), and tell the police the
platform must preserve the records.

## Layout

```
harassment-evidence-kit/
├── evidence_kit.py        # CLI: build + verify, complaint and report text
├── pdfwriter.py           # dependency-free PDF writer + image decoder
├── selftest.py            # 20 structural/behavioural checks
├── samples/               # clearly-marked demo data to try it safely
│   ├── make_samples.py    # regenerates the demo PNGs offline
│   ├── incidents.csv      # example log
│   └── case.json          # example complainant details
└── out/                   # generated output (not committed)
```
