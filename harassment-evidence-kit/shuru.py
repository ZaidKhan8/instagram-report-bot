#!/usr/bin/env python3
"""
Shuru karo — start here.

Pehli baar chalane par ye aapke liye kaam karne ka folder bana deta hai:

    mera-case/
      screenshots/     <- apne screenshots isme daaliye
      incidents.csv    <- date, time, platform, handle, description, file
      case.json        <- apna naam, phone, address, impact
      out/             <- banne wala evidence bundle yahan aayega

Doosri baar chalane par ye seedha bundle bana deta hai — yaani screenshots
daalne ke baad bas `python3 shuru.py` dobara chalaiye.

Usage:
    python3 shuru.py                  # set up, or build if already set up
    python3 shuru.py --folder mycase  # use a different folder name
    python3 shuru.py --rebuild        # force a rebuild
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KIT = os.path.join(HERE, "evidence_kit.py")

INCIDENTS_TEMPLATE = """date,time,platform,handle,description,file
# ---------------------------------------------------------------------------
# UPAR WALI LINE KO CHHEDIYE MAT. Iske NEECHE har incident ki ek line likhiye.
# '#' se shuru hone wali lines computer padhta nahi — sirf samjhane ke liye hain.
#
#   date        -> YYYY-MM-DD  (ya DD-MM-YYYY / DD/MM/YYYY)   [zaroori hai]
#   time        -> 24-ghante ka, jaise 22:14                  [khaali chhod sakte hain]
#   platform    -> Instagram / WhatsApp / Facebook / SMS / Email
#   handle      -> jis account se message aaya (@username ya phone number)
#   description -> 1 line me kya hua
#   file        -> screenshot ka file name (jo screenshots/ folder me rakhi hai)
#
# DHYAN: description me comma (,) aaye to poori description double quotes me likhiye.
#
# Neeche do example hain — '#' hata kar apna data bhariye:
#
# 2026-09-02,22:14,Instagram,@their_handle,"Rokne ke baad bhi gaali galoch wale messages",screenshot_1.png
# 2026-09-05,09:41,WhatsApp,+91-XXXXXXXXXX,"Dhamki di ki pareshan karta rahega",screenshot_2.png
# 2026-09-07,18:02,Instagram,@their_handle2,"Block karne ke baad naya account banaya",screenshot_3.png
#
# AB YAHAN APNI LINES LIKHIYE:
"""

CASE_TEMPLATE = """{
  "name": "YAHAN APNA POORA NAAM LIKHIYE",
  "phone": "+91-XXXXXXXXXX",
  "email": "aapka@email.com",
  "address": "Ghar ka address, sheher, rajya, PIN",
  "id_proof": "Aadhaar (last 4 digits) / Passport / DL",
  "accused_name": "Not known",
  "accused_address": "Not known",
  "impact": "YAHAN 4-6 LINE LIKHIYE: is harassment ka aap par kya asar hua - dar, neend, kaam, ghar walon ko dhamki, phone number badalna pada, etc.",
  "place": "Aapka sheher",
  "authority": "The Station House Officer / In-charge, Cyber Crime Cell",
  "prior_complaint": "None",
  "platform_reports": "YAHAN LIKHIYE: kis date ko kya report kiya aur block kiya",
  "threats": "yes"
}
"""

README_TEMPLATE = """YAHAN SE SHURU KARIYE
======================

1. Apne saare screenshots is folder ke andar 'screenshots' folder me daaliye.
   Screenshots me dhyan rakhiye ki:
     - jis account se message aaya uska naam/handle dikh raha ho
     - date/time dikh raha ho
     - sabse achha: poora screen (cropped bubble nahi)
   ORIGINAL screenshot hi rakhiye - crop/edit mat kariye.

2. 'incidents.csv' ko Notepad me kholiye aur apni incidents likhiye.
   Example lines hain usme - unhe delete karke apna data daaliye.
   (Notepad me: File > Save As > encoding me 'UTF-8' chuniye)

3. 'case.json' me apna naam, phone, address aur 'impact' likhiye.
   Naam me capital letters aur sateek likhiye.

4. Phir wapas upar wale folder me jaakar chalaiye:
       python3 shuru.py
   (Windows par: python shuru.py)

5. Ban jaayega: mera-case/out/evidence_bundle.pdf
     - evidence_bundle.pdf   -> print kariye, thane/cyber cell le jaaiye
     - complaint_draft.md    -> isko padhkar edit kariye, sign kariye, file kariye
     - platform_report.txt   -> Instagram ke Report box me paste kariye
     - manifest_sha256.txt   -> isko sambhal kar rakhiye (proof hai)

