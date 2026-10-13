"""
textshape.py — Hindi (aur doosri complex scripts) ka sahi text rendering.

Problem: Pillow ke normal wheel me 'raqm' nahi hota, isliye "कि" jaise akshar
galat chhapte hain (matra aage-peeche ho jaati hai, क्ष/त्र/ज्ञ ke ligature
nahi bante). Ye module:

  1. HarfBuzz (jo Pillow ke saath hi aati hai, ya system me hoti hai) ko ctypes
     se use karke akshar ki sahi shaping karta hai,
  2. glyph ke outline ko TTF file se khud padhta hai (koi fontTools nahi chahiye),
  3. unhe apne rasterizer se pixels me badalta hai.

Isliye Termux par bhi, bina kuch compile kiye, Hindi theek dikhti hai.

Sirf Python standard library + Pillow (jo pehle se chahiye).
"""

from __future__ import annotations

import ctypes
import ctypes.util
import glob
import math
import os
import struct
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

DEVANAGARI_RANGES = (
    (0x0900, 0x097F),   # Devanagari
    (0x1CD0, 0x1CFF),   # Vedic extensions
    (0xA8E0, 0xA8FF),   # Devanagari Extended
    (0x200C, 0x200D),   # ZWNJ / ZWJ
    (0x25CC, 0x25CC),   # dotted circle
)


def is_devanagari(char: str) -> bool:
    code = ord(char)
    return any(lo <= code <= hi for lo, hi in DEVANAGARI_RANGES)


# ---------------------------------------------------------------------------
# HarfBuzz (ctypes)
# ---------------------------------------------------------------------------


class _GlyphInfo(ctypes.Structure):
    _fields_ = [
        ("codepoint", ctypes.c_uint32),
        ("cluster", ctypes.c_uint32),
        ("mask", ctypes.c_uint32),
        ("var1", ctypes.c_uint32),
        ("var2", ctypes.c_uint32),
    ]


class _GlyphPosition(ctypes.Structure):
    _fields_ = [
        ("x_advance", ctypes.c_int32),
        ("y_advance", ctypes.c_int32),
        ("x_offset", ctypes.c_int32),
        ("y_offset", ctypes.c_int32),
        ("var", ctypes.c_uint32),
    ]


_HB = None
_HB_ERROR = ""


def _hb_candidates() -> List[str]:
    found: List[str] = []
    # 1. Pillow ke wheel ke saath aayi harfbuzz (desktop par sabse aam)
    try:
        import PIL

        for base in {os.path.dirname(os.path.dirname(PIL.__file__))}:
            found += sorted(glob.glob(os.path.join(base, "pillow.libs", "libharfbuzz*.so*")))
            found += sorted(glob.glob(os.path.join(base, "PIL", ".dylibs", "libharfbuzz*")))
    except Exception:
        pass
    # 2. system / Termux
    library = ctypes.util.find_library("harfbuzz")
    if library:
        found.append(library)
    found += [
        "/data/data/com.termux/files/usr/lib/libharfbuzz.so",
        "/data/data/com.termux/files/usr/lib/libharfbuzz.so.0",
        "/usr/lib/libharfbuzz.so.0",
        "/usr/lib/x86_64-linux-gnu/libharfbuzz.so.0",
        "/usr/lib/aarch64-linux-gnu/libharfbuzz.so.0",
        "/usr/local/lib/libharfbuzz.dylib",
        "/opt/homebrew/lib/libharfbuzz.dylib",
        "/opt/homebrew/lib/libharfbuzz.0.dylib",
    ]
    found += sorted(glob.glob("/usr/lib/**/libharfbuzz.so*", recursive=False))
    return [path for path in found if path and os.path.exists(path)]


