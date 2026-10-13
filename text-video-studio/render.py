"""
render.py — text se video banane wala engine.

Koi credit nahi, koi limit nahi, koi internet nahi: video aapke hi device par
banta hai. PIL se frames bante hain, ffmpeg se MP4.

Command line:
    python3 render.py --demo                      # sample video banao
    python3 render.py project.json -o out.mp4     # apni project file se
    python3 render.py --check                     # sab kuch theek hai ya nahi

Library:
    from render import render_video
    render_video(project_dict, "out.mp4", progress=lambda f, m: print(f, m))
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover - checked again in check_environment()
    Image = None  # type: ignore

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")

try:
    import textshape  # Hindi/complex-script shaping (HarfBuzz + apna rasterizer)
except Exception:  # pragma: no cover
    textshape = None  # type: ignore

SAMPLE_RATE = 44100

ASPECTS = {
    "9:16": {"high": (1080, 1920), "medium": (720, 1280), "fast": (540, 960)},
    "16:9": {"high": (1920, 1080), "medium": (1280, 720), "fast": (960, 540)},
    "1:1": {"high": (1080, 1080), "medium": (720, 720), "fast": (540, 540)},
}

THEMES = {
    "midnight": {"bg": "#0b1020", "bg2": "#1b2b52", "accent": "#ff3b6b", "text": "#ffffff", "sub": "#c9d4e6"},
    "sunrise": {"bg": "#2b1055", "bg2": "#ff7b54", "accent": "#ffd166", "text": "#ffffff", "sub": "#ffe9c9"},
    "forest": {"bg": "#062b1e", "bg2": "#0f6b4a", "accent": "#8ef2b7", "text": "#ffffff", "sub": "#c8f2dc"},
    "paper": {"bg": "#f4f1ea", "bg2": "#dcd6c8", "accent": "#c0392b", "text": "#141414", "sub": "#4a4a4a"},
    "royal": {"bg": "#1a0033", "bg2": "#5b0ea6", "accent": "#ffd60a", "text": "#ffffff", "sub": "#e6d5ff"},
}


class RenderError(Exception):
    """Kabhi bhi user ko saaf message dikhane ke liye."""


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------


def find_ffmpeg() -> Optional[str]:
    """ffmpeg dhoondho: PATH me, ya imageio-ffmpeg ke saath aaya hua."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def find_espeak() -> Optional[str]:
    for name in ("espeak-ng", "espeak"):
        exe = shutil.which(name)
        if exe:
            return exe
    return None


def check_environment(verbose: bool = True) -> Dict[str, object]:
    ok = True
    info: Dict[str, object] = {}

    has_pil = Image is not None
    info["pillow"] = has_pil
    if not has_pil:
        ok = False

    ffmpeg = find_ffmpeg()
    info["ffmpeg"] = ffmpeg
    if not ffmpeg:
        ok = False

    espeak = find_espeak()
    info["espeak"] = espeak  # optional

    fonts = list_fonts()
    info["fonts"] = fonts
    info["has_devanagari"] = any("devanagari" in f.lower() for f in fonts)

    if Image is not None:
        try:
            from PIL import features  # type: ignore

            info["raqm"] = bool(features.check("raqm"))
        except Exception:
            info["raqm"] = False
    else:
        info["raqm"] = False

    if textshape is not None:
        available, reason = textshape.shaping_available()
    else:
        available, reason = False, "textshape module nahi mila"
    info["shaping"] = available
    info["shaping_detail"] = reason

    info["ok"] = ok
    if verbose:
        print("Environment check")
        print(f"  pillow          : {'ok' if has_pil else 'MISSING'}")
        if not has_pil:
            print("      Termux: pkg install python-pillow   (ya: pip install pillow)")
        print(f"  ffmpeg          : {ffmpeg or 'MISSING'}")
        if not ffmpeg:
            print("      Termux: pkg install ffmpeg")
            print("      Windows/Mac: https://ffmpeg.org/download.html  (ya: pip install imageio-ffmpeg)")
        print(f"  hindi fonts     : {'ok' if info['has_devanagari'] else 'nahi mile'}")
        if not info["has_devanagari"]:
            print("      Chalaiye: python3 get_fonts.py")
        print(f"  hindi shaping   : {'ok (HarfBuzz)' if info['shaping'] else 'NAHI MILI'}")
        if not info["shaping"]:
            print(f"      wajah: {info.get('shaping_detail', '')}")
            print("      Termux: pkg install harfbuzz     |  Desktop: pip install --upgrade pillow")
            print("      (isse Hindi ke akshar galat dikh sakte hain)")
        print(f"  voiceover       : {espeak or 'nahi hai (optional)'}")
        if not espeak:
            print("      Termux: pkg install espeak")
        print(f"  font files      : {len(fonts)}")
    return info


