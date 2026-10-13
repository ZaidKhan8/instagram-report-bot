#!/usr/bin/env python3
"""
Generate demo data for the evidence kit: three mock "screenshot" PNGs plus an
example incidents.csv and case.json.

These images are obviously marked SAMPLE — they exist so you can see the output
format and check the tool works before you drop your real screenshots in.
Regenerating them is fully offline: zlib + struct only.

  python3 samples/make_samples.py            # writes into samples/
"""

from __future__ import annotations

import os
import struct
import zlib

W, H = 540, 960
HERE = os.path.dirname(os.path.abspath(__file__))

# 5x7 bitmap font, one string per row, '#' = ink
FONT = {
    " ": ["00000"] * 7,
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10111", "10001", "10001", "01111"],
    "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"],
    "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"],
    "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "11011", "10001"],
    "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"],
    "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00110", "01000", "10000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "@": ["01110", "10001", "10111", "10101", "10111", "10000", "01110"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"],
    "!": ["00100", "00100", "00100", "00100", "00100", "00000", "00100"],
    "+": ["00000", "00100", "00100", "11111", "00100", "00100", "00000"],
    "?": ["01110", "10001", "00001", "00110", "00100", "00000", "00100"],
    ",": ["00000", "00000", "00000", "00000", "01100", "00100", "01000"],
}


def new_canvas(bg=(250, 250, 252)):
    pixels = bytearray()
    for _ in range(W * H):
        pixels.extend(bg)
    return pixels


def fill_rect(pixels, x0, y0, x1, y1, color):
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x1), min(H, y1)
    for y in range(y0, y1):
        row = y * W * 3
        for x in range(x0, x1):
            off = row + x * 3
            pixels[off : off + 3] = bytes(color)


def fill_round_rect(pixels, x0, y0, x1, y1, color, radius=14):
    fill_rect(pixels, x0 + radius, y0, x1 - radius, y1, color)
    fill_rect(pixels, x0, y0 + radius, x1, y1 - radius, color)
    for cx, cy, sx, sy in ((x0 + radius, y0 + radius, -1, -1), (x1 - radius, y0 + radius, 1, -1),
                           (x0 + radius, y1 - radius, -1, 1), (x1 - radius, y1 - radius, 1, 1)):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    off = ((cy + dy) * W + (cx + dx)) * 3
                    pixels[off : off + 3] = bytes(color)


def draw_text(pixels, x, y, text, scale=2, color=(20, 20, 25), spacing=1):
    cx = x
    for char in text.upper():
        glyph = FONT.get(char, FONT["?"])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == "#":
                    fill_rect(
                        pixels,
                        cx + gx * scale,
                        y + gy * scale,
                        cx + (gx + 1) * scale,
                        y + (gy + 1) * scale,
                        color,
                    )
        cx += (5 + spacing) * scale


def text_width(text, scale=2, spacing=1):
    return len(text) * (5 + spacing) * scale


def write_png(path, pixels):
    raw = bytearray()
    stride = W * 3
    for y in range(H):
        raw.append(0)  # filter: None
        raw.extend(pixels[y * stride : (y + 1) * stride])
    def chunk(ctype, data):
        body = struct.pack(">I", len(data)) + ctype + data
        return body + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    blob = b"\x89PNG\r\n\x1a\n"
    blob += chunk(b"IHDR", header)
    blob += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    blob += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(blob)


def base_screen(header_text, handle, body_lines, accent):
    pixels = new_canvas()
    fill_rect(pixels, 0, 0, W, 96, (32, 34, 40))
    draw_text(pixels, 16, 20, header_text, scale=2, color=(255, 255, 255))
    draw_text(pixels, 16, 52, handle, scale=2, color=(180, 190, 200))
    y = 130
    for line, incoming in body_lines:
        scale = 2
        bubble_w = min(W - 60, text_width(line, scale) + 40)
        x0 = 20 if incoming else W - 20 - bubble_w
        colour = (232, 234, 240) if incoming else (accent[0], accent[1], accent[2])
        text_colour = (20, 20, 25) if incoming else (255, 255, 255)
        fill_round_rect(pixels, x0, y, x0 + bubble_w, y + 56, colour)
        draw_text(pixels, x0 + 20, y + 18, line, scale=scale, color=text_colour)
        y += 78
    # sample watermark, deliberately unmistakable
    for i, line in enumerate(("SAMPLE - DEMO DATA", "REPLACE WITH A REAL", "SCREENSHOT")):
        draw_text(pixels, 30, H - 150 + i * 40, line, scale=2, color=(200, 40, 40))
    fill_rect(pixels, 0, H - 12, W, H, (200, 40, 40))
    return pixels


def write_incidents():
    rows = [
        "date,time,platform,handle,description,file",
        '2026-09-02,22:14,Instagram,@example_handle,"Repeated abusive messages after I asked them to stop. Sender handle visible at the top of the chat.",sample_01_instagram.png',
        '2026-09-05,09:41,WhatsApp,+91-00000-00000,"Threatening message: said they would not leave me alone. Mobile number visible in the chat header.",sample_02_whatsapp.png',
        '2026-09-07,18:02,Instagram,@example_handle_alt,"New account created after I blocked the first one; same abusive content.",sample_03_instagram.png',
    ]
    with open(os.path.join(HERE, "incidents.csv"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + "\n")


def write_case():
    import json

    case = {
        "name": "YOUR NAME (edit this file)",
        "phone": "+91-00000-00000",
        "email": "you@example.com",
        "address": "Your address, city, state, PIN",
        "id_proof": "Aadhaar (last 4 digits) / Passport / DL",
        "accused_name": "Not known",
        "accused_address": "Not known",
        "impact": "Edit this: explain in your own words how this has affected your "
                  "safety, sleep, work and daily life.",
        "place": "Your city",
        "authority": "The Station House Officer / In-charge, Cyber Crime Cell",
        "prior_complaint": "None",
        "platform_reports": "Reports filed in-app on 02-09-2026 and 07-09-2026; blocked both accounts.",
        "threats": "yes",
    }
    with open(os.path.join(HERE, "case.json"), "w", encoding="utf-8") as fh:
        json.dump(case, fh, indent=2)
        fh.write("\n")


def main():
    first = base_screen(
        "SAMPLE CHAT - NOT REAL",
        "@example_handle  02-09-2026 22:14",
        [("HELLO", True), ("PLEASE STOP MESSAGING ME", False), ("NO. I WILL NOT STOP", True),
         ("I AM BLOCKING YOU NOW", False)],
        (48, 110, 220),
    )
    write_png(os.path.join(HERE, "sample_01_instagram.png"), first)

    second = base_screen(
        "SAMPLE CHAT - NOT REAL",
        "+91-00000-00000  05-09-2026 09:41",
        [("YOU CANNOT GET RID OF ME", True), ("I WILL KEEP MAKING", True), ("NEW ACCOUNTS", True)],
        (30, 160, 110),
    )
    write_png(os.path.join(HERE, "sample_02_whatsapp.png"), second)

    third = base_screen(
        "SAMPLE PROFILE - NOT REAL",
        "@example_handle_alt  07-09-2026 18:02",
        [("NEW ACCOUNT", False), ("SAME MESSAGES AGAIN", False), ("STILL NOT STOPPING", False)],
        (170, 70, 190),
    )
    write_png(os.path.join(HERE, "sample_03_instagram.png"), third)

    write_incidents()
    write_case()
    print("Wrote sample_01_instagram.png, sample_02_whatsapp.png, sample_03_instagram.png,")
    print("      incidents.csv, case.json in", HERE)


if __name__ == "__main__":
    main()