def _declare(lib) -> None:
    """
    Har function ke prototypes set karna ZAROORI hai.

    Bina argtypes ke ctypes pointer ko 32-bit int maan leta hai — aur 4 GB se
    upar ka address kat jaata hai, jisse poora process crash (segfault) ho jaata
    hai. Yahan sab kuch theek se declare kiya gaya hai.
    """
    void_p, c_uint, c_int, c_char_p = ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_char_p
    c_int32, c_uint32 = ctypes.c_int32, ctypes.c_uint32

    lib.hb_blob_create_from_file.restype = void_p
    lib.hb_blob_create_from_file.argtypes = [c_char_p]
    lib.hb_blob_destroy.restype = None
    lib.hb_blob_destroy.argtypes = [void_p]
    lib.hb_face_create.restype = void_p
    lib.hb_face_create.argtypes = [void_p, c_uint]
    lib.hb_face_destroy.restype = None
    lib.hb_face_destroy.argtypes = [void_p]
    lib.hb_font_create.restype = void_p
    lib.hb_font_create.argtypes = [void_p]
    lib.hb_font_destroy.restype = None
    lib.hb_font_destroy.argtypes = [void_p]
    lib.hb_font_set_scale.restype = None
    lib.hb_font_set_scale.argtypes = [void_p, c_int, c_int]
    lib.hb_ot_font_set_funcs.restype = None
    lib.hb_ot_font_set_funcs.argtypes = [void_p]
    lib.hb_font_get_glyph_h_advance.restype = c_int32
    lib.hb_font_get_glyph_h_advance.argtypes = [void_p, c_uint32]

    lib.hb_buffer_create.restype = void_p
    lib.hb_buffer_create.argtypes = []
    lib.hb_buffer_destroy.restype = None
    lib.hb_buffer_destroy.argtypes = [void_p]
    lib.hb_buffer_add_utf8.restype = None
    lib.hb_buffer_add_utf8.argtypes = [void_p, c_char_p, c_int, c_uint, c_int]
    lib.hb_buffer_guess_segment_properties.restype = None
    lib.hb_buffer_guess_segment_properties.argtypes = [void_p]
    lib.hb_buffer_get_length.restype = c_uint
    lib.hb_buffer_get_length.argtypes = [void_p]
    lib.hb_shape.restype = c_int
    lib.hb_shape.argtypes = [void_p, void_p, void_p, c_uint]
    lib.hb_buffer_get_glyph_infos.restype = ctypes.POINTER(_GlyphInfo)
    lib.hb_buffer_get_glyph_infos.argtypes = [void_p, ctypes.POINTER(c_uint)]
    lib.hb_buffer_get_glyph_positions.restype = ctypes.POINTER(_GlyphPosition)
    lib.hb_buffer_get_glyph_positions.argtypes = [void_p, ctypes.POINTER(c_uint)]


def load_harfbuzz(verbose: bool = False):
    global _HB, _HB_ERROR
    if _HB is not None:
        return _HB
    errors = []
    for path in _hb_candidates():
        try:
            lib = ctypes.CDLL(path)
            _declare(lib)
            # ek chhota test: khali text ko shape karke dekho
            probe = _shape_once(lib, path, b"", 1000)
            if probe is None:
                raise RuntimeError("test shape fail")
            _HB = lib
            if verbose:
                print(f"  harfbuzz mila: {path}")
            return _HB
        except Exception as exc:  # agla candidate try karo
            errors.append(f"{path}: {exc}")
            continue
    _HB_ERROR = "; ".join(errors[-3:]) or "koi libharfbuzz nahi mili"
    return None


def _shape_once(lib, font_path: str, text: bytes, upem: int):
    """HarfBuzz ZINDA hai ya nahi, ye test karta hai (ek chhota shape)."""
    blob = lib.hb_blob_create_from_file(font_path.encode())
    if not blob:
        return None
    face = lib.hb_face_create(blob, 0)
    if not face:
        lib.hb_blob_destroy(blob)
        return None
    font = lib.hb_font_create(face)
    lib.hb_ot_font_set_funcs(font)
    lib.hb_font_set_scale(font, upem, upem)
    buffer = lib.hb_buffer_create()
    lib.hb_buffer_add_utf8(buffer, text, len(text), 0, len(text))
    lib.hb_buffer_guess_segment_properties(buffer)
    lib.hb_shape(font, buffer, None, 0)
    count = lib.hb_buffer_get_length(buffer)
    lib.hb_buffer_destroy(buffer)
    lib.hb_font_destroy(font)
    lib.hb_face_destroy(face)
    lib.hb_blob_destroy(blob)
    return count


