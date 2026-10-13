"""
Minimal, dependency-free PDF writer + image reader.

Only the Python standard library is used, so this works on any machine with
Python 3.8+ and no pip installs.

Supported input images
----------------------
  * JPEG  (.jpg / .jpeg)  -> embedded as-is (DCTDecode, no re-compression)
  * PNG   (.png)          -> decoded (color types 0/2/4/6, bit depth 8),
                             alpha kept as soft mask, re-compressed with Flate
  * BMP   (.bmp)          -> decoded to RGB
  * GIF   (.gif)          -> decoded to RGB (first frame)

Known limits
------------
  * Interlaced (Adam7) PNGs and 16-bit PNGs are not supported; the reader
    raises ImageError with a message telling the user what to do.
  * Text is written with the built-in Helvetica fonts (WinAnsi/CP1252), so
    captions should be Latin text. Non-Latin characters are transliterated to
    '?' rather than silently dropped, and the tool warns you.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

# A4 portrait, in PDF points (72 pt = 1 inch)
A4_WIDTH = 595.28
A4_HEIGHT = 841.89


class ImageError(Exception):
    """Raised when an image cannot be read."""


class RowTooLarge(Exception):
    """Raised when a table row cannot fit on one page even alone."""


# ---------------------------------------------------------------------------
# Image loading
# ---------------------------------------------------------------------------


@dataclass
class RasterImage:
    width: int
    height: int
    color_space: str  # "/DeviceRGB" or "/DeviceGray"
    bits_per_component: int
    data: bytes  # already-encoded stream bytes
    filter_name: str  # "/DCTDecode" or "/FlateDecode"
    soft_mask: bytes = b""  # Flate-compressed alpha, optional
    soft_mask_width: int = 0
    soft_mask_height: int = 0

    # Rejected/corrected hints, surfaced to the user
    notes: List[str] = field(default_factory=list)


def _sniff(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"BM"):
        return "bmp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    return "unknown"


def load_image(path: str) -> RasterImage:
    """Read an image file into a RasterImage ready for PDF embedding."""
    with open(path, "rb") as fh:
        blob = fh.read()
    kind = _sniff(blob)
    if kind == "jpeg":
        return _load_jpeg(blob)
    if kind == "png":
        return _load_png(blob)
    if kind == "bmp":
        return _load_bmp(blob)
    if kind == "gif":
        return _load_gif(blob)
    raise ImageError(
        "Unrecognised image format (looks like neither JPEG, PNG, BMP nor GIF). "
        "Re-save it as PNG or JPG and try again."
    )


# --- JPEG ------------------------------------------------------------------


def _load_jpeg(blob: bytes) -> RasterImage:
    width, height, ncomp = _jpeg_size(blob)
    color_space = "/DeviceGray" if ncomp == 1 else "/DeviceRGB"
    return RasterImage(
        width=width,
        height=height,
        color_space=color_space,
        bits_per_component=8,
        data=blob,
        filter_name="/DCTDecode",
    )


def _jpeg_size(blob: bytes) -> Tuple[int, int, int]:
    """Walk JPEG markers to the frame header and read dimensions/components."""
    if len(blob) < 4:
        raise ImageError("JPEG file is truncated.")
    i = 2
    sof_markers = set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
    while i < len(blob) - 1:
        if blob[i] != 0xFF:
            i += 1
            continue
        marker = blob[i + 1]
        # Standalone markers carry no length
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xD9:  # EOI
            break
        if i + 4 > len(blob):
            break
        seg_len = struct.unpack(">H", blob[i + 2 : i + 4])[0]
        if marker in sof_markers:
            if i + 10 > len(blob):
                break
            height = struct.unpack(">H", blob[i + 5 : i + 7])[0]
            width = struct.unpack(">H", blob[i + 7 : i + 9])[0]
            ncomp = blob[i + 9]
            if width and height:
                return width, height, ncomp
            break
        if marker == 0xDA:  # start of scan: no more headers found
            break
        i += 2 + seg_len
    raise ImageError("Could not read JPEG dimensions (file may be corrupt).")


# --- PNG ------------------------------------------------------------------


def _load_png(blob: bytes) -> RasterImage:
    pos = 8
    ihdr = None
    idat = bytearray()
    palette = None
    trns = None

    while pos + 8 <= len(blob):
        length = struct.unpack(">I", blob[pos : pos + 4])[0]
        ctype = blob[pos + 4 : pos + 8]
        chunk = blob[pos + 8 : pos + 8 + length]
        pos += 12 + length  # length + type + data + crc
        if ctype == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", chunk)
        elif ctype == b"IDAT":
            idat.extend(chunk)
        elif ctype == b"PLTE":
            palette = chunk
        elif ctype == b"tRNS":
            trns = chunk
        elif ctype == b"IEND":
            break

    if ihdr is None:
        raise ImageError("PNG has no IHDR chunk (file is corrupt).")

    width, height, depth, color_type, compression, filt, interlace = ihdr
    if interlace != 0:
        raise ImageError(
            "Interlaced (Adam7) PNG is not supported. Open the screenshot and "
            "re-save it as a non-interlaced PNG or JPG."
        )
    if depth == 16:
        raise ImageError(
            "16-bit PNG is not supported. Re-save the screenshot as 8-bit PNG/JPG."
        )
    if compression != 0 or filt != 0:
        raise ImageError("PNG uses an unsupported compression/filter method.")

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ImageError(f"PNG colour type {color_type} is not supported.")

    if depth != 8:
        # Sub-byte depths only occur for grey/palette images; expand them.
        if color_type in (0, 3):
            raise ImageError(
                f"{depth}-bit PNG is not supported. Re-save the screenshot as "
                "24-bit PNG or as JPG."
            )
        raise ImageError(f"PNG bit depth {depth} is not supported.")

    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    expected = height * (stride + 1)
    if len(raw) < expected:
        raise ImageError("PNG pixel data is truncated.")
    scanlines = _unfilter(raw, height, stride, channels)

    if color_type == 3:  # palette
        if palette is None:
            raise ImageError("Palette PNG has no PLTE chunk.")
        alpha = None
        if trns is not None:
            alpha = bytearray(width * height)
        rgb = bytearray(width * height * 3)
        for idx, pal_index in enumerate(scanlines):
            base = pal_index * 3
            if base + 3 > len(palette):
                raise ImageError("Palette index out of range (corrupt PNG).")
            rgb[idx * 3 : idx * 3 + 3] = palette[base : base + 3]
            if alpha is not None:
                a = trns[pal_index] if pal_index < len(trns) else 255
                alpha[idx] = a
        if alpha is None:
            return _flate_image(width, height, "/DeviceRGB", 3, rgb)
        mask = _flate_image(width, height, "/DeviceGray", 1, alpha, as_mask=True)
        img = _flate_image(width, height, "/DeviceRGB", 3, rgb)
        img.soft_mask = mask.data
        img.soft_mask_width = width
        img.soft_mask_height = height
        return img

    if color_type == 0:  # greyscale
        return _flate_image(width, height, "/DeviceGray", 1, scanlines)

    if color_type == 2:  # RGB
        return _flate_image(width, height, "/DeviceRGB", 3, scanlines)

    if color_type == 4:  # grey + alpha
        gray = bytearray(width * height)
        alpha = bytearray(width * height)
        for i in range(width * height):
            gray[i] = scanlines[i * 2]
            alpha[i] = scanlines[i * 2 + 1]
        img = _flate_image(width, height, "/DeviceGray", 1, gray)
        mask = _flate_image(width, height, "/DeviceGray", 1, alpha, as_mask=True)
        img.soft_mask = mask.data
        img.soft_mask_width = width
        img.soft_mask_height = height
        return img

    # color_type == 6: RGBA
    rgb = bytearray(width * height * 3)
    alpha = bytearray(width * height)
    for i in range(width * height):
        rgb[i * 3 : i * 3 + 3] = scanlines[i * 4 : i * 4 + 3]
        alpha[i] = scanlines[i * 4 + 3]
    img = _flate_image(width, height, "/DeviceRGB", 3, rgb)
    mask = _flate_image(width, height, "/DeviceGray", 1, alpha, as_mask=True)
    img.soft_mask = mask.data
    img.soft_mask_width = width
    img.soft_mask_height = height
    return img


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytearray:
    """Reverse PNG scanline filters (None/Sub/Up/Average/Paeth)."""
    out = bytearray(height * stride)
    prev_start = 0
    pos = 0
    for row in range(height):
        ftype = raw[pos]
        pos += 1
        line = bytearray(raw[pos : pos + stride])
        pos += stride
        cur_start = row * stride
        if ftype == 0:
            pass
        elif ftype == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            if row:
                for i in range(stride):
                    line[i] = (line[i] + out[prev_start + i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up = out[prev_start + i] if row else 0
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = out[prev_start + i] if row else 0
                c = out[prev_start + i - bpp] if (row and i >= bpp) else 0
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise ImageError(f"PNG uses unknown filter type {ftype}.")
        out[cur_start : cur_start + stride] = line
        prev_start = cur_start
    return out


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _flate_image(
    width: int,
    height: int,
    color_space: str,
    colors: int,
    pixels: bytes,
    as_mask: bool = False,
) -> RasterImage:
    return RasterImage(
        width=width,
        height=height,
        color_space=color_space,
        bits_per_component=8,
        data=zlib.compress(bytes(pixels), 6),
        filter_name="/FlateDecode",
        notes=["SMask"] if as_mask else [],
    )


# --- BMP ------------------------------------------------------------------


def _load_bmp(blob: bytes) -> RasterImage:
    if len(blob) < 54:
        raise ImageError("BMP file is truncated.")
    data_offset = struct.unpack("<I", blob[10:14])[0]
    header_size = struct.unpack("<I", blob[14:18])[0]
    if header_size < 40:
        raise ImageError("Old-style BMP headers are not supported.")
    width, height = struct.unpack("<ii", blob[18:26])
    planes, bpp = struct.unpack("<HH", blob[26:30])
    compression = struct.unpack("<I", blob[30:34])[0]
    if compression != 0 or bpp not in (24, 32):
        raise ImageError("Only uncompressed 24/32-bit BMP is supported.")
    if width <= 0 or height == 0:
        raise ImageError("BMP has invalid dimensions.")

    top_down = height < 0
    height = abs(height)
    src_row = width * (bpp // 8)
    pad = (-src_row) % 4
    pixels = bytearray(width * height * 3)
    for row in range(height):
        src_y = row if top_down else (height - 1 - row)
        off = data_offset + src_y * (src_row + pad)
        line = blob[off : off + src_row]
        if len(line) < src_row:
            raise ImageError("BMP pixel data is truncated.")
        for x in range(width):
            b, g, r = line[x * (bpp // 8) : x * (bpp // 8) + 3]
            dst = (row * width + x) * 3
            pixels[dst] = r
            pixels[dst + 1] = g
            pixels[dst + 2] = b
    return _flate_image(width, height, "/DeviceRGB", 3, pixels)


# --- GIF ------------------------------------------------------------------


def _load_gif(blob: bytes) -> RasterImage:
    """Decode the first frame of a GIF (no transparency, no interlacing)."""
    if len(blob) < 13:
        raise ImageError("GIF file is truncated.")
    width, height, packed, _bg, _aspect = struct.unpack("<HHBBB", blob[6:13])
    gct_flag = packed & 0x80
    gct_size = 2 ** ((packed & 0x07) + 1)
    pos = 13
    palette = b""
    if gct_flag:
        palette = blob[pos : pos + gct_size * 3]
        pos += gct_size * 3

    while pos < len(blob):
        block = blob[pos]
        pos += 1
        if block == 0x3B:  # trailer
            break
        if block == 0x21:  # extension
            pos += 1  # label
            while pos < len(blob):
                size = blob[pos]
                pos += 1
                if size == 0:
                    break
                pos += size
            continue
        if block != 0x2C:
            break
        left, top, w, h, flags = struct.unpack("<HHHHB", blob[pos : pos + 9])
        pos += 9
        local_palette = palette
        if flags & 0x80:
            lct_size = 2 ** ((flags & 0x07) + 1)
            local_palette = blob[pos : pos + lct_size * 3]
            pos += lct_size * 3
        interlaced = bool(flags & 0x40)
        min_code_size = blob[pos]
        pos += 1
        lzw_data = bytearray()
        while pos < len(blob):
            size = blob[pos]
            pos += 1
            if size == 0:
                break
            lzw_data.extend(blob[pos : pos + size])
            pos += size
        indices = _gif_lzw(bytes(lzw_data), min_code_size, w * h)
        if not local_palette:
            raise ImageError("GIF has no colour palette.")
        canvas = bytearray(b"\xff" * (width * height * 3))
        # Interlaced GIFs store rows in four passes; map storage row -> display row.
        if interlaced:
            row_map = (
                list(range(0, h, 8)) + list(range(4, h, 8))
                + list(range(2, h, 4)) + list(range(1, h, 2))
            )
        else:
            row_map = list(range(h))
        for storage_y in range(min(h, len(row_map))):
            display_y = row_map[storage_y]
            for x in range(w):
                idx = indices[storage_y * w + x]
                base = idx * 3
                if base + 3 > len(local_palette):
                    continue
                cy, cx = top + display_y, left + x
                if cy >= height or cx >= width or cy < 0:
                    continue
                dst = (cy * width + cx) * 3
                canvas[dst : dst + 3] = local_palette[base : base + 3]
        return _flate_image(width, height, "/DeviceRGB", 3, canvas)

    raise ImageError("GIF contains no image frame.")


def _gif_lzw(data: bytes, min_code_size: int, pixel_count: int) -> bytes:
    clear = 1 << min_code_size
    end = clear + 1
    code_size = min_code_size + 1
    table: List[bytes] = [bytes([i]) for i in range(clear)] + [b"", b""]
    bitpos = 0
    out = bytearray()
    prev: bytes | None = None
    total_bits = len(data) * 8
    while bitpos + code_size <= total_bits and len(out) < pixel_count:
        byte_index = bitpos >> 3
        chunk = data[byte_index : byte_index + 3]
        if len(chunk) < 3:
            chunk = chunk + b"\x00" * (3 - len(chunk))
        value = chunk[0] | (chunk[1] << 8) | (chunk[2] << 16)
        code = (value >> (bitpos & 7)) & ((1 << code_size) - 1)
        bitpos += code_size
        if code == clear:
            table = [bytes([i]) for i in range(clear)] + [b"", b""]
            code_size = min_code_size + 1
            prev = None
            continue
        if code == end:
            break
        if code < len(table) and table[code]:
            entry = table[code]
        elif prev is not None:
            entry = prev + prev[:1]
        else:
            break
        out.extend(entry)
        if prev is not None:
            table.append(prev + entry[:1])
            if len(table) == (1 << code_size) and code_size < 12:
                code_size += 1
        prev = entry
    return bytes(out[:pixel_count])


# ---------------------------------------------------------------------------
# PDF writing
# ---------------------------------------------------------------------------


def _pdf_escape(text: str) -> bytes:
    """Encode text for a WinAnsi (CP1252) base-14 font."""
    encoded = text.encode("cp1252", errors="replace")
    out = bytearray()
    for byte in encoded:
        if byte in (0x28, 0x29, 0x5C):  # ( ) \
            out.append(0x5C)
        out.append(byte)
    return bytes(out)


class PDF:
    """A very small PDF document builder: text pages and image pages."""

    def __init__(self, page_width: float = A4_WIDTH, page_height: float = A4_HEIGHT):
        self.page_width = page_width
        self.page_height = page_height
        self._objects: List[bytes] = []  # 1-based, index 0 unused
        self._page_obj_nums: List[int] = []

        self._regular = self._add_font("Helvetica")
        self._bold = self._add_font("Helvetica-Bold")
        self._oblique = self._add_font("Helvetica-Oblique")
        self._mono = self._add_font("Courier")

    # -- object plumbing --

    def _reserve(self) -> int:
        self._objects.append(b"")
        return len(self._objects)

    def _set(self, num: int, body: bytes) -> None:
        self._objects[num - 1] = body

    def _add_simple(self, body: bytes) -> int:
        num = self._reserve()
        self._set(num, body)
        return num

    def _add_stream(self, dict_body: bytes, stream: bytes) -> int:
        num = self._reserve()
        body = (
            b"<< "
            + dict_body
            + b" /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
        self._set(num, body)
        return num

    def _add_font(self, base_font: str) -> int:
        return self._add_simple(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /"
            + base_font.encode()
            + b" /Encoding /WinAnsiEncoding >>"
        )

    # -- pages --

    def _start_page(self, resources: bytes) -> Tuple[int, int]:
        content_num = self._reserve()
        page_num = self._add_simple(
            b"<< /Type /Page /Parent 0 0 R /MediaBox [0 0 "
            + f"{self.page_width:.2f} {self.page_height:.2f}".encode()
            + b"] /Resources << "
            + resources
            + b" >> /Contents "
            + str(content_num).encode()
            + b" 0 R >>"
        )
        self._page_obj_nums.append(page_num)
        return page_num, content_num

    def _font_resources(self, extra: bytes = b"") -> bytes:
        res = (
            b"/Font << /F1 "
            + str(self._regular).encode()
            + b" 0 R /F2 "
            + str(self._bold).encode()
            + b" 0 R /F3 "
            + str(self._oblique).encode()
            + b" 0 R /F4 "
            + str(self._mono).encode()
            + b" 0 R >>"
        )
        if extra:
            res += b" " + extra
        return res

    # -- text helpers --

    @staticmethod
    def _text_op(x: float, y: float, text: str, font: str, size: float, gray: float) -> bytes:
        return (
            b"BT "
            + f"/{font} {size:.2f} Tf ".encode()
            + f"{gray:.3f} g ".encode()
            + f"1 0 0 1 {x:.2f} {y:.2f} Tm ".encode()
            + b"("
            + _pdf_escape(text)
            + b") Tj ET\n"
        )

    def text_page(
        self,
        title: str,
        paragraphs: Sequence[str],
        title_size: float = 20.0,
        body_size: float = 11.0,
        line_gap: float = 1.45,
        margin: float = 56.0,
        rule: bool = True,
        footer: str = "",
    ) -> None:
        """A page with a title, an optional rule, and wrapped body paragraphs."""
        page_num, content_num = self._start_page(self._font_resources())
        body = bytearray()
        y = self.page_height - margin
        usable = self.page_width - 2 * margin

        body += self._text_op(margin, y - title_size, title, "F2", title_size, 0.0)
        y -= title_size * 1.5
        if rule:
            body += (
                f"{margin:.2f} {y:.2f} m {self.page_width - margin:.2f} {y:.2f} l S\n".encode()
            )
            y -= body_size * line_gap

        max_chars = max(20, int(usable / (body_size * 0.5)))
        floor = margin + body_size * 0.8
        overflow: List[str] = []
        for index, para in enumerate(paragraphs):
            if para == "":
                if y - body_size * line_gap * 0.6 < floor:
                    overflow = list(paragraphs[index:])
                    break
                y -= body_size * line_gap * 0.6
                continue
            lines = _wrap(para, max_chars)
            if y - len(lines) * body_size * line_gap - body_size * 0.35 < floor:
                # Does not fit whole: fill this page, push the remainder onward.
                # Always drawing at least one line guarantees forward progress,
                # even for a single paragraph longer than a page.
                drawn = 0
                while drawn < len(lines) and (drawn == 0 or y - body_size * line_gap >= floor):
                    body += self._text_op(margin, y, lines[drawn], "F1", body_size, 0.12)
                    y -= body_size * line_gap
                    drawn += 1
                rest = lines[drawn:]
                if rest:
                    overflow.append(" ".join(rest))
                overflow.extend(paragraphs[index + 1 :])
                break
            for line in lines:
                body += self._text_op(margin, y, line, "F1", body_size, 0.12)
                y -= body_size * line_gap
            y -= body_size * 0.35

        if not overflow and footer:
            body += self._text_op(margin, margin * 0.55, footer, "F3", 8.5, 0.45)

        self._set(
            content_num,
            b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + bytes(body) + b"\nendstream",
        )
        del page_num
        return overflow

    def text_flow(self, title: str, paragraphs: Sequence[str], **kwargs) -> None:
        """Render paragraphs across as many pages as needed."""
        remaining = list(paragraphs)
        page_kwargs = dict(kwargs)
        first = True
        while remaining:
            page_title = title if first else f"{title} (continued)"
            if not first:
                page_kwargs.pop("footer", None)
            remaining = self.text_page(page_title, remaining, **page_kwargs)
            first = False

    def heading_and_table_page(
        self,
        title: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[str]],
        col_widths: Sequence[float] | None = None,
        margin: float = 48.0,
        font_size: float = 9.0,
        footer: str = "",
        page_limit: int = 0,
    ) -> List[Sequence[str]]:
        """
        A page with a title and a grid table. Returns the rows that did not fit,
        so the caller can render them on a following page.

        If `page_limit` is set and even a single row is too tall for one page,
        RowTooLarge is raised so the caller can retry with a smaller font.
        """
        page_num, content_num = self._start_page(self._font_resources())
        body = bytearray()
        usable = self.page_width - 2 * margin
        y = self.page_height - margin

        body += self._text_op(margin, y - 16, title, "F2", 16.0, 0.0)
        y -= 16 * 1.9

        if col_widths is None:
            col_widths = [usable / len(columns)] * len(columns)
        scale = usable / sum(col_widths)
        widths = [w * scale for w in col_widths]

        row_h = font_size * 1.9
        line_h = font_size * 1.25

        def row_height(cells: Sequence[str]) -> float:
            tallest = 1
            for cell, width in zip(cells, widths):
                chars = max(6, int((width - 6) / (font_size * 0.5)))
                tallest = max(tallest, len(_wrap(str(cell), chars)))
            return max(row_h, line_h * tallest + font_size * 0.8)

        def draw_row(cells: Sequence[str], bold: bool, shade: bool) -> None:
            nonlocal y
            height = row_height(cells)
            if shade:
                body.extend(
                    f"0.94 g {margin:.2f} {y - height:.2f} {sum(widths):.2f} {height:.2f} re f\n".encode()
                )
            x = margin
            for cell, width in zip(cells, widths):
                chars = max(6, int((width - 6) / (font_size * 0.5)))
                lines = _wrap(str(cell), chars)
                ty = y - font_size * 1.15
                for line in lines:
                    if ty < margin * 0.6:
                        break
                    body.extend(
                        self._text_op(x + 3, ty, line, "F2" if bold else "F1", font_size, 0.08)
                    )
                    ty -= line_h
                x += width
            body.extend(
                f"0.72 g {margin:.2f} {y - height:.2f} m {margin + sum(widths):.2f} {y - height:.2f} l S\n".encode()
            )
            y -= height

        draw_row(columns, bold=True, shade=True)

        remaining: List[Sequence[str]] = []
        drawn_rows = 0
        for index, row in enumerate(rows):
            if drawn_rows > 0 and y - row_height(row) < margin * 1.1:
                remaining = list(rows[index:])
                break
            if drawn_rows == 0 and page_limit and y - row_height(row) < margin * 1.1:
                # A single row taller than the page can never fit; the caller
                # will retry with a smaller font.
                self._objects[content_num - 1] = (
                    b"<< /Length 0 >>\nstream\n\nendstream"
                )
                self._page_obj_nums.pop()
                raise RowTooLarge(
                    f"one table row does not fit at {font_size:.1f}pt "
                    f"(needs {row_height(row):.0f}pt of {y - margin * 1.1:.0f}pt available)"
                )
            draw_row(row, bold=False, shade=False)
            drawn_rows += 1

        if footer and not remaining:
            body.extend(self._text_op(margin, margin * 0.45, footer, "F3", 8.0, 0.45))

        self._set(
            content_num,
            b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + bytes(body) + b"\nendstream",
        )
        del page_num
        return remaining

    def table_flow(
        self,
        title: str,
        columns: Sequence[str],
        rows: Sequence[Sequence[str]],
        col_widths: Sequence[float] | None = None,
        margin: float = 48.0,
        footer: str = "",
        min_font_size: float = 6.0,
    ) -> List[str]:
        """
        Render a table across as many pages as needed, repeating the header.

        The font is chosen automatically: the largest size in
        (min_font_size .. 9.0] at which every row fits within a page, so a long
        description stays readable instead of running off the page. Returns any
        notes about content that had to be shortened.
        """
        def render(font_size: float) -> List[Sequence[str]]:
            remaining: List[Sequence[str]] = list(rows)
            page_title = title
            first = True
            while True:
                remaining = self.heading_and_table_page(
                    page_title,
                    columns,
                    remaining,
                    col_widths=col_widths,
                    margin=margin,
                    font_size=font_size,
                    footer=footer if first else "",
                    page_limit=1,
                )
                if not remaining:
                    return []
                page_title = f"{title} (continued)"
                first = False

        notes: List[str] = []
        try:
            render(9.0)
            return notes
        except RowTooLarge:
            pass
        size = 8.0
        while size > min_font_size:
            try:
                render(size)
                return notes
            except RowTooLarge:
                size -= 0.5

        # Last resort: one row is longer than a whole page even at the smallest
        # size. Truncate those cells (the full text still lives in the CSV) and
        # say so, rather than silently losing the content or crashing.
        usable = self.page_width - 2 * margin
        if col_widths is None:
            col_widths = [usable / len(columns)] * len(columns)
        scale = usable / sum(col_widths)
        widths = [w * scale for w in col_widths]
        available = self.page_height - 2 * margin - 45.0
        line_h = min_font_size * 1.25
        max_lines = max(1, int((available - min_font_size * 0.8) / line_h))
        budgets = [
            max(8, int((w - 6) / (min_font_size * 0.5))) * max_lines for w in widths
        ]
        def truncate(rows_in: Sequence[Sequence[str]], budgets_in: Sequence[int]) -> List[Sequence[str]]:
            out: List[Sequence[str]] = []
            for row in rows_in:
                new_row = []
                for cell, budget in zip(row, budgets_in):
                    text = str(cell)
                    if len(text) > budget:
                        new_row.append(text[: max(8, budget - 24)].rstrip() + "… (see incidents.csv)")
                        if not notes:
                            notes.append(
                                "A table cell was too long for the PDF and was shortened; "
                                "the complete text is in incidents.csv."
                            )
                    else:
                        new_row.append(text)
                out.append(new_row)
            return out

        attempt = truncate(rows, budgets)
        for _ in range(6):
            try:
                rows = attempt
                render(min_font_size)
                return notes
            except RowTooLarge:
                budgets = [max(8, int(b * 0.6)) for b in budgets]
                attempt = truncate(rows, budgets)
        raise RowTooLarge("could not fit the table even after shortening its cells")

    def image_page(
        self,
        image_path: str,
        caption_lines: Sequence[Sequence[str]] | None = None,
        margin: float = 48.0,
        caption_size: float = 9.5,
        footer: str = "",
        border: bool = True,
    ) -> None:
        """
        A page with one image (scaled to fit) and a caption block underneath.

        `caption_lines` is a sequence of (label, value) pairs rendered as
        "label: value" lines; a pair with an empty label renders as a plain
        paragraph line.
        """
        img = load_image(image_path)
        image_num = self._add_image_object(img)
        image_name = f"/Im{image_num}"

        resources = self._font_resources(
            b"/XObject << " + image_name.encode() + b" " + str(image_num).encode() + b" 0 R >>"
        )
        page_num, content_num = self._start_page(resources)

        caption_lines = list(caption_lines or [])
        usable_w = self.page_width - 2 * margin

        body = bytearray()
        y_top = self.page_height - margin
        body.extend(
            self._text_op(margin, y_top - 14, "EVIDENCE", "F2", 11.0, 0.35)
            + f"0.72 g {margin:.2f} {y_top - 22:.2f} m {self.page_width - margin:.2f} {y_top - 22:.2f} l S\n".encode()
        )

        # Measure caption block (rendered at the bottom)
        max_chars = max(20, int(usable_w / (caption_size * 0.5)))
        rendered_caption: List[Tuple[str, str, float]] = []
        caption_height = 0.0
        for label, value in caption_lines:
            if label:
                line = f"{label}: {value}"
                rendered = _wrap(line, max_chars)
                for i, part in enumerate(rendered):
                    prefix = "" if i else ""
                    rendered_caption.append((prefix, part, caption_size))
                    caption_height += caption_size * 1.4
            else:
                for part in _wrap(value, max_chars):
                    rendered_caption.append(("", part, caption_size))
                    caption_height += caption_size * 1.4
            caption_height += caption_size * 0.25

        if footer:
            caption_height += caption_size * 1.8

        top_of_image = y_top - 34
        bottom_of_caption = margin * 0.6 + caption_height
        avail_h = max(60.0, top_of_image - bottom_of_caption)

        scale = min(usable_w / img.width, avail_h / img.height)
        draw_w = img.width * scale
        draw_h = img.height * scale
        img_x = margin + (usable_w - draw_w) / 2
        img_y = top_of_image - draw_h

        if border:
            body.extend(
                f"0.80 g {img_x - 1:.2f} {img_y - 1:.2f} {draw_w + 2:.2f} {draw_h + 2:.2f} re S\n".encode()
            )

        body.extend(
            b"q "
            + f"{draw_w:.2f} 0 0 {draw_h:.2f} {img_x:.2f} {img_y:.2f} cm ".encode()
            + image_name.encode()
            + b" Do Q\n"
        )

        y = img_y - caption_size * 1.6
        for _prefix, line, size in rendered_caption:
            if y < margin * 0.4:
                break
            body.extend(self._text_op(margin, y, line, "F1", size, 0.08))
            y -= size * 1.4

        if footer:
            body.extend(self._text_op(margin, margin * 0.42, footer, "F3", 8.0, 0.45))

        self._set(
            content_num,
            b"<< /Length " + str(len(body)).encode() + b" >>\nstream\n" + bytes(body) + b"\nendstream",
        )
        del page_num

    def _add_image_object(self, img: RasterImage) -> int:
        smask_ref = b""
        if img.soft_mask:
            smask_num = self._add_stream(
                b"/Type /XObject /Subtype /Image /Width "
                + str(img.soft_mask_width).encode()
                + b" /Height "
                + str(img.soft_mask_height).encode()
                + b" /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode",
                img.soft_mask,
            )
            smask_ref = b" /SMask " + str(smask_num).encode() + b" 0 R"

        return self._add_stream(
            b"/Type /XObject /Subtype /Image /Width "
            + str(img.width).encode()
            + b" /Height "
            + str(img.height).encode()
            + b" /ColorSpace "
            + img.color_space.encode()
            + b" /BitsPerComponent "
            + str(img.bits_per_component).encode()
            + b" /Filter "
            + img.filter_name.encode()
            + smask_ref,
            img.data,
        )

    # -- output --

    def save(self, path: str, title: str = "Evidence bundle", author: str = "") -> None:
        pages_num = self._reserve()
        kids = b" ".join(str(n).encode() + b" 0 R" for n in self._page_obj_nums)
        self._set(
            pages_num,
            b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(self._page_obj_nums)).encode() + b" >>",
        )

        for num in self._page_obj_nums:
            body = self._objects[num - 1].replace(
                b"/Parent 0 0 R", b"/Parent " + str(pages_num).encode() + b" 0 R"
            )
            self._objects[num - 1] = body

        catalog_extra = b""
        info_num = None
        if title or author:
            info_num = self._add_simple(
                b"<< /Title ("
                + _pdf_escape(title)
                + b") /Author ("
                + _pdf_escape(author)
                + b") /Producer (harassment-evidence-kit) >>"
            )
            catalog_extra = b""

        catalog = self._add_simple(
            b"<< /Type /Catalog /Pages " + str(pages_num).encode() + b" 0 R >>"
        )

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: List[int] = []
        for i, body in enumerate(self._objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

        xref_pos = len(out)
        count = len(self._objects) + 1
        out += f"xref\n0 {count}\n".encode()
        out += b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()

        trailer = (
            b"trailer\n<< /Size "
            + str(count).encode()
            + b" /Root "
            + str(catalog).encode()
            + b" 0 R"
        )
        if info_num:
            trailer += b" /Info " + str(info_num).encode() + b" 0 R"
        trailer += b" >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
        out += trailer

        with open(path, "wb") as fh:
            fh.write(bytes(out))


def _wrap(text: str, max_chars: int) -> List[str]:
    """Greedy word wrap that also breaks over-long single tokens."""
    text = str(text).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    while "  " in text:
        text = text.replace("  ", " ")
    words = text.split(" ")
    lines: List[str] = []
    current = ""
    for word in words:
        while len(word) > max_chars:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:max_chars])
            word = word[max_chars:]
        candidate = word if not current else current + " " + word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current or not lines:
        lines.append(current)
    return lines
