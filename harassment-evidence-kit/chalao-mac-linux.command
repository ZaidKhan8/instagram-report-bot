#!/bin/sh
# Mac / Linux: is file ko double-click kariye, ya terminal me: sh chalao-mac-linux.command
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then exec python3 shuru.py "$@"; fi
if command -v python >/dev/null 2>&1; then exec python shuru.py "$@"; fi
echo "Python 3 nahi mila. python.org se install kariye, phir ye file dobara chalaiye."
read -r _