def harfbuzz_path() -> str:
    for path in _hb_candidates():
        return path
    return ""


# ---------------------------------------------------------------------------
# ek font ka shaper
# ---------------------------------------------------------------------------


class ShapedFont:
    """HarfBuzz se shaping + glyph outline reader, dono ek jagah."""

    def __init__(self, path: str, index: int = 0):
        self.path = path
        self._blob = None
        lib = load_harfbuzz()
        if lib is None:
            raise RuntimeError(f"HarfBuzz nahi mili ({_HB_ERROR})")
        self.lib = lib
        self._blob = lib.hb_blob_create_from_file(path.encode())
        if not self._blob:
            raise RuntimeError(f"Font file padhi nahi ja saki: {path}")
        self._face = lib.hb_face_create(self._blob, index)
        self._font = lib.hb_font_create(self._face)
        lib.hb_ot_font_set_funcs(self._font)

        self.outline = TrueTypeOutlines(path)
        # shorthand: glyph outlines seedha ShapedFont se
        upem = self.outline.units_per_em
        self.upem = upem or 1000
        lib.hb_font_set_scale(self._font, self.upem, self.upem)

        self._advance_cache: Dict[int, int] = {}

    # -- shaping --
    def shape(self, text: str) -> List[Tuple[int, int, int, int, int]]:
        """Return [(glyph_id, x_advance, y_advance, x_offset, y_offset)] font units me."""
        lib = self.lib
        buffer = lib.hb_buffer_create()
        try:
            encoded = text.encode("utf-8")
            lib.hb_buffer_add_utf8(buffer, encoded, len(encoded), 0, len(encoded))
            lib.hb_buffer_guess_segment_properties(buffer)
            lib.hb_shape(self._font, buffer, None, 0)
            count = ctypes.c_uint(0)
            infos = lib.hb_buffer_get_glyph_infos(buffer, ctypes.byref(count))
            positions = lib.hb_buffer_get_glyph_positions(buffer, ctypes.byref(count))
            out = []
            for i in range(count.value):
                info = infos[i]
                pos = positions[i]
                out.append((int(info.codepoint), int(pos.x_advance), int(pos.y_advance),
                            int(pos.x_offset), int(pos.y_offset)))
            return out
        finally:
            try:
                lib.hb_buffer_destroy(buffer)
            except Exception:
                pass

    def contours(self, glyph_id: int):
        return self.outline.contours(glyph_id)

    def glyph_advance(self, glyph_id: int) -> int:
        if glyph_id not in self._advance_cache:
            self._advance_cache[glyph_id] = int(self.lib.hb_font_get_glyph_h_advance(self._font, glyph_id))
        return self._advance_cache[glyph_id]


# ---------------------------------------------------------------------------
# TrueType outline reader (glyf table)
# ---------------------------------------------------------------------------


class TrueTypeError(Exception):
    pass


