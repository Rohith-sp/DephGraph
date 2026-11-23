#!/usr/bin/env python3
"""Render the DFD .eraser sources to PNG and capture editable Eraser links.

Usage: python3 dfd/render.py
Uses only the standard library. Writes dfd/images/*.png and dfd/rendered.json.
The Eraser render API is free and works without a key (adds a small watermark);
set ERASER_API_TOKEN to render without the watermark.
"""
import json
import os
import pathlib
import urllib.request

from PIL import Image, ImageChops

HERE = pathlib.Path(__file__).parent
API = "https://app.eraser.io/api/render/elements"
SCALE = 3
PAD = 32  # px of breathing room kept around the trimmed content


def trim(path: pathlib.Path) -> None:
    """Crop the export canvas down to the drawn content (Eraser pads the frame)."""
    im = Image.open(path).convert("RGB")
    bg = Image.new("RGB", im.size, im.getpixel((0, 0)))
    box = ImageChops.difference(im, bg).getbbox()
    if box:
        im.crop(
            (
                max(box[0] - PAD, 0),
                max(box[1] - PAD, 0),
                min(box[2] + PAD, im.width),
                min(box[3] + PAD, im.height),
            )
        ).save(path)

DIAGRAMS = [
    ("level-0-context", "dfd-level-0-context"),
    ("level-1-system", "dfd-level-1-system"),
    ("level-2-ingestion", "dfd-level-2-ingestion"),
]


def render(name: str, out: str) -> dict:
    code = (HERE / f"{name}.eraser").read_text()
    headers = {"Content-Type": "application/json", "X-Skill-Source": "opencode"}
    if token := os.getenv("ERASER_API_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(
        {
            "elements": [
                {"type": "diagram", "id": "diagram-1", "code": code,
                 "diagramType": "flowchart-diagram"}
            ],
            "scale": SCALE,
            "theme": "light",
            "background": True,
        }
    ).encode()
    req = urllib.request.Request(API, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        payload = json.load(r)
    if "imageUrl" not in payload:
        raise RuntimeError(payload.get("imageError", payload))
    img = HERE / "images" / f"{out}.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(payload["imageUrl"], img)  # noqa: S310
    trim(img)
    payload["file"] = str(img.relative_to(HERE.parent))
    print(f"{name}: {img}")
    return payload


if __name__ == "__main__":
    results = {}
    for name, out in DIAGRAMS:
        results[name] = render(name, out)
    (HERE / "rendered.json").write_text(json.dumps(results, indent=2))
