#!/usr/bin/env python3
"""
Self-test for the evidence kit — no third-party packages, no internet.

It builds a PDF from generated sample data, then re-parses the file byte by byte
to check that the PDF it produced is structurally valid:

  * the cross-reference table points at the right object offsets
  * every object parses and ends where it says it does
  * every stream's /Length matches its actual byte count
  * every page has a /Contents stream and an inherited /Parent
  * the images embedded carry the dimensions of the source images
  * the hash manifest actually matches the files it lists

Run:  python3 selftest.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from evidence_kit import main as kit_main  # noqa: E402
from pdfwriter import PDF, load_image  # noqa: E402

PASS, FAIL = [], []


def check(condition: bool, label: str, detail: str = "") -> None:
    (PASS if condition else FAIL).append(label)
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f"  — {detail}" if detail and not condition else ""))


def parse_pdf(path: str) -> dict:
    """Very small PDF inspector: verify xref offsets and stream lengths."""
    with open(path, "rb") as fh:
        blob = fh.read()
    if not blob.startswith(b"%PDF-"):
        raise ValueError("missing %PDF- header")
    if not blob.rstrip().endswith(b"%%EOF"):
        raise ValueError("missing %%EOF trailer")

    m = re.search(rb"startxref\s+(\d+)", blob)
    if not m:
        raise ValueError("no startxref")
    xref_pos = int(m.group(1))
    if blob[xref_pos : xref_pos + 4] != b"xref":
        raise ValueError("startxref does not point at an xref table")

    xref_block = blob[xref_pos : xref_pos + 4000]
    header = re.search(rb"xref\s+0\s+(\d+)", xref_block)
    if not header:
        raise ValueError("malformed xref header")
    count = int(header.group(1))

    entries = re.findall(rb"(\d{10}) (\d{5}) ([nf])", xref_block)
    if len(entries) < count:
        raise ValueError(f"xref has {len(entries)} entries, expected {count}")

    offsets = []
    for i, (off, gen, kind) in enumerate(entries[:count]):
        if i == 0:
            continue  # free head
        if kind == b"f":
            continue
        offsets.append((i, int(off)))

    objects = {}
    for num, off in offsets:
        if blob[off : off + len(f"{num} 0 obj")] != f"{num} 0 obj".encode():
            raise ValueError(f"object {num}: offset {off} does not point at 'obj'")
        end = blob.find(b"endobj", off)
        if end == -1:
            raise ValueError(f"object {num}: no endobj")
        objects[num] = blob[off:end]

    # every stream's declared /Length must match its real length
    stream_count = 0
    for num, body in objects.items():
        declared = re.search(rb"/Length (\d+)", body)
        if b"stream\n" not in body:
            continue
        stream_count += 1
        start = body.index(b"stream\n") + len(b"stream\n")
        stop = body.rindex(b"\nendstream")
        actual = stop - start
        if not declared:
            raise ValueError(f"object {num}: stream without /Length")
        if int(declared.group(1)) != actual:
            raise ValueError(
                f"object {num}: /Length says {int(declared.group(1))} but stream is {actual} bytes"
            )

    pages = [num for num, body in objects.items() if b"/Type /Page " in body or b"/Type /Page\n" in body or b"/Type /Page/" in body or b"/Type /Page " in body]
    pages = [num for num, body in objects.items() if re.search(rb"/Type\s*/Page\b(?!s)", body)]
    missing_parent = [n for n in pages if b"/Parent" not in objects[n]]
    missing_contents = [n for n in pages if b"/Contents" not in objects[n]]

    return {
        "objects": objects,
        "page_count": len(pages),
        "objects_without_parent": missing_parent,
        "objects_without_contents": missing_contents,
        "stream_count": stream_count,
        "size": len(blob),
    }


def main() -> int:
    print("PDF structure checks")
    tmp = tempfile.mkdtemp(prefix="evidence-kit-selftest-")
    try:
        # 1. Tiny synthetic PDF
        pdf = PDF()
        pdf.text_flow("Self test", ["line " * 400, "second paragraph " * 300])
        pdf.table_flow("Table", ["A", "B"], [[f"row {i}", "x" * (i + 1)] for i in range(120)])
        small = os.path.join(tmp, "small.pdf")
        pdf.save(small, title="selftest")
        info = parse_pdf(small)
        check(True, "PDF parses (xref, objects, streams)")
        check(info["page_count"] >= 2, "text_flow paginates across pages", f"{info['page_count']} pages")
        check(not info["objects_without_parent"], "every page has a /Parent")
        check(not info["objects_without_contents"], "every page has /Contents")
        check(info["stream_count"] > 0, "streams present", f"{info['stream_count']} streams")

        # 1b. Edge cases that must terminate and never lose the page stream
        edge = PDF()
        edge.text_flow("Giant paragraph", ["word " * 5000])  # far longer than one page
        edge.table_flow("Giant cell", ["a", "b"], [["1", "word " * 3000]])
        edge.table_flow("Many rows", ["a", "b"], [[f"r{i}", "x" * 120] for i in range(60)])
        edge_path = os.path.join(tmp, "edge.pdf")
        edge.save(edge_path, title="edge")
        edge_info = parse_pdf(edge_path)
        check(True, "oversized paragraph and table rows terminate")
        check(
            not edge_info["objects_without_parent"] and not edge_info["objects_without_contents"],
            "edge-case pages are all well formed",
        )

        # 2. Sample data build
        samples = os.path.join(HERE, "samples")
        if not os.path.exists(os.path.join(samples, "sample_01_instagram.png")):
            subprocess.run([sys.executable, os.path.join(samples, "make_samples.py")], check=True)
        out = os.path.join(tmp, "out")
        rc = kit_main([
            "build",
            "--photos", samples,
            "--incidents", os.path.join(samples, "incidents.csv"),
            "--case", os.path.join(samples, "case.json"),
            "--out", out,
        ])
        check(rc == 0, "build command exits 0")

        bundle = os.path.join(out, "evidence_bundle.pdf")
        check(os.path.exists(bundle), "evidence_bundle.pdf created")
        info = parse_pdf(bundle)
        check(info["page_count"] >= 6, "bundle has cover + guide + index + image pages", f"{info['page_count']} pages")

        # 3. embedded images keep source dimensions
        src_dims = []
        for name in sorted(os.listdir(samples)):
            if name.lower().endswith((".png", ".jpg", ".jpeg")):
                img = load_image(os.path.join(samples, name))
                src_dims.append((img.width, img.height))
        embedded = []
        for num, body in info["objects"].items():
            if b"/Subtype /Image" in body:
                w = re.search(rb"/Width (\d+)", body)
                h = re.search(rb"/Height (\d+)", body)
                if w and h:
                    embedded.append((int(w.group(1)), int(h.group(1))))
        for dims in src_dims:
            check(dims in embedded, f"image {dims[0]}x{dims[1]} embedded with correct dimensions")

        # 4. outputs exist
        for name in ("complaint_draft.md", "platform_report.txt", "manifest_sha256.txt", "incidents.csv"):
            path = os.path.join(out, name)
            check(os.path.exists(path) and os.path.getsize(path) > 0, f"{name} written")

        # 5. verify round-trip: manifest check must pass, then detect tampering
        rc = kit_main(["verify", "--manifest", os.path.join(out, "manifest_sha256.txt"), "--photos", samples])
        check(rc == 0, "verify accepts untouched images")

        tamper_dir = os.path.join(tmp, "tampered")
        shutil.copytree(samples, tamper_dir)
        target = os.path.join(tamper_dir, "sample_01_instagram.png")
        with open(target, "r+b") as fh:
            fh.seek(200)
            fh.write(b"\x00")
        rc = kit_main(["verify", "--manifest", os.path.join(out, "manifest_sha256.txt"), "--photos", tamper_dir])
        check(rc == 1, "verify detects a tampered image")

        # 6. missing screenshot produces a warning, not a crash
        lonely = os.path.join(tmp, "lonely")
        os.makedirs(lonely)
        shutil.copy(os.path.join(samples, "sample_03_instagram.png"), lonely)
        rc = kit_main(["build", "--photos", lonely, "--incidents", "/nonexistent.csv", "--out", os.path.join(tmp, "out2")])
        check(rc == 0, "build survives a missing incident log")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for label in FAIL:
            print(f"  failed: {label}")
        return 1
    print("All self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
