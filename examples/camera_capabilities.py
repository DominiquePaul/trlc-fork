#!/usr/bin/env python3
"""
Print supported resolution + frame-rate combinations for a camera device.

Best effort strategy:
- On Linux, prefer `v4l2-ctl --list-formats-ext -d /dev/videoX` (most accurate).
- If `v4l2-ctl` isn't available, fall back to probing common resolution/fps combos via OpenCV.

Usage:
  python examples/camera_capabilities.py --index 0
  python examples/camera_capabilities.py --device /dev/video2
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Mode:
    width: int
    height: int
    fps: float | None = None
    pixel_format: str | None = None


def _run_v4l2_ctl(device: str) -> str | None:
    if shutil.which("v4l2-ctl") is None:
        return None
    try:
        p = subprocess.run(
            ["v4l2-ctl", "--device", device, "--list-formats-ext"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return out.strip() if out.strip() else None
    except Exception:
        return None


def _parse_v4l2_formats_ext(output: str) -> list[Mode]:
    """
    Parses output from:
      v4l2-ctl --list-formats-ext -d /dev/videoX
    Returns a list of Mode(width,height,fps,pixel_format).
    """
    modes: list[Mode] = []

    # Example lines:
    #   [0]: 'MJPG' (Motion-JPEG, compressed)
    #       Size: Discrete 640x480
    #           Interval: Discrete 0.033s (30.000 fps)
    pf_re = re.compile(r"^\s*\[\d+\]:\s*'(?P<pf>[A-Z0-9]+)'")
    size_re = re.compile(r"^\s*Size:\s*Discrete\s*(?P<w>\d+)x(?P<h>\d+)")
    fps_re = re.compile(r"^\s*Interval:\s*Discrete\s*[0-9.]+s\s*\((?P<fps>[0-9.]+)\s*fps\)")

    current_pf: str | None = None
    current_size: tuple[int, int] | None = None

    for line in output.splitlines():
        m = pf_re.match(line)
        if m:
            current_pf = m.group("pf")
            current_size = None
            continue

        m = size_re.match(line)
        if m:
            current_size = (int(m.group("w")), int(m.group("h")))
            continue

        m = fps_re.match(line)
        if m and current_size is not None:
            fps = float(m.group("fps"))
            modes.append(
                Mode(
                    width=current_size[0],
                    height=current_size[1],
                    fps=fps,
                    pixel_format=current_pf,
                )
            )

    # Deduplicate while preserving order
    seen: set[tuple[int, int, float | None, str | None]] = set()
    deduped: list[Mode] = []
    for mode in modes:
        key = (mode.width, mode.height, mode.fps, mode.pixel_format)
        if key not in seen:
            seen.add(key)
            deduped.append(mode)
    return deduped


def _group_modes(modes: Iterable[Mode]) -> dict[tuple[int, int], list[Mode]]:
    grouped: dict[tuple[int, int], list[Mode]] = {}
    for m in modes:
        grouped.setdefault((m.width, m.height), []).append(m)
    for k in grouped:
        grouped[k].sort(key=lambda x: (x.pixel_format or "", x.fps or 0.0))
    return dict(sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])))


def _print_modes(modes: list[Mode], device: str) -> None:
    if not modes:
        print(f"No modes detected for {device}.")
        return

    grouped = _group_modes(modes)
    print(f"Camera capabilities for {device}")
    print("=" * 60)
    for (w, h), items in grouped.items():
        # Aggregate fps values and pixel formats
        by_pf: dict[str, list[float]] = {}
        for it in items:
            pf = it.pixel_format or "UNKNOWN"
            if it.fps is not None:
                by_pf.setdefault(pf, []).append(it.fps)
        print(f"\n- {w}x{h}")
        for pf, fps_list in sorted(by_pf.items(), key=lambda kv: kv[0]):
            fps_list = sorted(set(round(x, 3) for x in fps_list))
            fps_str = ", ".join(f"{x:g}" for x in fps_list) if fps_list else "unknown"
            print(f"  - {pf}: {fps_str} fps")


def _opencv_probe(device_index: int) -> list[Mode]:
    """
    Fallback: attempt to set common resolutions and fps values using OpenCV.
    Not authoritative, but helpful when v4l2-ctl isn't available.
    """
    try:
        import cv2  # type: ignore
    except Exception:
        return []

    common_sizes = [
        (320, 240),
        (424, 240),
        (640, 360),
        (640, 480),
        (848, 480),
        (960, 540),
        (1280, 720),
        (1920, 1080),
    ]
    common_fps = [5, 10, 15, 20, 24, 25, 30, 60]

    modes: list[Mode] = []
    cap = cv2.VideoCapture(device_index)
    if not cap.isOpened():
        return []

    try:
        for (w, h) in common_sizes:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(w))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(h))
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if actual_w <= 0 or actual_h <= 0:
                continue

            for fps in common_fps:
                cap.set(cv2.CAP_PROP_FPS, float(fps))
                actual_fps = cap.get(cv2.CAP_PROP_FPS)
                # Read a frame to force negotiation; ignore failures
                cap.read()
                if actual_fps and actual_fps > 0:
                    modes.append(Mode(width=actual_w, height=actual_h, fps=float(actual_fps), pixel_format="(opencv-probed)"))
    finally:
        cap.release()

    # Deduplicate
    seen: set[tuple[int, int, float]] = set()
    deduped: list[Mode] = []
    for m in modes:
        key = (m.width, m.height, round(m.fps or 0.0, 3))
        if key not in seen:
            seen.add(key)
            deduped.append(m)
    return deduped


def main() -> None:
    parser = argparse.ArgumentParser(description="Print camera size/fps capabilities for /dev/videoX")
    parser.add_argument("--index", type=int, default=0, help="Camera index (maps to /dev/video{index})")
    parser.add_argument("--device", type=str, default=None, help="Explicit V4L2 device path, e.g. /dev/video0")
    args = parser.parse_args()

    device = args.device or f"/dev/video{args.index}"

    if not sys.platform.startswith("linux"):
        print("This script is intended for Linux (V4L2).")
        print("Falling back to OpenCV probing only.")
        modes = _opencv_probe(args.index)
        _print_modes(modes, device=f"index {args.index}")
        return

    out = _run_v4l2_ctl(device)
    if out is None:
        print("`v4l2-ctl` not available or produced no output.")
        print("Install it with: sudo apt-get install v4l-utils")
        print("Falling back to OpenCV probing (best effort).")
        modes = _opencv_probe(args.index)
        _print_modes(modes, device=device)
        return

    modes = _parse_v4l2_formats_ext(out)
    if not modes:
        # If parsing failed, print raw output so it's still useful.
        print(f"Could not parse v4l2-ctl output for {device}. Raw output:")
        print(out)
        return

    _print_modes(modes, device=device)


if __name__ == "__main__":
    main()