class TrueTypeOutlines:
    """
    'glyf' table se glyph ke contours padhta hai — simple aur composite dono.
    Curve ko seedha line segments me flatten kar dete hain (rasterizer ke liye).
    """

    def __init__(self, path: str):
        with open(path, "rb") as fh:
            self.data = fh.read()
        self._read_table_directory()
        if "glyf" not in self.tables or "loca" not in self.tables:
            raise TrueTypeError(
                "Is font me 'glyf' outlines nahi hain (CFF/OTF font hai). "
                "Noto Sans / Noto Sans Devanagari (.ttf) use kariye."
            )
        self._read_head()
        self._read_maxp()
        self._read_loca()
        self._cache: Dict[int, List[List[Tuple[float, float]]]] = {}

    # -- tables --
    def _read_table_directory(self) -> None:
        data = self.data
        if len(data) < 12:
            raise TrueTypeError("Font file bahut chhoti hai.")
        tag = data[:4]
        self.tables: Dict[str, Tuple[int, int]] = {}
        if tag in (b"ttcf",):  # TrueType collection: pehla font uthate hain
            count = struct.unpack(">I", data[8:12])[0]
            if count < 1:
                raise TrueTypeError("Font collection khaali hai.")
            offset = struct.unpack(">I", data[12:16])[0]
        else:
            offset = 0
        num_tables = struct.unpack(">H", data[offset + 4 : offset + 6])[0]
        pos = offset + 12
        for _ in range(num_tables):
            name = data[pos : pos + 4].decode("latin-1")
            off, length = struct.unpack(">II", data[pos + 8 : pos + 16])
            self.tables[name] = (off, length)
            pos += 16

    def _read_head(self) -> None:
        off = self.tables["head"][0]
        self.units_per_em = struct.unpack(">H", self.data[off + 18 : off + 20])[0]
        self.index_to_loc = struct.unpack(">h", self.data[off + 50 : off + 52])[0]
        self.ymax = struct.unpack(">h", self.data[off + 42 : off + 44])[0]
        self.ymin = struct.unpack(">h", self.data[off + 38 : off + 40])[0]

    def _read_maxp(self) -> None:
        off = self.tables["maxp"][0]
        self.num_glyphs = struct.unpack(">H", self.data[off + 4 : off + 6])[0]

    def _read_loca(self) -> None:
        off, length = self.tables["loca"]
        count = self.num_glyphs + 1
        if self.index_to_loc == 0:
            self.loca = [2 * v for v in struct.unpack(f">{count}H", self.data[off : off + 2 * count])]
        else:
            self.loca = list(struct.unpack(f">{count}I", self.data[off : off + 4 * count]))

    # -- outlines --
    def contours(self, glyph_id: int, depth: int = 0) -> List[List[Tuple[float, float]]]:
        if glyph_id in self._cache:
            return self._cache[glyph_id]
        result = self._read_glyph(glyph_id, depth)
        self._cache[glyph_id] = result
        return result

    def _glyph_slice(self, glyph_id: int) -> Optional[bytes]:
        if glyph_id + 1 >= len(self.loca):
            return None
        start, end = self.loca[glyph_id], self.loca[glyph_id + 1]
        if start == end:
            return None
        glyf_off = self.tables["glyf"][0]
        return self.data[glyf_off + start : glyf_off + end]

    def _read_glyph(self, glyph_id: int, depth: int) -> List[List[Tuple[float, float]]]:
        if depth > 5:
            return []
        blob = self._glyph_slice(glyph_id)
        if not blob or len(blob) < 10:
            return []
        num_contours, x_min, y_min, x_max, y_max = struct.unpack(">hhhhh", blob[:10])

        if num_contours >= 0:
            return self._read_simple(blob, num_contours)
        return self._read_composite(blob, depth)

    def _read_simple(self, blob: bytes, num_contours: int) -> List[List[Tuple[float, float]]]:
        pos = 10
        ends = struct.unpack(f">{num_contours}H", blob[pos : pos + 2 * num_contours])
        pos += 2 * num_contours
        num_points = (ends[-1] + 1) if num_contours else 0
        if num_points == 0:
            return []
        instruction_length = struct.unpack(">H", blob[pos : pos + 2])[0]
        pos += 2 + instruction_length

        flags: List[int] = []
        while len(flags) < num_points:
            flag = blob[pos]
            pos += 1
            flags.append(flag)
            if flag & 0x08:  # repeat
                repeat = blob[pos]
                pos += 1
                flags.extend([flag] * repeat)
        flags = flags[:num_points]

        xs: List[int] = []
        value = 0
        for flag in flags:
            if flag & 0x02:  # x is 1 byte
                delta = blob[pos]
                pos += 1
                value += delta if flag & 0x10 else -delta
            elif not flag & 0x10:  # x is 2 bytes signed
                value += struct.unpack(">h", blob[pos : pos + 2])[0]
                pos += 2
            xs.append(value)

        ys: List[int] = []
        value = 0
        for flag in flags:
            if flag & 0x04:
                delta = blob[pos]
                pos += 1
                value += delta if flag & 0x20 else -delta
            elif not flag & 0x20:
                value += struct.unpack(">h", blob[pos : pos + 2])[0]
                pos += 2
            ys.append(value)

        points = list(zip(xs, ys))
        on_curve = [bool(f & 0x01) for f in flags]

        contours: List[List[Tuple[float, float]]] = []
        start = 0
        for end in ends:
            if end < start:
                start = end + 1
                continue
            contour_points = points[start : end + 1]
            contour_flags = on_curve[start : end + 1]
            contours.append(_quadratic_to_lines(contour_points, contour_flags))
            start = end + 1
        return contours

    def _read_composite(self, blob: bytes, depth: int) -> List[List[Tuple[float, float]]]:
        contours: List[List[Tuple[float, float]]] = []
        pos = 10
        while pos + 4 <= len(blob):
            flags, glyph_index = struct.unpack(">HH", blob[pos : pos + 4])
            pos += 4
            dx = dy = 0
            if flags & 0x0001:  # ARG_1_AND_2_ARE_WORDS
                dx, dy = struct.unpack(">hh", blob[pos : pos + 4])
                pos += 4
            else:
                dx, dy = struct.unpack(">bb", blob[pos : pos + 2])
                pos += 2
            a = d = 1.0
            b = c = 0.0
            if flags & 0x0008:  # WE_HAVE_A_SCALE
                a = d = struct.unpack(">h", blob[pos : pos + 2])[0] / 16384.0
                pos += 2
            elif flags & 0x0040:  # X_AND_Y_SCALE
                a, d = (v / 16384.0 for v in struct.unpack(">hh", blob[pos : pos + 4]))
                pos += 4
            elif flags & 0x0080:  # 2x2
                a, b, c, d = (v / 16384.0 for v in struct.unpack(">hhhh", blob[pos : pos + 8]))
                pos += 8

            for contour in self.contours(glyph_index, depth + 1):
                moved = []
                for x, y in contour:
                    nx = a * x + c * y + dx
                    ny = b * x + d * y + dy
                    moved.append((nx, ny))
                contours.append(moved)

            if not flags & 0x0020:  # MORE_COMPONENTS
                break
        return contours


