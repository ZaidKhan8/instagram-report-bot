#!/usr/bin/env python3
"""
Harassment Evidence Kit
=======================

Turns a folder of screenshots plus a simple incident log into one clean,
dated, numbered PDF bundle that a police cyber cell / platform support team
can actually read — and a pre-filled complaint draft in Markdown.

Why this exists: when someone is harassing you online, what decides whether
anything happens is not how many reports get filed, it is whether you can hand
over a clear, ordered, verifiable record. This tool builds that record.

Commands
--------
  build    Read screenshots + incidents.csv -> PDF bundle, complaint draft,
           hash manifest, and a normalised index CSV.
  verify   Re-check the screenshots against a previously generated manifest,
           to prove nothing was altered after you filed.

Usage
-----
  python3 evidence_kit.py build --photos ./screenshots --incidents incidents.csv --out ./out --name "Your Name"
  python3 evidence_kit.py verify --manifest ./out/manifest_sha256.txt

No third-party packages required (Python 3.8+ standard library only).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
from typing import Dict, List, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdfwriter import PDF, ImageError  # noqa: E402

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif"}
INCIDENT_FIELDS = ["date", "time", "platform", "handle", "description", "file"]

PLATFORM_CHOICES = "Instagram / WhatsApp / Facebook / X / SMS / Email / other"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def non_cp1252(text: str) -> bool:
    """True if the text contains characters the built-in PDF fonts cannot show."""
    try:
        text.encode("cp1252")
        return False
    except UnicodeEncodeError:
        return True


def parse_date(value: str) -> Tuple[dt.date | None, str]:
    value = (value or "").strip()
    if not value:
        return None, "not recorded"
    formats = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d-%b-%Y")
    for fmt in formats:
        try:
            return dt.datetime.strptime(value, fmt).date(), value
        except ValueError:
            continue
    return None, value


def parse_time(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I %p"):
        try:
            return dt.datetime.strptime(value, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return value


def incident_sort_key(record: Dict[str, str]) -> Tuple[int, str]:
    """Chronological, with undated incidents pushed to the end."""
    date_obj, _ = parse_date(record.get("date", ""))
    if date_obj is None:
        return (1, record.get("_file", ""))
    return (0, f"{date_obj.isoformat()} {parse_time(record.get('time', ''))}")


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------


def read_incidents(path: str) -> Tuple[List[Dict[str, str]], List[str]]:
    warnings: List[str] = []
    if not path or not os.path.exists(path):
        return [], [f"Incident log not found at '{path}' — bundle will only contain screenshots."]

    with open(path, newline="", encoding="utf-8-sig") as fh:
        # Lines starting with '#' are guidance, not incidents. The shipped
        # template is full of them, and they must never end up in someone's
        # complaint as if they were real events.
        kept = [line for line in fh if not line.lstrip().startswith("#")]
        text = "".join(kept)

    if not text.strip():
        return [], [
            f"'{path}' has no incident rows yet — it still contains only guidance lines. "
            "Add one line per incident: date, time, platform, handle, description, file."
        ]

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], [f"'{path}' is empty."]

    header_map = {}
    for field in reader.fieldnames:
        key = (field or "").strip().lower().replace(" ", "_")
        aliases = {
            "date": "date",
            "datetime": "date",
            "time": "time",
            "platform": "platform",
            "app": "platform",
            "handle": "handle",
            "username": "handle",
            "account": "handle",
            "description": "description",
            "what_happened": "description",
            "message": "description",
            "file": "file",
            "screenshot": "file",
            "image": "file",
            "filename": "file",
        }
        if key in aliases:
            header_map[field] = aliases[key]

    records: List[Dict[str, str]] = []
    for row in reader:
        if not any((v or "").strip() for v in row.values()):
            continue
        record = {name: "" for name in INCIDENT_FIELDS}
        for raw_key, value in row.items():
            mapped = header_map.get(raw_key)
            if mapped:
                record[mapped] = (value or "").strip()
        if record["description"].upper().startswith("EXAMPLE ROW"):
            warnings.append(
                "Incidents log still contains EXAMPLE rows — delete them before filing, "
                "otherwise your complaint will list events that never happened."
            )
        if not record["date"]:
            warnings.append(
                f"An incident entry (file: {record['file'] or 'n/a'}) has no date — "
                "undated entries carry much less weight; please add the date."
            )
        records.append(record)

    if not records:
        warnings.append(
            "The incident log has a header but no incident rows — every screenshot will be "
            "listed as 'not described'. Add one line per incident (date, time, platform, "
            "handle, description, file) so the bundle tells a story."
        )

    return records, warnings


def discover_images(photos_dir: str) -> List[str]:
    if not photos_dir or not os.path.isdir(photos_dir):
        return []
    found: List[str] = []
    for root, dirs, files in os.walk(photos_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if os.path.splitext(name)[1].lower() in IMAGE_EXTS:
                found.append(os.path.join(root, name))
    return sorted(found)


def match_file(reference: str, images: Sequence[str], photos_dir: str) -> str | None:
    reference = (reference or "").strip().strip('"')
    if not reference:
        return None
    wanted = os.path.basename(reference).lower()
    for image in images:
        if os.path.basename(image).lower() == wanted:
            return image
    for image in images:
        if reference.lower() in image.lower():
            return image
    return None


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------


def write_manifest(paths: Sequence[str], out_path: str) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for path in paths:
        rows.append((os.path.basename(path), sha256_file(path)))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("# SHA-256 manifest — generated by harassment-evidence-kit\n")
        fh.write(f"# Generated: {dt.datetime.now().isoformat(timespec='seconds')}\n")
        fh.write("# Verify with:  sha256sum -c manifest_sha256.txt   (or: python3 evidence_kit.py verify)\n")
        for name, digest in rows:
            fh.write(f"{digest}  {name}\n")
    return rows


def write_index_csv(records: Sequence[Dict[str, str]], out_path: str) -> None:
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["no", "date", "time", "platform", "handle", "description", "file", "sha256"])
        writer.writeheader()
        for i, record in enumerate(records, start=1):
            writer.writerow(
                {
                    "no": i,
                    "date": record.get("date", ""),
                    "time": record.get("time", ""),
                    "platform": record.get("platform", ""),
                    "handle": record.get("handle", ""),
                    "description": record.get("description", ""),
                    "file": os.path.basename(record.get("_file", "")),
                    "sha256": record.get("_sha256", ""),
                }
            )


COMPLAINT_TEMPLATE = """# Complaint draft — online harassment / cyber stalking