def list_fonts() -> List[str]:
    if not os.path.isdir(FONT_DIR):
        return []
    return sorted(f for f in os.listdir(FONT_DIR) if f.lower().endswith((".ttf", ".otf")))


def pick_fonts(wanted: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """(latin_font, devanagari_font) — dono ka path, jo mile."""
    fonts = list_fonts()
    if not fonts:
        return None, None
    if wanted:
        chosen = next((f for f in fonts if wanted.lower() in f.lower()), None)
        if chosen:
            return os.path.join(FONT_DIR, chosen), os.path.join(FONT_DIR, chosen)

    def first(predicate):
        for f in fonts:
            if predicate(f):
                return os.path.join(FONT_DIR, f)
        return None

    latin = first(lambda f: "devanagari" not in f.lower() and "bold" in f.lower()) \
        or first(lambda f: "devanagari" not in f.lower())
    dev = first(lambda f: "devanagari" in f.lower() and "bold" in f.lower()) \
        or first(lambda f: "devanagari" in f.lower())
    return latin, dev


def pick_font(text: str, wanted: Optional[str] = None) -> Optional[str]:
    """Text ke hisaab se font chuno: Devanagari ho to Devanagari font."""
    fonts = list_fonts()
    if not fonts:
        return None

    if wanted:
        for f in fonts:
            if wanted.lower() in f.lower():
                return os.path.join(FONT_DIR, f)

    devanagari = any("\u0900" <= ch <= "\u097f" for ch in text)
    if devanagari:
        for f in fonts:
            if "devanagari" in f.lower() and "bold" in f.lower():
                return os.path.join(FONT_DIR, f)
        for f in fonts:
            if "devanagari" in f.lower():
                return os.path.join(FONT_DIR, f)

    for f in fonts:
        if "devanagari" not in f.lower() and "bold" in f.lower():
            return os.path.join(FONT_DIR, f)
    for f in fonts:
        if "devanagari" not in f.lower():
            return os.path.join(FONT_DIR, f)
    return os.path.join(FONT_DIR, fonts[0])


# ---------------------------------------------------------------------------
# project defaults
# ---------------------------------------------------------------------------


def normalise_project(project: Dict) -> Dict:
    p = dict(project or {})
    aspect = str(p.get("aspect", "9:16")).strip()
    if aspect not in ASPECTS:
        aspect = "9:16"
    p["aspect"] = aspect

    quality = str(p.get("quality", "medium")).lower()
    if quality not in ("high", "medium", "fast"):
        quality = "medium"
    p["quality"] = quality

    try:
        p["fps"] = max(12, min(60, int(p.get("fps", 30))))
    except (TypeError, ValueError):
        p["fps"] = 30

    theme_name = str(p.get("theme", "midnight"))
    p["theme_name"] = theme_name if theme_name in THEMES else "midnight"
    theme = dict(THEMES[p["theme_name"]])
    for key in ("bg", "bg2", "accent", "text", "sub"):
        if p.get("theme_overrides", {}).get(key):
            theme[key] = p["theme_overrides"][key]
    p["theme"] = theme

    scenes = []
    for raw in p.get("scenes", []) or []:
        if isinstance(raw, str):
            raw = {"text": raw}
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text", "")).strip()
        image = raw.get("image")
        if not text and not image:
            continue
        try:
            duration = float(raw.get("duration", 3.5))
        except (TypeError, ValueError):
            duration = 3.5
        scenes.append(
            {
                "text": text,
                "sub": str(raw.get("sub", "")).strip(),
                "image": image,
                "duration": max(0.8, min(60.0, duration)),
                "animation": str(raw.get("animation", "slide-up")),
                "transition": str(raw.get("transition", "fade")),
                "text_position": str(raw.get("text_position", "center")),
            }
        )
    if not scenes:
        raise RenderError("Kam se kam ek scene chahiye (scenes list khaali hai).")
    p["scenes"] = scenes

    voice = p.get("voiceover") or {}
    if voice and str(voice.get("engine", "")).lower() == "espeak":
        p["voiceover"] = {
            "engine": "espeak",
            "voice": str(voice.get("voice", "hi")),
            "speed": int(voice.get("speed", 150) or 150),
            "auto_extend": bool(voice.get("auto_extend", True)),
        }
    else:
        p["voiceover"] = None

    try:
        p["music_volume"] = max(0.0, min(1.0, float(p.get("music_volume", 0.3))))
    except (TypeError, ValueError):
        p["music_volume"] = 0.3
    return p


# ---------------------------------------------------------------------------
# drawing helpers
# ---------------------------------------------------------------------------


def hex_rgb(value: str) -> Tuple[int, int, int]:
    value = (value or "#000000").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore
    except (ValueError, IndexError):
        return (0, 0, 0)


def mix(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore


def vertical_gradient(size: Tuple[int, int], top: str, bottom: str) -> "Image.Image":
    """1-pixel wide gradient ko stretch karke smooth gradient banate hain."""
    w, h = size
    strip = Image.new("RGB", (1, h))
    top_rgb, bottom_rgb = hex_rgb(top), hex_rgb(bottom)
    for y in range(h):
        strip.putpixel((0, y), mix(top_rgb, bottom_rgb, y / max(1, h - 1)))
    return strip.resize((w, h), Image.BILINEAR)


def radial_glow(radius: int, color: Tuple[int, int, int]) -> "Image.Image":
    """Narm chamak (soft glow): chhote RGBA par circles, phir blur."""
    size = radius * 2
    small = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(small)
    for i in range(20, 0, -1):
        r = int(31 * i / 20)
        alpha = int(46 * (1 - i / 20) ** 0.7)
        draw.ellipse([32 - r, 32 - r, 32 + r, 32 + r], fill=color + (alpha,))
    return small.resize((size, size), Image.BICUBIC).filter(ImageFilter.GaussianBlur(radius * 0.12))



class TextPainter:
    """
    Text ko image par likhne wala. Agar HarfBuzz mil jaye to apna shaping
    engine (textshape) use hota hai — Hindi theek dikhti hai. Warna Pillow ka
    simple layout chal jaata hai (Latin theek, Hindi adhoori).
    """

    def __init__(self, latin_path: Optional[str], dev_path: Optional[str]):
        self.latin_path = latin_path
        self.renderer = None
        if textshape is not None and latin_path:
            try:
                self.renderer = textshape.make_renderer(latin_path, dev_path)
            except Exception:
                self.renderer = None
        self._pil_fonts: Dict[int, "ImageFont.FreeTypeFont"] = {}

    @property
    def shaped(self) -> bool:
        return self.renderer is not None

    def pil_font(self, size: int) -> "ImageFont.FreeTypeFont":
        size = max(8, int(size))
        if size not in self._pil_fonts:
            self._pil_fonts[size] = load_font(self.latin_path, size)
        return self._pil_fonts[size]

    # -- measurement --
    def measure(self, text: str, size: int) -> float:
        if self.renderer is not None:
            try:
                return float(self.renderer.measure(text, size))
            except Exception:
                pass
        font = self.pil_font(size)
        try:
            return float(font.getlength(text))
        except Exception:
            return len(text) * size * 0.55

    def wrap(self, text: str, size: int, max_width: int) -> List[str]:
        words = str(text).replace("\r", " ").replace("\n", " ").split(" ")
        lines: List[str] = []
        current = ""
        space = self.measure(" ", size)
        for word in words:
            if not current:
                candidate = word
                width = self.measure(candidate, size)
            else:
                candidate = current + " " + word
                width = self.measure(current, size) + space + self.measure(word, size)
            if width <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or [""]

    # -- drawing --
    def draw_line(self, canvas: "Image.Image", text: str, size: int,
                  color: Tuple[int, int, int], top: int) -> float:
        """Ek line ko canvas par beech me likhta hai. Return: line ki height."""
        line_height = size * 1.3
        if self.renderer is not None:
            try:
                image, used = self.renderer.render_line(text, size, color)
                x = int((canvas.width - used) / 2)
                target = top + int((line_height - image.height) / 2)
                canvas.alpha_composite(image, (max(0, x), max(0, target)))
                return line_height
            except Exception:
                pass
        draw = ImageDraw.Draw(canvas)
        font = self.pil_font(size)
        try:
            width = draw.textlength(text, font=font)
        except Exception:
            width = len(text) * size * 0.55
        draw.text((int((canvas.width - width) / 2), top), text, font=font, fill=color)
        return line_height

    def block_height(self, lines: Sequence[str], size: int) -> int:
        return int(len(lines) * size * 1.3)


def load_font(path: Optional[str], size: int) -> "ImageFont.FreeTypeFont":
    size = max(8, int(size))
    if path and os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()  # ASCII-only fallback


def wrap_text(text: str, font, max_width: int, draw) -> List[str]:
    words = text.replace("\r", " ").replace("\n", " ").split(" ")
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = word if not current else current + " " + word
        try:
            width = draw.textlength(candidate, font=font)
        except Exception:
            width = len(candidate) * font.size * 0.55
        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def fit_text(text, sub, size_hint, canvas_w, painter, max_lines=7):
    """Text ko canvas ki chaudai me fit hone tak font chhota karte hain."""
    size = size_hint
    while size > 14:
        lines = painter.wrap(text, size, int(canvas_w * 0.86)) if text else []
        sub_lines = painter.wrap(sub, int(size * 0.58), int(canvas_w * 0.86)) if sub else []
        if len(lines) <= max_lines:
            return size, int(size * 0.58), lines, sub_lines
        size = int(size * 0.92)
    return 18, 12, painter.wrap(text, 18, int(canvas_w * 0.86)), []


def cover_crop(image: "Image.Image", size: Tuple[int, int], zoom: float, pan_x: float, pan_y: float) -> "Image.Image":
    """Image ko canvas jitna bada karo (crop-to-cover) + halka zoom/pan."""
    w, h = size
    img = image.convert("RGB")
    scale = max(w / img.width, h / img.height) * zoom
    new_w, new_h = max(w, int(img.width * scale + 0.5)), max(h, int(img.height * scale + 0.5))
    img = img.resize((new_w, new_h), Image.BICUBIC)
    max_dx, max_dy = max(0, new_w - w), max(0, new_h - h)
    left = int(max_dx * (0.5 + pan_x * 0.5))
    top = int(max_dy * (0.5 + pan_y * 0.5))
    return img.crop((left, top, left + w, top + h))


def darken_for_text(image: "Image.Image", strength: float = 0.45) -> "Image.Image":
    """Text padhne layak banane ke liye neeche-upar gradient shade."""
    w, h = image.size
    shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    for y in range(h):
        # center me halka, kinaron par zyada
        edge = abs(y / h - 0.5) * 2
        alpha = int(255 * strength * (0.25 + 0.75 * edge ** 1.6))
        draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
    return Image.alpha_composite(image.convert("RGBA"), shade).convert("RGB")


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------


@dataclass
class ScenePrep:
    scene: Dict
    frames: int
    base_image: Optional["Image.Image"] = None
    bg_top: Optional["Image.Image"] = None
    bg_bottom: Optional["Image.Image"] = None
    glow: Optional["Image.Image"] = None
    font_path: Optional[str] = None
    painter: Optional["TextPainter"] = None
    drawn: bool = False
    notes: List[str] = field(default_factory=list)


def prepare_scene(scene: Dict, size: Tuple[int, int], fps: int, theme: Dict,
                  font_path: Optional[str], painter: Optional["TextPainter"] = None) -> ScenePrep:
    frames = max(1, int(round(scene["duration"] * fps)))
    prep = ScenePrep(scene=scene, frames=frames, font_path=font_path, painter=painter)

    image = scene.get("image")
    if image and os.path.exists(image):
        try:
            loaded = Image.open(image)
            loaded.load()
            prep.base_image = darken_for_text(cover_crop(loaded, size, 1.0, 0.0, 0.0))
        except Exception as exc:  # pragma: no cover
            prep.notes.append(f"Image nahi khul payi ({os.path.basename(image)}): {exc}")
    elif image:
        prep.notes.append(f"Image file nahi mili: {image}")

    if prep.base_image is None:
        prep.bg_top = vertical_gradient(size, theme["bg"], theme["bg2"])
        prep.bg_bottom = vertical_gradient(size, theme["bg2"], theme["bg"])
        prep.glow = radial_glow(int(min(size) * 0.9), hex_rgb(theme["accent"]))
    return prep


def render_frame(prep: ScenePrep, index: int, size: Tuple[int, int], theme: Dict, fps: int) -> "Image.Image":
    w, h = size
    t = index / max(1, prep.frames - 1)
    scene = prep.scene

    if prep.base_image is not None:
        zoom = 1.0 + 0.06 * t
        pan_x = -0.12 + 0.24 * t
        frame = cover_crop(prep.base_image, size, zoom, pan_x, 0.08 * math.sin(math.pi * t))
    else:
        # "saans leta hua" gradient + dheere ghoomta glow
        breathe = 0.5 + 0.5 * math.sin(2 * math.pi * t)
        frame = Image.blend(prep.bg_top, prep.bg_bottom, breathe * 0.55).convert("RGB")
        glow = prep.glow
        angle = 2 * math.pi * t
        gx = int(w / 2 + math.cos(angle) * w * 0.22 - glow.width / 2)
        gy = int(h / 2 + math.sin(angle) * h * 0.16 - glow.height / 2)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.paste(glow.resize((int(glow.width * 1.3), int(glow.height * 1.3))), (gx, gy), glow.resize((int(glow.width * 1.3), int(glow.height * 1.3))))
        frame = Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB")

    if scene["text"] or scene["sub"]:
        frame = draw_text_layer(frame, prep, t, theme, size, fps)
    return frame


def draw_text_layer(frame: "Image.Image", prep: ScenePrep, t: float, theme: Dict,
                    size: Tuple[int, int], fps: int) -> "Image.Image":
    w, h = size
    scene = prep.scene
    painter = prep.painter
    if painter is None:
        return frame

    size_hint = int(min(w, h) * 0.085)
    font_size, sub_size, lines, sub_lines = fit_text(
        scene["text"], scene["sub"], size_hint, w, painter
    )

    line_h = font_size * 1.3
    sub_h = sub_size * 1.35
    block_h = line_h * len(lines) + (sub_h * len(sub_lines) + font_size * 0.45 if sub_lines else 0)

    position = scene.get("text_position", "center")
    if position == "top":
        block_top = int(h * 0.16)
    elif position == "bottom":
        block_top = int(h * 0.72) - block_h
    else:
        block_top = int((h - block_h) / 2)

    animation = scene.get("animation", "slide-up")
    appear = min(1.0, t / max(0.001, 0.35))
    alpha = 1.0

    offset_y = 0.0
    if animation == "slide-up":
        offset_y = (1 - appear) * font_size * 1.6
    elif animation == "pop":
        pass  # neeche scale lagta hai
    elif animation == "typewriter":
        total_chars = max(1, sum(len(x) for x in lines))
        shown = int(total_chars * min(1.0, t / 0.75))
        counted = 0
        new_lines = []
        for line in lines:
            if counted >= shown:
                break
            take = min(len(line), shown - counted)
            new_lines.append(line[:take])
            counted += len(line)
        lines = new_lines or [""]
    elif animation == "none":
        appear = 1.0

    text_color = hex_rgb(theme["text"])
    sub_color = hex_rgb(theme["sub"])

    # Text ko apni layer par likhte hain, taaki fade/pop ke waqt poora block ek saath chale
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y = int(block_top + offset_y)
    for line in lines:
        y += int(painter.draw_line(layer, line, font_size, text_color, y))
    if sub_lines:
        y += int(font_size * 0.45)
        for line in sub_lines:
            y += int(painter.draw_line(layer, line, sub_size, sub_color, y))

    if animation == "pop":
        scale = 0.86 + 0.14 * appear
        scaled = layer.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BICUBIC)
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        layer.alpha_composite(scaled, (int((w - scaled.width) / 2), int((h - scaled.height) / 2)))

    if appear < 1.0 or alpha < 1.0:
        factor = appear * alpha
        channel = layer.getchannel("A").point(lambda v: int(v * factor))
        layer.putalpha(channel)

    return Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB")


def blend_transition(current: "Image.Image", previous: "Image.Image", progress: float, kind: str) -> "Image.Image":
    progress = max(0.0, min(1.0, progress))
    if kind == "none" or progress >= 1.0:
        return current
    if kind == "slide":
        w, h = current.size
        shifted = previous.resize((w, h)).transform(
            (w, h), Image.AFFINE, (1, 0, int(-w * progress), 0, 1, 0), resample=Image.BILINEAR
        )
        layer = Image.new("RGB", (w, h))
        layer.paste(shifted, (0, 0))
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rectangle([int(w * (1 - progress)), 0, w, h], fill=255)
        layer.paste(current, (0, 0), mask)
        return layer
    if kind == "zoom":
        w, h = current.size
        zoomed = previous.resize((int(w * (1 + 0.06 * progress)), int(h * (1 + 0.06 * progress))), Image.BICUBIC)
        left = (zoomed.width - w) // 2
        top = (zoomed.height - h) // 2
        zoomed = zoomed.crop((left, top, left + w, top + h))
        return Image.blend(zoomed, current, progress)
    return Image.blend(previous, current, progress)  # fade


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------


def wav_duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as fh:
            return fh.getnframes() / float(fh.getframerate())
    except Exception:
        return 0.0


def read_wav_mono(path: str) -> Tuple[List[float], int]:
    with wave.open(path, "rb") as fh:
        channels = fh.getnchannels()
        width = fh.getsampwidth()
        rate = fh.getframerate()
        frames = fh.readframes(fh.getnframes())
    samples: List[float] = []
    if width == 2:
        data = array("h")
        data.frombytes(frames)
        if channels == 2:
            for i in range(0, len(data) - 1, 2):
                samples.append((data[i] + data[i + 1]) / 65536.0)
        else:
            samples = [v / 32768.0 for v in data]
    elif width == 1:
        samples = [(b - 128) / 128.0 for b in frames[::channels]]
    else:
        raise RenderError("Sirf 8 ya 16-bit WAV support hai.")
    return samples, rate


def resample(samples: Sequence[float], src_rate: int, dst_rate: int) -> List[float]:
    if src_rate == dst_rate or not samples:
        return list(samples)
    ratio = dst_rate / float(src_rate)
    out_len = max(1, int(len(samples) * ratio))
    out = [0.0] * out_len
    for i in range(out_len):
        pos = i / ratio
        i0 = int(pos)
        i1 = min(i0 + 1, len(samples) - 1)
        frac = pos - i0
        out[i] = samples[i0] * (1 - frac) + samples[i1] * frac
    return out


def generate_voiceover(scene_text: str, voice: str, speed: int, workdir: str, index: int) -> Optional[str]:
    espeak = find_espeak()
    if not espeak or not scene_text.strip():
        return None
    path = os.path.join(workdir, f"vo_{index:03d}.wav")
    try:
        subprocess.run(
            [espeak, "-v", voice or "hi", "-s", str(speed), "-w", path, scene_text],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return path if os.path.exists(path) and os.path.getsize(path) > 44 else None


def decode_audio(path: str, workdir: str) -> Optional[str]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg or not path or not os.path.exists(path):
        return None
    out = os.path.join(workdir, "music_decoded.wav")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE), out],
            check=True,
        )
    except Exception:
        return None
    return out if os.path.exists(out) else None


