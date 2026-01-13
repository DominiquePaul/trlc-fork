#!/usr/bin/env python3
"""
Debug helper for lerobot OpenCVCamera failures like:
  RuntimeError: OpenCVCamera(N) read failed (status=False)

It reproduces lerobot's connect flow:
  - cv2.VideoCapture(index_or_path, backend)
  - set FPS / width / height (and optional FOURCC)
  - warmup loop that calls cap.read() repeatedly

It also prints V4L2 device info and checks whether /dev/videoN is busy.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _run(cmd: list[str], timeout_s: float = 3.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout_s}s"


def _print_cmd(title: str, cmd: list[str], timeout_s: float = 3.0) -> None:
    rc, out, err = _run(cmd, timeout_s=timeout_s)
    print(f"\n== {title} ==")
    print("$ " + " ".join(cmd))
    print(f"exit={rc}")
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def _infer_video_path(index_or_path: str) -> Optional[str]:
    # lerobot uses "index_or_path" which can be an int-like string OR "/dev/videoX".
    if index_or_path.startswith("/dev/video"):
        return index_or_path
    try:
        idx = int(index_or_path)
    except ValueError:
        return None
    return f"/dev/video{idx}"


def _busy_check(video_path: str) -> None:
    if Path(video_path).exists():
        if _which("fuser"):
            _print_cmd("busy check (fuser)", ["fuser", "-v", video_path], timeout_s=2.0)
        if _which("lsof"):
            _print_cmd("busy check (lsof)", ["lsof", video_path], timeout_s=2.0)
    else:
        print(f"\n== device node ==\n{video_path} does not exist")


@dataclass
class OpenCVResult:
    ok: bool
    reason: str
    frames_ok: int = 0
    frames_fail: int = 0
    first_shape: Optional[tuple[int, int, int]] = None
    backend_name: Optional[str] = None
    actual_width: Optional[int] = None
    actual_height: Optional[int] = None
    actual_fps: Optional[float] = None
    actual_fourcc: Optional[str] = None
    opened_video_nodes: list[str] = None  # type: ignore[assignment]


def _fourcc_to_str(code: float) -> str:
    # OpenCV returns FOURCC as float; convert to 4-char code (best-effort).
    try:
        c = int(code)
        return "".join([chr((c >> (8 * i)) & 0xFF) for i in range(4)])
    except Exception:
        return "????"


def try_opencv_open(
    *,
    index_or_path: str,
    backend: str,
    width: Optional[int],
    height: Optional[int],
    fps: Optional[float],
    fourcc: Optional[str],
    warmup_s: float,
    warmup_sleep_s: float,
    read_frames: int,
    save_dir: Optional[Path],
) -> OpenCVResult:
    try:
        import cv2  # type: ignore
    except ModuleNotFoundError:
        return OpenCVResult(False, "missing dependency: cv2 (opencv-python)")

    cv2.setNumThreads(1)

    if backend == "v4l2":
        backend_id = getattr(cv2, "CAP_V4L2", 0)
    else:
        backend_id = 0  # CAP_ANY

    # Important: OpenCV treats numeric strings as *filenames* when a backend is forced.
    # lerobot usually passes an int index. Convert digit strings to int so behavior matches.
    cap_src: object = index_or_path
    if isinstance(index_or_path, str) and index_or_path.isdigit():
        cap_src = int(index_or_path)

    cap = cv2.VideoCapture(cap_src, backend_id)
    if not cap.isOpened():
        cap.release()
        return OpenCVResult(False, "cap.isOpened() == False")

    res = OpenCVResult(True, "opened", opened_video_nodes=[])

    # Best-effort: infer which /dev/video* node OpenCV actually opened by inspecting /proc/self/fd.
    try:
        for fd in sorted(os.listdir("/proc/self/fd")):
            try:
                target = os.readlink(f"/proc/self/fd/{fd}")
            except OSError:
                continue
            if target.startswith("/dev/video"):
                res.opened_video_nodes.append(target)
    except Exception:
        pass
    try:
        res.backend_name = cap.getBackendName()
    except Exception:
        res.backend_name = None

    # Apply settings in the same general order as lerobot:
    if fourcc:
        try:
            fc = cv2.VideoWriter_fourcc(*fourcc)
            cap.set(cv2.CAP_PROP_FOURCC, fc)
        except Exception:
            pass
    if width is not None:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))
    if height is not None:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))
    if fps is not None:
        cap.set(cv2.CAP_PROP_FPS, float(fps))

    # Read back actual properties
    try:
        res.actual_width = int(round(cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
        res.actual_height = int(round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        res.actual_fps = float(cap.get(cv2.CAP_PROP_FPS))
        res.actual_fourcc = _fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC))
    except Exception:
        pass

    # lerobot warmup: keep reading until warmup_s elapses; it raises on first failure.
    t0 = time.time()
    while time.time() - t0 < warmup_s:
        ret, frame = cap.read()
        if not ret or frame is None:
            cap.release()
            res.ok = False
            res.reason = f"warmup read failed (ret={ret})"
            res.frames_fail += 1
            return res
        res.frames_ok += 1
        time.sleep(warmup_sleep_s)

    # Additional reads
    first_saved = False
    for _ in range(read_frames):
        ret, frame = cap.read()
        if not ret or frame is None:
            res.frames_fail += 1
            continue
        res.frames_ok += 1
        if res.first_shape is None:
            res.first_shape = frame.shape
        if save_dir and not first_saved:
            save_dir.mkdir(parents=True, exist_ok=True)
            out = save_dir / f"camera_{index_or_path}_first.jpg"
            try:
                cv2.imwrite(str(out), frame)
                print(f"saved {out}")
            except Exception as e:
                print(f"failed to save frame to {out}: {e}", file=sys.stderr)
            first_saved = True

    cap.release()
    res.ok = True
    res.reason = "ok"
    return res


def parse_indices(s: str) -> list[str]:
    # supports: "2" or "0,2,4" or "/dev/video0,/dev/video2"
    parts = []
    for p in s.split(","):
        p = p.strip()
        if p:
            parts.append(p)
    return parts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--indices", default="2", help='Comma-separated camera indices/paths, e.g. "0,2,4"')
    ap.add_argument("--backend", choices=["any", "v4l2"], default="v4l2")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--fourcc", default=None, help='Optional FOURCC like "MJPG" or "YUYV"')
    ap.add_argument("--warmup-s", type=float, default=1.0)
    ap.add_argument("--warmup-sleep-s", type=float, default=0.1)
    ap.add_argument("--read-frames", type=int, default=30)
    ap.add_argument("--save-dir", default=None, help="If set, saves the first successful frame per camera here")
    ap.add_argument(
        "--keep-open",
        action="store_true",
        help="Open cameras sequentially and keep previous ones open (bandwidth stress).",
    )
    ap.add_argument(
        "--v4l2-info",
        action="store_true",
        help="Print v4l2-ctl details (requires v4l2-ctl).",
    )
    args = ap.parse_args()

    indices = parse_indices(args.indices)
    save_dir = Path(args.save_dir) if args.save_dir else None

    print("Debugging lerobot OpenCVCamera connect/read")
    print(f"indices={indices}")
    print(f"backend={args.backend} width={args.width} height={args.height} fps={args.fps} fourcc={args.fourcc}")
    print(f"warmup_s={args.warmup_s} warmup_sleep_s={args.warmup_sleep_s} read_frames={args.read_frames}")
    print(f"keep_open={args.keep_open} v4l2_info={args.v4l2_info} save_dir={save_dir}")

    if args.v4l2_info and not _which("v4l2-ctl"):
        print("note: v4l2-ctl not found; install with: sudo apt-get install v4l-utils", file=sys.stderr)

    # Optional bandwidth stress: keep earlier VideoCapture objects alive.
    kept_caps = []
    try:
        import cv2  # type: ignore
    except Exception:
        cv2 = None  # type: ignore

    for idx in indices:
        print("\n" + "=" * 72)
        print(f"CAMERA {idx}")

        video_path = _infer_video_path(idx)
        if video_path:
            _busy_check(video_path)
            if args.v4l2_info and _which("v4l2-ctl"):
                _print_cmd("v4l2-ctl --all", ["v4l2-ctl", "--device", video_path, "--all"], timeout_s=3.0)
                _print_cmd(
                    "v4l2-ctl --list-formats-ext",
                    ["v4l2-ctl", "--device", video_path, "--list-formats-ext"],
                    timeout_s=3.0,
                )

        # keep_open mode: open and keep cap alive, but still do a lerobot-like warmup/read test first.
        res = try_opencv_open(
            index_or_path=idx,
            backend=args.backend,
            width=args.width,
            height=args.height,
            fps=args.fps,
            fourcc=args.fourcc,
            warmup_s=args.warmup_s,
            warmup_sleep_s=args.warmup_sleep_s,
            read_frames=args.read_frames,
            save_dir=save_dir,
        )

        print("\n-- OpenCV result --")
        print(f"ok={res.ok} reason={res.reason}")
        print(f"backend={res.backend_name}")
        if res.opened_video_nodes:
            print(f"opened_video_nodes={res.opened_video_nodes}")
        print(f"actual: {res.actual_width}x{res.actual_height} @ {res.actual_fps} fourcc={res.actual_fourcc}")
        print(f"frames_ok={res.frames_ok} frames_fail={res.frames_fail} first_shape={res.first_shape}")

        if args.keep_open and cv2 is not None:
            backend_id = getattr(cv2, "CAP_V4L2", 0) if args.backend == "v4l2" else 0
            cap_src: object = idx
            if isinstance(idx, str) and idx.isdigit():
                cap_src = int(idx)
            cap = cv2.VideoCapture(cap_src, backend_id)
            if cap.isOpened():
                kept_caps.append(cap)
                print("kept this camera open for subsequent cameras.")
            else:
                cap.release()
                print("could not keep camera open (cap.isOpened()==False).")

        if not res.ok:
            print("\nHINTS:")
            print("- If fuser/lsof shows a process holding the device, stop it and retry.")
            print("- Try forcing a format: --fourcc MJPG or --fourcc YUYV (check v4l2-ctl formats).")
            print("- Try backend 'any' instead of 'v4l2': --backend any")
            print("- If it only fails with --keep-open, it’s likely USB bandwidth/power.")
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