def _quadratic_to_lines(
    points: Sequence[Tuple[float, float]],
    on_curve: Sequence[bool],
    steps_per_curve: int = 8,
) -> List[Tuple[float, float]]:
    """TrueType ke quadratic curves ko chhote line segments me badalna."""
    n = len(points)
    if n == 0:
        return []
    if n == 1:
        return [points[0]]

    # shuruaati point: agar pehla point off-curve hai to pichhle ke saath mila kar
    output: List[Tuple[float, float]] = []
    if on_curve[0]:
        start = points[0]
        index = 1
    else:
        last = points[-1]
        if on_curve[-1]:
            start = last
        else:
            start = ((points[0][0] + last[0]) / 2.0, (points[0][1] + last[1]) / 2.0)
        index = 0
    output.append(start)

    current = start
    i = index
    guard = 0
    while guard <= n + 1:
        guard += 1
        if i >= n + index:
            break
        point = points[i % n]
        flag = on_curve[i % n]
        if flag:
            output.append(point)
            current = point
            i += 1
        else:
            nxt = points[(i + 1) % n]
            nxt_flag = on_curve[(i + 1) % n]
            end = nxt if nxt_flag else ((point[0] + nxt[0]) / 2.0, (point[1] + nxt[1]) / 2.0)
            for step in range(1, steps_per_curve + 1):
                t = step / steps_per_curve
                mt = 1 - t
                x = mt * mt * current[0] + 2 * mt * t * point[0] + t * t * end[0]
                y = mt * mt * current[1] + 2 * mt * t * point[1] + t * t * end[1]
                output.append((x, y))
            current = end
            i += 2 if nxt_flag else 1
    return output


# ---------------------------------------------------------------------------
# rasterizer
# ---------------------------------------------------------------------------