> **Read this first.** This is a draft for *you* to edit, print, sign and submit.
> It is not legal advice. The police decide which sections apply; the ones below
> are the usual ones for repeated online harassment in India. Correct anything
> that does not match your facts before you file.

**To:** {authority}
**Subject:** Complaint regarding repeated online harassment{from_phrase} — request for registration of FIR / action

---

## 1. Complainant

| | |
|---|---|
| Name | {name} |
| Address | {address} |
| Phone | {phone} |
| Email | {email} |
| ID proof | {id_proof} |

## 2. Person complained against

| | |
|---|---|
| Name (if known) | {accused_name} |
| Handle / profile(s) | {handles} |
| Platform(s) | {platforms} |
| Known address | {accused_address} |

## 3. Facts of the case

I state that from **{first_date}** onwards, the person named above has repeatedly
harassed me online. The incidents, in order, are:

{incident_table}

Each incident listed above is supported by a screenshot. The screenshots are
enclosed in the attached evidence bundle (`evidence_bundle.pdf`), each printed on
its own page and numbered. SHA-256 hashes of every screenshot image are listed in
Annexure A (`manifest_sha256.txt`) so that the integrity of the images can be
verified at any time.

Describe here, in your own words, the effect this has had on you — fear, anxiety,
sleep, work, safety, threats, whether the person has contacted your family or
employer, whether they have threatened to post private material, and whether you
have had to change your phone number, accounts or routine:

{impact}

## 4. Steps already taken

- Reported the account(s)/messages to the platform: {platform_reports}
- Blocked the account(s): {blocked}
- Any earlier complaint to police: {prior_complaint}

## 5. Legal provisions I rely upon

*(Indicative only. The Investigating Officer will apply the correct sections.)*

- **Bharatiya Nyaya Sanhita, 2023 — s. 78**: stalking (following, contacting or
  attempting to contact a person repeatedly despite clear indication of disinterest;
  includes monitoring a person's use of the internet, email or other electronic
  communication).
