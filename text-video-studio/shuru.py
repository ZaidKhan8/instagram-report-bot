#!/usr/bin/env python3
"""
shuru.py — ek command me sab kuch.

    python3 shuru.py

Ye check karta hai ki Pillow, ffmpeg, fonts aur HarfBuzz (Hindi shaping) hai ya
nahi — jo missing ho use install karne ka tareeka batata hai — aur phir web
studio chalu kar deta hai.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main() -> int:
    print()
    print("=" * 68)
    print("  TEXT -> VIDEO STUDIO")
    print("=" * 68)
    print()

    # 1. Python
    print(f"  python  : {sys.version.split()[0]}")

    # 2. Pillow
    try:
        import PIL

        print(f"  pillow  : {PIL.__version__}")
    except ImportError:
        print("  pillow  : NAHI HAI")
        print("     Termux : pkg install python-pillow")
        print("     PC     : pip install pillow")
        return 1

    # 3. ffmpeg
    import render as R

    ffmpeg = R.find_ffmpeg()
    print(f"  ffmpeg  : {ffmpeg or 'NAHI MILA'}")
    if not ffmpeg:
        print("     Termux : pkg install ffmpeg")
        print("     PC     : pip install imageio-ffmpeg")
        return 1

    # 4. fonts
    fonts = R.list_fonts()
    if not fonts:
        print("  fonts   : nahi mile — abhi laa raha hoon...")
        subprocess.run([sys.executable, os.path.join(HERE, "get_fonts.py")], check=False)
        fonts = R.list_fonts()
    print(f"  fonts   : {len(fonts)} file(s)")
    if not any("devanagari" in f.lower() for f in fonts):
        print("     Hindi font nahi mila -> python3 get_fonts.py")

    # 5. Hindi shaping
    try:
        import textshape

        available, reason = textshape.shaping_available()
    except Exception as exc:
        available, reason = False, str(exc)
    if available:
        print("  hindi   : theek (HarfBuzz shaping)")
    else:
        print(f"  hindi   : shaping nahi mili ({reason})")
        print("     Termux : pkg install harfbuzz")
        print("     PC     : pip install --upgrade pillow")
        print("     (isse Hindi ke akshar galat dikh sakte hain, baaki sab chalega)")

    # 6. espeak (optional)
    espeak = R.find_espeak()
    print(f"  voice   : {espeak or 'nahi hai (voiceover optional) -> Termux: pkg install espeak'}")

    print()
    print("  Studio chalu kar raha hoon — band karne ke liye Ctrl+C")
    print()
    return subprocess.call([sys.executable, os.path.join(HERE, "studio.py"), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