def build_audio(project: Dict, scene_starts: Sequence[float], total: float, workdir: str) -> Optional[str]:
    """Sab audio (voiceover + music) ko ek WAV me milate hain."""
    total_samples = int(total * SAMPLE_RATE) + SAMPLE_RATE // 2
    track = [0.0] * total_samples
    anything = False

    voice = project.get("voiceover")
    if voice and voice.get("engine") == "espeak":
        for i, scene in enumerate(project["scenes"]):
            wav = generate_voiceover(scene["text"], voice.get("voice", "hi"), voice.get("speed", 150), workdir, i)
            if not wav:
                continue
            samples, rate = read_wav_mono(wav)
            samples = resample(samples, rate, SAMPLE_RATE)
            start = int(scene_starts[i] * SAMPLE_RATE)
            for j, value in enumerate(samples):
                pos = start + j
                if 0 <= pos < total_samples:
                    track[pos] += value * 0.95
            anything = True

    music = project.get("music")
    if music:
        decoded = decode_audio(music, workdir)
        if decoded:
            samples, rate = read_wav_mono(decoded)
            samples = resample(samples, rate, SAMPLE_RATE)
            if samples:
                volume = float(project.get("music_volume", 0.3))
                for pos in range(total_samples):
                    track[pos] += samples[pos % len(samples)] * volume
                anything = True

    if not anything:
        return None

    # halka limiter, phir 16-bit WAV
    peak = max(1e-6, max(abs(v) for v in track))
    gain = min(1.0, 0.95 / peak)
    data = array("h", (int(max(-1.0, min(1.0, v * gain)) * 32767) for v in track))
    out = os.path.join(workdir, "mixed.wav")
    with wave.open(out, "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SAMPLE_RATE)
        fh.writeframes(data.tobytes())
    return out


# ---------------------------------------------------------------------------
# main render
# ---------------------------------------------------------------------------


def render_video(
    project: Dict,
    out_path: str,
    progress: Optional[Callable[[float, str], None]] = None,
    workdir: Optional[str] = None,
) -> Dict:
    """Project dict se MP4 banao. Return: {'output','duration','frames','warnings'}"""
    def report(frac: float, message: str) -> None:
        if progress:
            try:
                progress(max(0.0, min(1.0, frac)), message)
            except Exception:
                pass

    if Image is None:
        raise RenderError(
            "Pillow install nahi hai. Termux: pkg install python-pillow — ya: pip install pillow"
        )
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RenderError(
            "ffmpeg nahi mila. Termux: pkg install ffmpeg — PC: ffmpeg.org se install kariye "
            "(ya: pip install imageio-ffmpeg)"
        )

    project = normalise_project(project)
    width, height = ASPECTS[project["aspect"]][project["quality"]]
    fps = project["fps"]
    theme = project["theme"]
    warnings: List[str] = []

    temp_dir = workdir or tempfile.mkdtemp(prefix="tvs-")
    os.makedirs(temp_dir, exist_ok=True)
    keep_temp = workdir is not None

    # Voiceover pehle banao, taaki scene ki lambai usi hisaab se badha sakein.
    voice = project.get("voiceover")
    if voice and voice.get("engine") == "espeak" and not find_espeak():
        warnings.append(
            "Voiceover ke liye espeak nahi mila — video bina awaaz ke banega. "
            "Termux: pkg install espeak"
        )
        project["voiceover"] = None
        voice = None

    if voice:
        report(0.02, "Voiceover bana raha hoon...")
        for i, scene in enumerate(project["scenes"]):
            wav = generate_voiceover(scene["text"], voice.get("voice", "hi"), voice.get("speed", 150), temp_dir, i)
            if wav and voice.get("auto_extend", True):
                needed = wav_duration(wav) + 0.45
                if needed > scene["duration"]:
                    scene["duration"] = min(60.0, needed)

    total_duration = sum(s["duration"] for s in project["scenes"])
    scene_starts: List[float] = []
    cursor = 0.0
    for scene in project["scenes"]:
        scene_starts.append(cursor)
        cursor += scene["duration"]

    report(0.04, f"{len(project['scenes'])} scene taiyaar kar raha hoon...")
    all_text = " ".join(s["text"] + " " + s["sub"] for s in project["scenes"])
    latin_font, dev_font = pick_fonts(project.get("font"))
    if latin_font is None and dev_font is None:
        warnings.append(
            "Koi font file nahi mili — default font use ho raha hai. Chalaiye: python3 get_fonts.py"
        )
    painter = TextPainter(latin_font, dev_font)
    if not painter.shaped and any("\u0900" <= ch <= "\u097f" for ch in all_text):
        warnings.append(
            "Hindi shaping engine (HarfBuzz) nahi mila — Hindi akshar galat dikh sakte hain. "
            "Termux: pkg install harfbuzz   |   Desktop: pip install --upgrade pillow"
        )
    if painter.shaped:
        print("  (=) text shaping: HarfBuzz — Hindi sahi banegi")

    preps = [
        prepare_scene(scene, (width, height), fps, theme, latin_font, painter)
        for scene in project["scenes"]
    ]
    for prep in preps:
        warnings.extend(prep.notes)
    total_frames = sum(p.frames for p in preps)

    audio_path = build_audio(project, scene_starts, total_duration, temp_dir)
    if project.get("music") and not audio_path:
        warnings.append("Background music file padhi nahi ja saki — video bina music ke banega.")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
    ]
    if audio_path:
        cmd += ["-i", audio_path]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ]
    if audio_path:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd += [out_path]

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    done = 0
    previous_last: Optional["Image.Image"] = None
    transition_frames = max(1, int(0.45 * fps))
    try:
        for prep in preps:
            for index in range(prep.frames):
                frame = render_frame(prep, index, (width, height), theme, fps)
                if previous_last is not None and index < transition_frames:
                    frame = blend_transition(frame, previous_last, (index + 1) / transition_frames, prep.scene["transition"])
                proc.stdin.write(frame.tobytes())
                done += 1
                if done % 5 == 0 or done == total_frames:
                    report(0.05 + 0.9 * done / total_frames, f"Frame {done}/{total_frames}")
            previous_last = frame
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass

    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    code = proc.wait()
    if code != 0:
        raise RenderError(f"ffmpeg fail hua (exit {code}):\n{stderr.strip()[:800]}")

    if not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        raise RenderError("Video file bani hi nahi — ffmpeg ka output khaali hai.")

    if not keep_temp:
        shutil.rmtree(temp_dir, ignore_errors=True)

    report(1.0, "Ban gaya!")
    return {
        "output": out_path,
        "duration": round(total_duration, 2),
        "frames": total_frames,
        "size": os.path.getsize(out_path),
        "resolution": f"{width}x{height}",
        "warnings": warnings,
        "audio": bool(audio_path),
    }


