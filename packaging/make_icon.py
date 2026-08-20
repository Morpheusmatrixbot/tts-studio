#!/usr/bin/env python3
"""Generate the app icon (PNG, ICNS, ICO) with no image-library dependency.

Drawing the icon in code keeps the repository free of opaque binary art and
lets the build regenerate every size from one definition. PNG and ICO are
written directly; ICNS is delegated to macOS `iconutil` when available.

    python packaging/make_icon.py
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent
BG_TOP = (124, 157, 255)
BG_BOTTOM = (79, 209, 165)
FG = (255, 255, 255)


def _rounded_mask(size: int, radius_frac: float = 0.22) -> np.ndarray:
    """Anti-aliased rounded-square coverage, supersampled 4x."""
    ss = 4
    n = size * ss
    r = radius_frac * n
    y, x = np.mgrid[0:n, 0:n].astype(np.float64)
    inside = np.ones((n, n), dtype=bool)
    for cx, cy, sx, sy in (
        (r, r, -1, -1), (n - r, r, 1, -1), (r, n - r, -1, 1), (n - r, n - r, 1, 1),
    ):
        corner = ((sx * (x - cx)) > 0) & ((sy * (y - cy)) > 0)
        far = ((x - cx) ** 2 + (y - cy) ** 2) > r * r
        inside &= ~(corner & far)
    return inside.reshape(size, ss, size, ss).mean(axis=(1, 3))


def _microphone(size: int) -> np.ndarray:
    """Coverage mask for a simple microphone glyph, supersampled 4x."""
    ss = 4
    n = size * ss
    y, x = np.mgrid[0:n, 0:n].astype(np.float64)
    cx = n / 2.0
    mask = np.zeros((n, n), dtype=bool)

    # Capsule body.
    body_w, body_top, body_bot = n * 0.115, n * 0.20, n * 0.545
    within_x = np.abs(x - cx) <= body_w
    mask |= within_x & (y >= body_top) & (y <= body_bot)
    for cy in (body_top, body_bot):
        mask |= ((x - cx) ** 2 + (y - cy) ** 2) <= body_w**2

    # Cradle arc, drawn as an annulus clipped to its lower half.
    arc_r, arc_t = n * 0.225, n * 0.052
    arc_cy = n * 0.50
    d = np.sqrt((x - cx) ** 2 + (y - arc_cy) ** 2)
    mask |= (np.abs(d - arc_r) <= arc_t / 2) & (y >= arc_cy)

    # Stem and base.
    mask |= (np.abs(x - cx) <= n * 0.028) & (y >= arc_cy + arc_r - arc_t) & (y <= n * 0.80)
    mask |= (np.abs(x - cx) <= n * 0.145) & (np.abs(y - n * 0.815) <= n * 0.028)

    return mask.astype(np.float64).reshape(size, ss, size, ss).mean(axis=(1, 3))


def render(size: int) -> np.ndarray:
    """RGBA array for one icon size."""
    ramp = np.linspace(0.0, 1.0, size)[:, None]
    rgb = np.zeros((size, size, 3), dtype=np.float64)
    for c in range(3):
        rgb[:, :, c] = BG_TOP[c] * (1 - ramp) + BG_BOTTOM[c] * ramp

    glyph = _microphone(size)[:, :, None]
    fg = np.array(FG, dtype=np.float64)[None, None, :]
    rgb = rgb * (1 - glyph) + fg * glyph

    alpha = _rounded_mask(size) * 255.0
    out = np.concatenate([rgb, alpha[:, :, None]], axis=2)
    return np.clip(out, 0, 255).astype(np.uint8)


def write_png(path: Path, rgba: np.ndarray) -> Path:
    """Minimal PNG encoder (filter type 0 on every row)."""
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return path


def write_ico(path: Path, sizes: list[int]) -> Path:
    """ICO container of embedded PNGs (supported since Windows Vista)."""
    images = []
    for size in sizes:
        tmp = OUT / f"_ico_{size}.png"
        write_png(tmp, render(size))
        images.append((size, tmp.read_bytes()))
        tmp.unlink()

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, blob in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        blobs += blob
        offset += len(blob)
    path.write_bytes(header + entries + blobs)
    return path


def write_icns(path: Path) -> Path | None:
    if not shutil.which("iconutil"):
        print("iconutil not available — skipping .icns", flush=True)
        return None
    iconset = OUT / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    # Names iconutil expects, including @2x retina variants.
    for base in (16, 32, 128, 256, 512):
        write_png(iconset / f"icon_{base}x{base}.png", render(base))
        write_png(iconset / f"icon_{base}x{base}@2x.png", render(base * 2))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    shutil.rmtree(iconset)
    return path


def main() -> int:
    write_png(OUT / "icon.png", render(512))
    print(f"wrote {OUT / 'icon.png'}")
    write_ico(OUT / "icon.ico", [16, 32, 48, 64, 128, 256])
    print(f"wrote {OUT / 'icon.ico'}")
    if sys.platform == "darwin":
        result = write_icns(OUT / "icon.icns")
        if result:
            print(f"wrote {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