def rasterize(
    contours: Sequence[Sequence[Tuple[float, float]]],
    width: int,
    height: int,
    supersample: int = 3,
) -> "Image.Image":
    """
    Contours (image ke coordinates me, y neeche) ko 'L' mask me badalta hai.
    Even-odd fill, supersampling se smooth kinare.
    """
    scale = supersample
    big = Image.new("L", (max(1, width * scale), max(1, height * scale)), 0)
    draw = ImageDraw.Draw(big)

    edges: List[Tuple[float, float, float, float]] = []
    for contour in contours:
        if len(contour) < 2:
            continue
        points = [(x * scale, y * scale) for x, y in contour]
        for i in range(len(points)):
            x0, y0 = points[i]
            x1, y1 = points[(i + 1) % len(points)]
            x0 = min(max(x0, -1.0), big.width + 1.0)
            x1 = min(max(x1, -1.0), big.width + 1.0)
            y0 = min(max(y0, -1.0), big.height + 1.0)
            y1 = min(max(y1, -1.0), big.height + 1.0)
            if y0 != y1:
                edges.append((x0, y0, x1, y1))

    if not edges:
        return big.resize((max(1, width), max(1, height)), Image.LANCZOS)

    y_start = max(0, int(min(min(e[1], e[3]) for e in edges)) - 1)
    y_end = min(big.height, int(max(max(e[1], e[3]) for e in edges)) + 2)

    for y in range(y_start, y_end):
        scan = y + 0.5
        crossings: List[float] = []
        for x0, y0, x1, y1 in edges:
            if (y0 <= scan < y1) or (y1 <= scan < y0):
                t = (scan - y0) / (y1 - y0)
                crossings.append(x0 + t * (x1 - x0))
        if len(crossings) < 2:
            continue
        crossings.sort()
        for i in range(0, len(crossings) - 1, 2):
            start = int(crossings[i] + 0.5)
            end = int(crossings[i + 1] + 0.5)
            if end > start:
                draw.line([(start, y), (end, y)], fill=255)

    return big.resize((max(1, width), max(1, height)), Image.LANCZOS)


# ---------------------------------------------------------------------------
# text renderer
# ---------------------------------------------------------------------------


@dataclass
class GlyphBitmap:
    mask: "Image.Image"
    left: int
    top: int
    advance: float


class TextRenderer:
    """
    Latin aur Devanagari ke liye alag-alag font use karke text ko image banata hai.

    Bhasha ke hisaab se font khud badalta hai, isliye mix text (Hindi + English)
    bhi theek aata hai.
    """

    def __init__(self, latin_font: str, devanagari_font: Optional[str] = None):
        self.latin = ShapedFont(latin_font)
        self.devanagari = ShapedFont(devanagari_font) if devanagari_font and os.path.exists(devanagari_font) else None
        self._glyph_cache: Dict[Tuple[str, int, int], GlyphBitmap] = {}

    # -- runs --
    def _runs(self, text: str) -> List[Tuple[bool, str]]:
        runs: List[Tuple[bool, str]] = []
        for char in text:
            dev = is_devanagari(char)
            if runs and runs[-1][0] == dev:
                runs[-1] = (dev, runs[-1][1] + char)
            else:
                runs.append((dev, char))
        return runs

    def _font_for(self, dev: bool) -> ShapedFont:
        if dev and self.devanagari is not None:
            return self.devanagari
        return self.latin

    # -- measurement --
    def measure(self, text: str, size: int) -> float:
        total = 0.0
        for dev, run in self._runs(text):
            font = self._font_for(dev)
            scale = size / font.upem
            for glyph_id, advance, _y_adv, _x_off, _y_off in font.shape(run):
                total += advance * scale
        return total

    # -- glyph bitmaps --
    def _glyph(self, font: ShapedFont, glyph_id: int, size: int) -> GlyphBitmap:
        key = (font.path, glyph_id, size)
        cached = self._glyph_cache.get(key)
        if cached is not None:
            return cached

        scale = size / font.upem
        advance = font.glyph_advance(glyph_id) * scale
        raw_contours = font.contours(glyph_id)

        if not raw_contours:
            bitmap = GlyphBitmap(Image.new("L", (1, 1), 0), 0, 0, advance)
            self._glyph_cache[key] = bitmap
            return bitmap

        # Font units -> pixels (y upar ki taraf positive rehta hai)
        contours = [[(x * scale, y * scale) for x, y in contour] for contour in raw_contours]
        xs = [x for contour in contours for x, _ in contour]
        ys = [y for contour in contours for _, y in contour]

        pad = 2
        left = int(math.floor(min(xs))) - pad
        right = int(math.ceil(max(xs))) + pad
        bottom = int(math.ceil(max(ys))) + pad
        top = int(math.floor(min(ys))) - pad
        width = max(1, right - left)
        height = max(1, bottom - top)

        # Image space: y neeche ki taraf badhta hai
        image_contours = [[(x - left, bottom - y) for x, y in contour] for contour in contours]
        mask = rasterize(image_contours, width, height, supersample=3)

        # Baseline image me pen_y par hoti hai; mask ka top = font ka sabse uncha point
        bitmap = GlyphBitmap(mask, left, -bottom, advance)
        self._glyph_cache[key] = bitmap
        return bitmap

    # -- drawing --
    def render_line(self, text: str, size: int, color: Tuple[int, int, int], baseline: float = 0.0):
        """Ek line ki RGBA image + uska width deta hai (baseline = 0 se neeche)."""
        width = int(self.measure(text, size)) + size
        ascent = int(size * 1.15)
        descent = int(size * 0.55)
        height = ascent + descent
        canvas = Image.new("RGBA", (max(1, width), height), (0, 0, 0, 0))

        pen_x = size * 0.25
        pen_y = ascent - size * 0.25  # baseline
        for dev, run in self._runs(text):
            font = self._font_for(dev)
            scale = size / font.upem
            for glyph_id, advance, _y_adv, x_off, y_off in font.shape(run):
                bitmap = self._glyph(font, glyph_id, size)
                if bitmap.mask.getbbox() is not None:
                    x = int(pen_x + (x_off * scale)) + bitmap.left
                    y = int(pen_y - (y_off * scale)) + bitmap.top
                    canvas.alpha_composite(_tinted(bitmap.mask, color), (x, y))
                pen_x += advance * scale
        used = int(pen_x + size * 0.25)
        return canvas.crop((0, 0, max(1, used), height)), used