- **BNS — s. 351**: criminal intimidation (threat of injury to person, reputation
  or property, including by electronic means).
- **BNS — s. 352**: intentional insult with intent to provoke a breach of the peace.
- **BNS — s. 356**: defamation (if false statements were published).
- **BNS — s. 77 / s. 79**: voyeurism / insulting the modesty of a woman
  *(if applicable)*.
- **Information Technology Act, 2000 — s. 66C** (identity theft), **s. 66D**
  (cheating by personation using a computer resource), **s. 66E** (violation of
  privacy), **s. 67 / 67A** (publishing obscene or sexually explicit material).
- **BNS — s. 3(5)**: common intention *(if more than one person is involved)*.

## 6. Request

I respectfully request that:

1. this complaint be registered and investigated;
2. the platform be directed to preserve and produce the account, message, login
   and IP records for the handles named above (they are deleted quickly — please
   send a preservation request at the earliest);
3. appropriate protection be provided to me and my family, if required;
4. a copy of the complaint/FIR and the acknowledgement be given to me.

## 7. Evidence enclosed

1. **Annexure A** — `evidence_bundle.pdf`: {page_count} page evidence bundle —
   {incident_count} numbered screenshots, each with date, time, platform, handle
   and description; followed by the hash manifest.
2. **Annexure B** — `manifest_sha256.txt`: SHA-256 hash of every original image file.
3. **Annexure C** — `incidents.csv`: the same incident list as a spreadsheet.
4. Original image files on {original_media} (offered for forensic examination).

## 8. Declaration

I declare that the facts stated above are true to the best of my knowledge and
belief, and that the documents enclosed are genuine copies of what I saw and
saved at the times stated.

**Place:** {place}
**Date:** {today}

**Signature:** _______________________
({name})

---

### Filing routes (India)

| Route | Where | Best for |
|---|---|---|
| National Cyber Crime Reporting Portal | https://cybercrime.gov.in | All cyber harassment; choose "Women/Child related crime" where applicable. Gives you an acknowledgement number. |
| Police station / cyber cell | Walk in with this printout + a USB or your phone | Urgent threats, stalking, sextortion, doxxing |
| Dial 112 | Emergency | Immediate danger |
| Dial 1930 | Financial fraud helpline | Money was taken |
| Women Helpline 181 / 1091 | Phone | Women in distress, immediate support |
| National Commission for Women | http://ncwapps.nic.in/onlinecomplaintsv2/ | Harassment not being acted upon locally |
| Platform support | In-app report + the platform's India grievance officer | Getting the account removed or content taken down |

**Keep:** the acknowledgement number / FIR copy, and do not delete or edit the
original screenshots — the hash manifest proves they are unchanged.
"""


PLATFORM_REPORT_TEMPLATE = """WHAT TO PASTE INTO THE PLATFORM'S "REPORT" FORM
================================================================
Instagram/X/Facebook report forms give you a small text box. Keep it short,
specific and factual — vague text gets auto-closed. Copy the block below and
fill in the blanks.

--------------------------------------------------------------
I am reporting repeated harassment from the account "{handle}".

This account has been sending me abusive and unwanted messages since
{first_date}. I asked them to stop on {first_date} and I have blocked the
account, but they created new accounts and continued
({handle_list}).

This is a repeated pattern, not a single message. I have kept dated
screenshots of every incident and can provide them, along with SHA-256 hashes
proving the images are unaltered, if requested.

I am asking for the account to be reviewed for harassment and removed so that
this stops, and for the associated messages to be preserved in case I need to
file a police complaint.

Reported by: {name}
Contact: {email} {phone}
Incident dates: {dates}
--------------------------------------------------------------

Reporting tips that actually matter
-----------------------------------
1. Report the exact MESSAGE or POST, not only the profile. The message carries
   the context the reviewer needs; a bare profile report is often dismissed.
2. If the option exists, prefer the category "Harassment or bullying" or
   "I'm being targeted by someone" over generic "spam".
3. Block the account. If new accounts keep coming, restrict as well — and
   report each new account separately, listing the previous handles as proof
   of a pattern.
4. If a threat of violence was made, do not wait on the platform: file at
   cybercrime.gov.in (or dial 112 / 1930) the same day, and tell the platform
   in the report that a police complaint has been filed.
