#!/usr/bin/env python3
"""
studio.py — text se video banane ka web studio.

Chalaiye:  python3 studio.py
Phir browser me:  http://localhost:8000

Koi login nahi, koi credit nahi, koi limit nahi. Sab kuch aapke device par.
Termux me bhi wahi command — phir phone ke browser me wahi address kholiye.

Sirf Python standard library (PIL aur ffmpeg render.py use karta hai).
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render as R  # noqa: E402

WEB_DIR = os.path.join(HERE, "web")
PROJECTS_DIR = os.path.join(HERE, "projects")
MAX_BODY = 64 * 1024 * 1024  # 64 MB (screenshots + music ke liye kaafi)

JOBS: Dict[str, Dict] = {}
JOBS_LOCK = threading.Lock()


def safe_name(name: str, fallback: str = "file") -> str:
    name = os.path.basename(str(name or fallback))
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name[:80] or fallback


def save_data_url(data_url: str, folder: str, name_hint: str) -> Optional[str]:
    """'data:image/png;base64,....' ko file bana kar path return karo."""
    if not data_url or not isinstance(data_url, str) or not data_url.startswith("data:"):
        return None
    try:
        header, payload = data_url.split(",", 1)
        if ";base64" not in header:
            return None
        blob = base64.b64decode(payload, validate=False)
    except Exception:
        return None
    if not blob:
        return None
    ext = ".bin"
    if "image/png" in header:
        ext = ".png"
    elif "image/jpeg" in header or "image/jpg" in header:
        ext = ".jpg"
    elif "image/webp" in header:
        ext = ".webp"
    elif "audio/" in header:
        ext = ".mp3" if "mpeg" in header else ".wav"
    elif "video/" in header:
        ext = ".mp4"
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, safe_name(name_hint + ext, "upload" + ext))
    with open(path, "wb") as fh:
        fh.write(blob)
    return path


def materialise(project: Dict, workdir: str) -> Dict:
    """UI se aaye data-URL images/music ko asli files me badlo."""
    project = json.loads(json.dumps(project))  # deep copy
    for index, scene in enumerate(project.get("scenes", [])):
        image = scene.get("image")
        if isinstance(image, str) and image.startswith("data:"):
            scene["image"] = save_data_url(image, workdir, f"scene{index + 1}")
        elif isinstance(image, str) and not image.startswith("data:"):
            # server par pehle se maujood path hi rakhna hai, par sirf apne folder ke andar
            candidate = os.path.abspath(image)
            if not candidate.startswith(HERE):
                scene["image"] = None
    music = project.get("music")
    if isinstance(music, str) and music.startswith("data:"):
        project["music"] = save_data_url(music, workdir, "music")
    return project


def run_job(job_id: str, project: Dict, out_path: str) -> None:
    def progress(frac: float, message: str) -> None:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id].update(progress=round(frac, 4), message=message, state="rendering")

    workdir = os.path.join(PROJECTS_DIR, job_id)
    os.makedirs(workdir, exist_ok=True)
    try:
        with JOBS_LOCK:
            JOBS[job_id].update(state="preparing", message="Taiyaari...")
        project = materialise(project, workdir)
        result = R.render_video(project, out_path, progress=progress, workdir=workdir)
        with JOBS_LOCK:
            JOBS[job_id].update(state="done", progress=1.0, message="Ban gaya!",
                                result=result, output=out_path)
    except R.RenderError as exc:
        with JOBS_LOCK:
            JOBS[job_id].update(state="error", message=str(exc), progress=1.0)
    except Exception as exc:  # pragma: no cover
        with JOBS_LOCK:
            JOBS[job_id].update(state="error", progress=1.0,
                                message=f"{exc}\n{traceback.format_exc()[-600:]}")


def preview_still(project: Dict, workdir: str) -> bytes:
    """Pehle scene ka ek still PNG (preview ke liye) — render se bahut tez."""
    from PIL import Image  # local import: sirf preview me chahiye

    project = R.normalise_project(project)
    project = materialise(project, workdir)
    width, height = R.ASPECTS[project["aspect"]][project["quality"]]
    latin_font, dev_font = R.pick_fonts(project.get("font"))
    painter = R.TextPainter(latin_font, dev_font)
    prep = R.prepare_scene(project["scenes"][0], (width, height), project["fps"], project["theme"],
                           latin_font, painter)
    frame = R.render_frame(prep, int(prep.frames * 0.75), (width, height), project["theme"], project["fps"])
    import io

    buffer = io.BytesIO()
    preview = frame.copy()
    preview.thumbnail((720, 720))
    preview.save(buffer, "PNG")
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "TextVideoStudio/1.0"

    # ---- helpers ----
    def log_message(self, fmt: str, *args) -> None:  # quiet-ish logging
        if os.environ.get("TVS_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def send_json(self, payload: Dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, blob: bytes, content_type: str, download_name: Optional[str] = None) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Cache-Control", "no-store")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(blob)

    def read_json(self) -> Dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("Bheja gaya data bahut bada hai (64 MB se zyada).")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def send_file(self, path: str, content_type: str, download_name: Optional[str] = None) -> None:
        if not os.path.exists(path):
            self.send_json({"error": "File nahi mili"}, 404)
            return
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ---- routes ----
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]

        if path in ("/", "/index.html"):
            index = os.path.join(WEB_DIR, "index.html")
            if not os.path.exists(index):
                self.send_json({"error": "web/index.html nahi mila"}, 500)
                return
            with open(index, "rb") as fh:
                self.send_bytes(fh.read(), "text/html; charset=utf-8")
            return

        if path == "/api/info":
            env = R.check_environment(verbose=False)
            self.send_json({
                "themes": list(R.THEMES.keys()),
                "aspects": list(R.ASPECTS.keys()),
                "fonts": R.list_fonts(),
                "ffmpeg": bool(env.get("ffmpeg")),
                "pillow": bool(env.get("pillow")),
                "espeak": bool(env.get("espeak")),
                "raqm": bool(env.get("raqm")),
                "shaping": bool(env.get("shaping")),
                "shaping_detail": str(env.get("shaping_detail", "")),
                "has_devanagari": bool(env.get("has_devanagari")),
                "version": 1,
            })
            return

        if path == "/api/status":
            job_id = self._query_id()
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self.send_json({"error": "Job nahi mila"}, 404)
                    return
                self.send_json({k: v for k, v in job.items() if k != "output"})
            return

        if path == "/api/video":
            job_id = self._query_id()
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job or not job.get("output"):
                self.send_json({"error": "Video taiyaar nahi hai"}, 404)
                return
            self.send_file(job["output"], "video/mp4", f"video-{job_id[:8]}.mp4")
            return

        if path == "/api/projects":
            items = []
            if os.path.isdir(PROJECTS_DIR):
                for name in sorted(os.listdir(PROJECTS_DIR)):
                    full = os.path.join(PROJECTS_DIR, name)
                    if name.endswith(".json") and os.path.isfile(full):
                        items.append(name[:-5])
            self.send_json({"projects": items})
            return

        if path == "/api/project":
            name = safe_name(self._query_value("name") or "", "project")
            full = os.path.join(PROJECTS_DIR, name + ".json")
            if not os.path.exists(full):
                self.send_json({"error": "Project nahi mila"}, 404)
                return
            with open(full, encoding="utf-8") as fh:
                self.send_json({"name": name, "project": json.load(fh)})
            return

        if path == "/health":
            self.send_json({"ok": True})
            return

        self.send_json({"error": "Route nahi mila"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            payload = self.read_json()
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 413)
            return
        except Exception as exc:
            self.send_json({"error": f"JSON galat hai: {exc}"}, 400)
            return

        project = payload.get("project") if isinstance(payload, dict) else None
        if not isinstance(project, dict):
            self.send_json({"error": "project object chahiye"}, 400)
            return

        if path == "/api/preview":
            try:
                workdir = os.path.join(PROJECTS_DIR, "preview-" + uuid.uuid4().hex[:8])
                png = preview_still(project, workdir)
            except R.RenderError as exc:
                self.send_json({"error": str(exc)}, 400)
                return
            except Exception as exc:
                self.send_json({"error": f"Preview nahi ban paya: {exc}"}, 500)
                return
            self.send_bytes(png, "image/png")
            return

        if path == "/api/render":
            job_id = uuid.uuid4().hex[:12]
            workdir = os.path.join(PROJECTS_DIR, job_id)
            os.makedirs(workdir, exist_ok=True)
            out_path = os.path.join(workdir, "video.mp4")
            with JOBS_LOCK:
                JOBS[job_id] = {"id": job_id, "state": "queued", "progress": 0.0,
                                "message": "Queue me...", "output": None, "result": None,
                                "created": time.time()}
            threading.Thread(target=run_job, args=(job_id, project, out_path), daemon=True).start()
            self.send_json({"job_id": job_id})
            return

        if path == "/api/project":
            name = safe_name(payload.get("name") or f"project-{time.strftime('%Y%m%d-%H%M%S')}", "project")
            os.makedirs(PROJECTS_DIR, exist_ok=True)
            full = os.path.join(PROJECTS_DIR, name + ".json")
            with open(full, "w", encoding="utf-8") as fh:
                json.dump(project, fh, indent=2, ensure_ascii=False)
            self.send_json({"saved": name})
            return

        self.send_json({"error": "Route nahi mila"}, 404)

    def _query_id(self) -> str:
        return self._query_value("id") or ""

    def _query_value(self, key: str) -> Optional[str]:
        if "?" not in self.path:
            return None
        from urllib.parse import parse_qs

        values = parse_qs(self.path.split("?", 1)[1]).get(key)
        return values[0] if values else None


def local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Text se video — local web studio.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    os.makedirs(PROJECTS_DIR, exist_ok=True)
    env = R.check_environment(verbose=True)
    if not env.get("ok"):
        print("\n  Upar likhi cheezein install kariye, phir dobara chalaiye.")

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print()
    print("=" * 66)
    print("  TEXT -> VIDEO STUDIO chal raha hai")
    print("=" * 66)
    print(f"  Browser me kholiye :  http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        print(f"  Phone/doosre device :  http://{local_ip()}:{args.port}")
    print("  Band karne ke liye  :  Ctrl+C")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Band kar diya.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