def _tinted(mask: "Image.Image", color: Tuple[int, int, int]) -> "Image.Image":
    layer = Image.new("RGBA", mask.size, color + (0,))
    layer.putalpha(mask)
    return layer


# ---------------------------------------------------------------------------
# public helpers
# ---------------------------------------------------------------------------


def shaping_available() -> Tuple[bool, str]:
    """(kaam karega?, wajah) — UI me dikhane ke liye."""
    lib = load_harfbuzz()
    if lib is None:
        return False, f"libharfbuzz nahi mili ({_HB_ERROR})"
    return True, f"harfbuzz: {harfbuzz_path()}"


def make_renderer(latin_font: str, devanagari_font: Optional[str]) -> Optional[TextRenderer]:
    try:
        return TextRenderer(latin_font, devanagari_font)
    except Exception as exc:
        print(f"  (text shaping off: {exc})", file=sys.stderr)
        return None


if __name__ == "__main__":
    # Chhota self-check: shaping theek kaam kar rahi hai ya nahi.
    here = os.path.dirname(os.path.abspath(__file__))
    fonts = os.path.join(here, "fonts")
    latin = os.path.join(fonts, "NotoSans-Bold.ttf")
    dev = os.path.join(fonts, "NotoSansDevanagari-Bold.ttf")
    print("shaping:", shaping_available())
    if not os.path.exists(latin):
        print("Pehle chalaiye: python3 get_fonts.py")
        raise SystemExit(1)
    renderer = make_renderer(latin, dev if os.path.exists(dev) else None)
    if renderer is None:
        raise SystemExit(1)

    samples = ["कि", "हिन्दी", "क्ष", "त्र", "ज्ञ", "श्री", "हिंदी में लिखें", "Hello कि!", "रुपये 500"]
    width = 900
    line_height = 80
    sheet = Image.new("RGB", (width, line_height * len(samples) + 20), "white")
    for i, text in enumerate(samples):
        image, used = renderer.render_line(text, 52, (10, 10, 10))
        sheet.paste(image, (20, 10 + i * line_height), image)
        print(f"  {text!r:28s} width={used:4d}px")
    out = os.path.join(here, "shaping-test.png")
    sheet.save(out)
    print("Test image:", out)