5. Raise a ticket with the platform's India Grievance Officer after ~48 hours
   if the report is closed without action. Mention the report reference number.
6. Never delete the original screenshots. Do not edit, crop or "clean" them.
   If you must mark something up, use a copy and keep the original pristine.
"""


def write_platform_report(records: Sequence[Dict[str, str]], meta: Dict[str, str], out_path: str) -> None:
    handles = sorted({r.get("handle", "") for r in records if r.get("handle")})
    # Prefer a username-style handle ("@name") as the primary one; a phone number
    # in the same column should not become the headline of the report.
    usernames = [h for h in handles if not re.fullmatch(r"[+\d\s\-()]{4,}", h)]
    primary = (usernames or handles or ["_______________________"])[0]
    dated = [r for r in records if parse_date(r.get("date", ""))[0] is not None]
    dates = sorted({str(parse_date(r.get("date", ""))[1]) for r in dated})
    first_date = dates[0] if dates else "_______________________"
    text = PLATFORM_REPORT_TEMPLATE.format(
        handle=primary,
        handle_list=", ".join(handles) or "_______________________",
        first_date=first_date,
        dates=", ".join(dates) or "_______________________",
        name=meta.get("name", ""),
        email=meta.get("email", ""),
        phone=meta.get("phone", ""),
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)


def build_incident_table(records: Sequence[Dict[str, str]]) -> str:
    lines = ["| # | Date | Time | Platform | Handle | What happened | Screenshot |", "|---|---|---|---|---|---|---|"]
    for i, record in enumerate(records, start=1):
        _, raw_date = parse_date(record.get("date", ""))
        cells = [
            str(i),
            raw_date or "not recorded",
            record.get("time", "") or "-",
            record.get("platform", "") or "-",
            record.get("handle", "") or "-",
            record.get("description", "") or "-",
            os.path.basename(record.get("_file", "")) or "-",
        ]
        lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    if len(lines) == 2:
        lines.append("| - | - | - | - | - | No dated incidents were logged. | - |")
    return "\n".join(lines)


def write_complaint_draft(records: Sequence[Dict[str, str]], meta: Dict[str, str], page_count: int, out_path: str) -> None:
    handles = sorted({r.get("handle", "") for r in records if r.get("handle")})
    platforms = sorted({r.get("platform", "") for r in records if r.get("platform")})
    dated = [r for r in records if parse_date(r.get("date", ""))[0] is not None]
    first_date = ""
    if dated:
        first_date = str(parse_date(min(dated, key=incident_sort_key).get("date", ""))[1])

    text = COMPLAINT_TEMPLATE.format(
        authority=meta.get("authority", "The Station House Officer / In-charge, Cyber Crime Cell"),
        from_phrase=" and criminal intimidation" if meta.get("threats") == "yes" else "",
        name=meta.get("name", "_______________________"),
        address=meta.get("address", "_______________________"),
        phone=meta.get("phone", "_______________________"),
        email=meta.get("email", "_______________________"),
        id_proof=meta.get("id_proof", "_______________________"),
        accused_name=meta.get("accused_name", "Not known / to be identified from the account records"),
        handles=", ".join(handles) or "_______________________",
        platforms=", ".join(platforms) or PLATFORM_CHOICES,
        accused_address=meta.get("accused_address", "Not known"),
        first_date=first_date or "_______________________",
        incident_table=build_incident_table(records),
        impact=meta.get("impact", "\n\n*(Write 4-6 lines here. This is the paragraph the officer actually reads.)*\n"),
        platform_reports=meta.get("platform_reports", "Yes — in-app reports filed on the dates shown above"),
        blocked=meta.get("blocked", "Yes (they created new accounts after blocking)"),
        prior_complaint=meta.get("prior_complaint", "None"),
        page_count=page_count,
        incident_count=len(records),
        original_media=meta.get("original_media", "my phone/computer"),
        place=meta.get("place", "_______________________"),
        today=dt.date.today().strftime("%d-%m-%Y"),
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def build(args: argparse.Namespace) -> int:
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    warnings: List[str] = []
    records, log_warnings = read_incidents(args.incidents)
    warnings.extend(log_warnings)

    images = discover_images(args.photos)
    if not images:
        print(f"error: no images found in '{args.photos}' (looked for: {', '.join(sorted(IMAGE_EXTS))})", file=sys.stderr)
        return 2

    matched_paths: List[str] = []
    for record in records:
        path = match_file(record.get("file", ""), images, args.photos)
        if path:
            record["_file"] = path
        elif record.get("file"):
            warnings.append(f"Screenshot '{record['file']}' referenced in the log was not found on disk.")
        else:
            warnings.append(f"Incident on {record.get('date') or 'unknown date'} has no screenshot attached.")

    # Attach every screenshot that the log did not mention — better to include it
    # than to leave evidence out of the bundle.
    mentioned = {r.get("_file") for r in records if r.get("_file")}
    for image in images:
        if image in mentioned:
            continue
        records.append(
            {
                "date": "",
                "time": "",
                "platform": "",
                "handle": "",
                "description": "Not described in incidents.csv — add a description for this screenshot.",
                "file": os.path.basename(image),
                "_file": image,
            }
        )

    records.sort(key=incident_sort_key)

    # hashes
    for record in records:
        if record.get("_file"):
            record["_sha256"] = sha256_file(record["_file"])

    manifest_path = os.path.join(out_dir, "manifest_sha256.txt")
    manifest_rows = write_manifest([r["_file"] for r in records if r.get("_file")], manifest_path)

    # Never overwrite the user's own incident log if --out points at its folder.
    index_path = os.path.join(out_dir, "incidents.csv")
    if args.incidents and os.path.abspath(index_path) == os.path.abspath(args.incidents):
        index_path = os.path.join(out_dir, "incidents_normalised.csv")
        warnings.append(
            "Your incident log sits in the output folder, so the normalised copy was written as "
            f"'{os.path.basename(index_path)}' — your original was left untouched."
        )
    write_index_csv(records, index_path)

    meta = load_case_meta(args)

    # ---- PDF ----
    pdf = PDF()
    with_images = [r for r in records if r.get("_file")]
    total_pages = 3 + len(with_images) + 1  # cover + guide + index + images + hashes

    cover_lines = [
        f"Prepared by: {meta.get('name', '')}",
        f"Contact: {meta.get('phone', '')}  {meta.get('email', '')}",
        f"Prepared on: {dt.date.today().strftime('%d %B %Y')}",
        "",
        f"Incidents recorded: {len(records)}",
        f"Screenshots enclosed: {len(with_images)}",
        f"Period covered: {period_text(records)}",
        "",
        "Platforms involved:",
        "  " + ("; ".join(sorted({r['platform'] for r in records if r.get('platform')})) or "not recorded"),
        "Accounts involved:",
        "  " + (", ".join(sorted({r['handle'] for r in records if r.get('handle')})) or "not recorded"),
        "",
        "How to use this bundle:",
        "1. Print it, or submit it as a PDF with your complaint.",
        "2. File online at cybercrime.gov.in, or walk into your local police "
        "station / cyber cell with a copy.",
        "3. Carry the ORIGINAL image files (not re-saved copies). Keep "
        "manifest_sha256.txt with them — it proves the images were not altered "
        "after this bundle was made.",
        "4. Edit and sign complaint_draft.md before submitting it.",
    ]
    pdf.text_page(
        "EVIDENCE BUNDLE",
        cover_lines,
        title_size=26.0,
        body_size=10.5,
        footer="Generated by harassment-evidence-kit · no third-party services used · all data stayed on this device",
    )

    pdf.text_page(
        "About this bundle",
        [
            "This bundle contains one page per incident. Each page shows the screenshot "
            "as it was saved, together with the date, time, platform, account handle and a "
            "short description of what it shows.",
            "",
            "Evidence rule to keep in mind: a screenshot on its own is weak because it can "
            "be edited. A screenshot presented together with (a) an exact date and time, "
            "(b) the account handle visible in the image, and (c) a SHA-256 hash of the "
            "original file, is much harder to dispute. That is why every image in this "
            "bundle is listed with its hash in Annexure A.",
            "",
            "Do not crop, re-save, brighten or otherwise edit the originals. If you need to "
            "highlight something, take a separate copy for the annotation and keep the "
            "original untouched.",
            "",
            "Annexures",
            "  Annexure A — manifest_sha256.txt — SHA-256 hash of each original image",
            "  Annexure B — incidents.csv — the incident list as a spreadsheet",
            "  Annexure C — this bundle — numbered screenshots and the evidence index",
            "",
            "Platform reporting, in short",
            "  Report the individual message/post (not just the profile) — that is what "
            "carries the context: open the chat or post, long-press or tap the three dots, "
            "choose Report, and pick the closest category (harassment, bullying, "
            "impersonation, nudity).",
            "  Block the account, and also restrict it if blocking provokes more accounts.",
            "  If the person keeps making new accounts, report each new account and keep "
            "adding the incidents to this bundle — a repeated pattern is what turns a "
            "'one message' complaint into a harassment case.",
        ],
        title_size=18.0,
        body_size=10.5,
        footer="Screenshots are reproduced unmodified; captions come from your incident log.",
    )

    index_rows = []
    for i, record in enumerate(records, start=1):
        _, raw_date = parse_date(record.get("date", ""))
        index_rows.append(
            [
                f"{i}",
                raw_date or "?",
                record.get("time", "") or "-",
                record.get("platform", "") or "-",
                record.get("handle", "") or "-",
                record.get("description", "") or "(no description)",
                os.path.splitext(os.path.basename(record.get("_file", "")))[0] or "-",
            ]
        )
    warnings.extend(
        pdf.table_flow(
            "Annexure C — Index of incidents",
            ["#", "Date", "Time", "Platform", "Handle", "What it shows", "Image file"],
            index_rows,
            col_widths=[0.4, 1.1, 0.6, 1.1, 1.5, 4.6, 1.9],
            footer="Each entry has its own page after this index.",
        )
    )

    for i, record in enumerate(with_images, start=1):
        _, raw_date = parse_date(record.get("date", ""))
        short_hash = record.get("_sha256", "")[:16] + "…" if record.get("_sha256") else "n/a"
        caption = [
            ("Incident", f"{i} of {len(with_images)}"),
            ("Date", f"{raw_date or 'not recorded'}{('  ' + record['time']) if record.get('time') else ''}"),
            ("Platform", record.get("platform", "") or "not recorded"),
            ("Account", record.get("handle", "") or "not recorded"),
            ("Description", record.get("description", "") or "(no description given)"),
            ("Original file", os.path.basename(record.get("_file", ""))),
            ("SHA-256 (first 16)", short_hash),
        ]
        try:
            pdf.image_page(record["_file"], caption, footer=f"Annexure C · incident {i} · {os.path.basename(record['_file'])}")
        except ImageError as exc:
            warnings.append(f"Could not embed '{os.path.basename(record['_file'])}': {exc}")

    hash_rows = [[name, digest[:32] + "…"] for name, digest in manifest_rows]
    warnings.extend(
        pdf.table_flow(
            "Annexure A — Integrity manifest (SHA-256)",
            ["Image file", "SHA-256 (first 32 characters)"],
            hash_rows,
            col_widths=[2.6, 4.4],
            footer="Full 64-character hashes are in manifest_sha256.txt.",
        )
    )

    bundle_path = os.path.join(out_dir, "evidence_bundle.pdf")
    pdf.save(bundle_path, title="Evidence bundle — online harassment complaint", author=meta.get("name", ""))

    page_count = len(pdf._page_obj_nums)
    write_complaint_draft(records, meta, page_count, os.path.join(out_dir, "complaint_draft.md"))
    write_platform_report(records, meta, os.path.join(out_dir, "platform_report.txt"))

    # ---- console report ----
    print(f"Built: {bundle_path}  ({page_count} pages, {len(with_images)} screenshots)")
    print(f"       {os.path.join(out_dir, 'complaint_draft.md')}")
    print(f"       {os.path.join(out_dir, 'platform_report.txt')}")
    print(f"       {manifest_path}")
    print(f"       {index_path}")

    unreadable = [r for r in records if not r.get("_file")]
    if unreadable:
        warnings.append(f"{len(unreadable)} incident(s) have no screenshot attached.")
    if any(non_cp1252(r.get("description", "")) for r in records):
        warnings.append(
            "Some descriptions contain non-Latin characters (e.g. Hindi). The PDF's built-in "
            "fonts cannot display those, so they appear as '?'. The complaint_draft.md keeps "
            "them intact — write those descriptions in English/transliteration for the PDF, or "
            "paste the Hindi text into the draft."
        )
    if warnings:
        print("\nWarnings:")
        for item in dict.fromkeys(warnings):
            print(f"  - {item}")
    return 0


def period_text(records: Sequence[Dict[str, str]]) -> str:
    dated = sorted(
        (str(parse_date(r.get("date", ""))[1]) for r in records if parse_date(r.get("date", ""))[0]),
    )
    if not dated:
        return "no dates recorded"
    if len(dated) == 1:
        return dated[0]
    return f"{dated[0]} to {dated[-1]}"


# ---------------------------------------------------------------------------
# case meta
# ---------------------------------------------------------------------------


def load_case_meta(args: argparse.Namespace) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    if args.case and os.path.exists(args.case):
        with open(args.case, encoding="utf-8") as fh:
            meta.update(json.load(fh))
    for key in ("name", "phone", "email", "address", "id_proof", "accused_name", "accused_address",
                "impact", "place", "authority", "prior_complaint", "platform_reports", "threats"):
        value = getattr(args, key, None)
        if value:
            meta[key] = value
    meta.setdefault("name", "")
    return meta


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def verify(args: argparse.Namespace) -> int:
    manifest = args.manifest
    base_dir = args.photos or os.path.dirname(os.path.abspath(manifest))
    if not os.path.exists(manifest):
        print(f"error: manifest not found: {manifest}", file=sys.stderr)
        return 2

    checked = ok = missing = changed = 0
    with open(manifest, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            digest, name = parts[0], parts[1].strip()
            checked += 1
            path = None
            for candidate in (os.path.join(base_dir, name), os.path.join(os.path.dirname(os.path.abspath(manifest)), name), name):
                if os.path.exists(candidate):
                    path = candidate
                    break
            if not path:
                print(f"MISSING  {name}")
                missing += 1
                continue
            actual = sha256_file(path)
            if actual == digest:
                ok += 1
            else:
                print(f"CHANGED  {name}\n         expected {digest}\n         actual   {actual}")
                changed += 1

    print(f"\n{checked} file(s) checked · {ok} unchanged · {changed} changed · {missing} missing")
    if changed or missing:
        print("If a file changed or is missing, re-export the screenshot from the source and note "
              "the reason in your complaint — do not present an altered image.")
        return 1
    print("All images match the manifest: the bundle is unaltered.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evidence_kit.py",
        description="Build a court-readable evidence bundle from harassment screenshots.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "example:\n"
            "  python3 evidence_kit.py build --photos ./screenshots --incidents incidents.csv "
            "--out ./out --name \"Your Name\" --phone \"+91...\" --place \"Lucknow\"\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="build the PDF bundle + complaint draft")
    b.add_argument("--photos", required=True, help="folder containing the screenshots")
    b.add_argument("--incidents", default="incidents.csv", help="incident log CSV (default: incidents.csv)")
    b.add_argument("--out", default="out", help="output folder (default: out)")
    b.add_argument("--case", default="case.json", help="optional case.json with complainant details")
    b.add_argument("--name", help="your name")
    b.add_argument("--phone", help="your phone number")
    b.add_argument("--email", help="your email")
    b.add_argument("--address", help="your address")
    b.add_argument("--id_proof", help="ID proof you will attach (e.g. Aadhaar last 4 digits)")
    b.add_argument("--accused_name", help="name of the person harassing you, if known")
    b.add_argument("--accused_address", help="their address, if known")
    b.add_argument("--impact", help="one or two lines on how this has affected you")
    b.add_argument("--place", help="place of signing")
    b.add_argument("--authority", help="who you are addressing (default: SHO / Cyber Crime Cell)")
    b.add_argument("--prior_complaint", help="any earlier police complaint, or 'None'")
    b.add_argument("--platform_reports", help="what you already reported to the platform")
    b.add_argument("--threats", choices=["yes", "no"], help="were there threats of harm?")
    b.set_defaults(func=build)

    v = sub.add_parser("verify", help="re-check images against a manifest")
    v.add_argument("--manifest", required=True, help="path to manifest_sha256.txt")
    v.add_argument("--photos", help="folder with the images (default: next to the manifest)")
    v.set_defaults(func=verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