6. Agar aap ya koi aur turant khatre me hai to 112 par call kariye.
   Cyber complaint: https://cybercrime.gov.in  |  Money fraud: 1930
"""


def say(text: str = "") -> None:
    print(text, flush=True)


def setup(folder: str) -> int:
    os.makedirs(os.path.join(folder, "screenshots"), exist_ok=True)
    with open(os.path.join(folder, "incidents.csv"), "w", encoding="utf-8") as fh:
        fh.write(INCIDENTS_TEMPLATE)
    with open(os.path.join(folder, "case.json"), "w", encoding="utf-8") as fh:
        fh.write(CASE_TEMPLATE)
    with open(os.path.join(folder, "SHURU-KARIYE.txt"), "w", encoding="utf-8") as fh:
        fh.write(README_TEMPLATE)

    say("")
    say("=" * 68)
    say("  FOLDER BAN GAYA:  " + folder)
    say("=" * 68)
    say("")
    say("  Ab ye 3 kaam kariye:")
    say("")
    say("   1. Apne screenshots daaliye is folder me:")
    say("        " + os.path.join(folder, "screenshots"))
    say("")
    say("   2. Incidents likhiye (Notepad se kholiye):")
    say("        " + os.path.join(folder, "incidents.csv"))
    say("      - example lines hain usme, unhe delete karke apni likhiye")
    say("      - date likhna zaroori hai (YYYY-MM-DD)")
    say("")
    say("   3. Apna naam/phone/address is file me bhariye:")
    say("        " + os.path.join(folder, "case.json"))
    say("")
    display = os.path.relpath(folder, HERE) if folder.startswith(HERE) else folder
    say("  Phir ye command dobara chalaiye:")
    say("        python3 shuru.py" + ("" if display == "mera-case" else f" --folder \"{display}\""))
    say("")
    say("  Poori detail ke liye:  " + os.path.join(folder, "SHURU-KARIYE.txt"))
    say("")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up a case folder and build the evidence bundle.")
    parser.add_argument("--folder", default="mera-case", help="case folder name (default: mera-case)")
    parser.add_argument("--rebuild", action="store_true", help="rebuild even if output already exists")
    args = parser.parse_args()

    folder = args.folder if os.path.isabs(args.folder) else os.path.join(HERE, args.folder)
    shots = os.path.join(folder, "screenshots")
    incidents = os.path.join(folder, "incidents.csv")
    case = os.path.join(folder, "case.json")
    out = os.path.join(folder, "out")

    if not os.path.isdir(folder):
        return setup(folder)

    images = []
    if os.path.isdir(shots):
        images = [
            f for f in os.listdir(shots)
            if os.path.splitext(f)[1].lower() in {".jpg", ".jpeg", ".png", ".bmp", ".gif"}
        ]

    if not images:
        say("")
        say("  FOLDER MIL GAYA, PAR USME KOI SCREENSHOT NAHI HAI: " + shots)
        say("  Screenshots daaliye us folder me, phir 'python3 shuru.py' dobara chalaiye.")
        say("")
        return 1

    bundle = os.path.join(out, "evidence_bundle.pdf")
    if os.path.exists(bundle) and not args.rebuild:
        say("")
        say("  Bundle pehle se bana hua hai: " + bundle)
        say("  Naye screenshots/incidents jode hain? To dobara banane ke liye chalaiye:")
        say("        python3 shuru.py --rebuild")
        say("")

    say("")
    say(f"  {len(images)} screenshot mile. Bundle bana raha hoon...")
    say("")

    cmd = [sys.executable, KIT, "build", "--photos", shots, "--incidents", incidents,
           "--case", case, "--out", out]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        say("")
        say("  Kuch galat hua. Upar ki command ka output dekhiye.")
        return result.returncode

    say("")
    say("=" * 68)
    say("  HO GAYA! Is folder me sab kuch hai:  " + out)
    say("=" * 68)
    say("")
    say("   evidence_bundle.pdf   <- PRINT kariye / PDF bhejiye police ko")
    say("   complaint_draft.md    <- padhkar edit kariye, sign kariye, file kariye")
    say("   platform_report.txt   <- Instagram ke Report box me paste kariye")
    say("   manifest_sha256.txt   <- sambhal kar rakhiye (originals ka proof)")
    say("")
    say("   Cyber complaint: https://cybercrime.gov.in  |  Emergency: 112")
    say("   Poore steps: " + os.path.join(folder, "SHURU-KARIYE.txt"))
    say("")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
