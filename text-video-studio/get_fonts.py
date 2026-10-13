#!/usr/bin/env python3
"""
get_fonts.py — Hindi aur English ke liye Noto fonts download karta hai.

Fonts Google ke "Noto" project se aate hain (SIL Open Font License — free,
personal aur commercial dono ke liye). Ye script unhe GitHub se laati hai aur
fonts/ folder me rakh deti hai. Ek hi baar chalana kaafi hai.

    python3 get_fonts.py

Agar aapke paas pehle se koi .ttf file hai, to use seedha fonts/ folder me
daal dijiye — ye script kuch overwrite nahi karti.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")

FONTS = [
    ("hinted/ttf/NotoSans/NotoSans-Regular.ttf", "NotoSans-Regular.ttf"),
    ("hinted/ttf/NotoSans/NotoSans-Bold.ttf", "NotoSans-Bold.ttf"),
    ("hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Regular.ttf", "NotoSansDevanagari-Regular.ttf"),
    ("hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf", "NotoSansDevanagari-Bold.ttf"),
]

API = "https://api.github.com/repos/notofonts/noto-fonts/contents/{path}"


def download(repo_path: str, filename: str) -> bool:
    target = os.path.join(FONT_DIR, filename)
    if os.path.exists(target) and os.path.getsize(target) > 1000:
        print(f"  ok       {filename} (pehle se maujood hai)")
        return True

    url = API.format(path=repo_path)
    request = urllib.request.Request(url, headers={
        "User-Agent": "text-video-studio/1.0",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"  FAIL     {filename}: GitHub ne {exc.code} diya")
        if exc.code == 403:
            print("           (ghante ki free limit khatam ho gayi ho sakti hai — thodi der baad try kariye)")
        return False
    except Exception as exc:
        print(f"  FAIL     {filename}: {exc}")
        return False

    content = payload.get("content")
    if not content:
        print(f"  FAIL     {filename}: file GitHub par nahi mili")
        return False

    try:
        blob = base64.b64decode(content)
    except Exception as exc:
        print(f"  FAIL     {filename}: decode nahi hua ({exc})")
        return False

    os.makedirs(FONT_DIR, exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(blob)
    print(f"  mil gaya {filename} ({len(blob) / 1024:.0f} KB)")
    return True


def main() -> int:
    print("Fonts laa raha hoon (Noto — SIL Open Font License)...")
    ok = sum(1 for repo_path, name in FONTS if download(repo_path, name))
    print()
    if ok == len(FONTS):
        print(f"Sab set hai. Fonts yahan hain: {FONT_DIR}")
    elif ok:
        print(f"{ok}/{len(FONTS)} fonts mile. Baaki ke bina bhi kaam chal jaayega.")
    else:
        print("Koi font nahi mila. Internet check kariye, ya koi bhi .ttf file")
        print(f"khud download karke is folder me daal dijiye: {FONT_DIR}")
        return 1

    try:
        from PIL import ImageFont

        sample = os.path.join(FONT_DIR, "NotoSansDevanagari-Bold.ttf")
        if os.path.exists(sample):
            ImageFont.truetype(sample, 40)
            print("Test: Hindi font theek se khul raha hai.")
    except Exception as exc:  # pragma: no cover
        print(f"Font test me dikkat: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