def write_demo_project(path: str) -> str:
    project = {
        "title": "Demo",
        "aspect": "9:16",
        "quality": "medium",
        "fps": 30,
        "theme": "midnight",
        "music_volume": 0.3,
        "scenes": [
            {"text": "Text se video, bilkul free", "sub": "koi credit nahi, koi limit nahi", "duration": 3.0},
            {"text": "Aapke phone par hi banta hai", "sub": "Termux + Python + ffmpeg", "duration": 3.0, "animation": "pop"},
            {"text": "Apna text likhiye, video le lijiye", "sub": "python3 studio.py", "duration": 3.5, "animation": "typewriter", "theme": None},
        ],
    }
    project["scenes"] = [{k: v for k, v in s.items() if v is not None} for s in project["scenes"]]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(project, fh, indent=2, ensure_ascii=False)
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="render.py", description="Text se video banaiye — offline, free, unlimited.")
    parser.add_argument("project", nargs="?", help="project JSON file")
    parser.add_argument("-o", "--out", default="video.mp4", help="output MP4 (default: video.mp4)")
    parser.add_argument("--demo", action="store_true", help="sample project bana kar render karo")
    parser.add_argument("--check", action="store_true", help="environment check karo")
    args = parser.parse_args(argv)

    if args.check:
        return 0 if check_environment().get("ok") else 1

    if args.demo:
        path = write_demo_project(os.path.join(HERE, "demo-project.json"))
        print(f"Demo project likha: {path}")
        with open(path, encoding="utf-8") as fh:
            project = json.load(fh)
    elif args.project:
        with open(args.project, encoding="utf-8") as fh:
            project = json.load(fh)
    else:
        parser.print_help()
        return 2

    def progress(frac: float, message: str) -> None:
        bar = int(frac * 30)
        sys.stdout.write(f"\r[{'#' * bar}{'.' * (30 - bar)}] {frac * 100:5.1f}%  {message[:40]:<40}")
        sys.stdout.flush()

    try:
        result = render_video(project, args.out, progress=progress)
    except RenderError as exc:
        print(f"\nRuk gaya: {exc}")
        return 1
    print()
    print(f"Video: {result['output']}")
    print(f"  {result['resolution']} · {result['duration']}s · {result['frames']} frames · {result['size'] / 1024:.0f} KB")
    for warning in result["warnings"]:
        print(f"  dhyan dijiye: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
