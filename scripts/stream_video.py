"""
Stream live video from a connected camera index using OpenCV.

Examples:
  python scripts/stream_video.py 0
  python scripts/stream_video.py 1 --width 1280 --height 720 --fps 30
  python scripts/stream_video.py 0 --no-autofocus
  python scripts/stream_video.py 0 --focus 20

Controls:
  - Press 'q' or ESC to quit
"""

from __future__ import annotations

import argparse
import sys
import time


def _try_set(cap, prop: int, value: float) -> bool:
    try:
        ok = cap.set(prop, value)
        return bool(ok)
    except Exception:
        return False


def _configure_camera(
    cap,
    *,
    width: int | None,
    height: int | None,
    fps: int | None,
    fourcc: str | None,
    autofocus: bool | None,
    focus: int | None,
    verbose: bool = True,
) -> None:
    import cv2  # type: ignore

    # Some USB webcams expose different quality modes depending on pixel format.
    # MJPG is commonly sharper at higher resolutions (device-dependent).
    if fourcc:
        fourcc_up = str(fourcc).upper()
        if len(fourcc_up) == 4:
            _try_set(cap, cv2.CAP_PROP_FOURCC, float(cv2.VideoWriter_fourcc(*fourcc_up)))

    if width is not None:
        _try_set(cap, cv2.CAP_PROP_FRAME_WIDTH, float(int(width)))
    if height is not None:
        _try_set(cap, cv2.CAP_PROP_FRAME_HEIGHT, float(int(height)))
    if fps is not None:
        _try_set(cap, cv2.CAP_PROP_FPS, float(int(fps)))

    # Manual focus: commonly requires disabling autofocus first.
    if focus is not None:
        if hasattr(cv2, "CAP_PROP_AUTOFOCUS"):
            _try_set(cap, cv2.CAP_PROP_AUTOFOCUS, 0.0)
        if hasattr(cv2, "CAP_PROP_FOCUS"):
            _try_set(cap, cv2.CAP_PROP_FOCUS, float(int(focus)))
        return

    if autofocus is False:
        if hasattr(cv2, "CAP_PROP_AUTOFOCUS"):
            _try_set(cap, cv2.CAP_PROP_AUTOFOCUS, 0.0)
        return

    # Default behaviour: try enabling autofocus quietly (unless explicitly requested).
    if hasattr(cv2, "CAP_PROP_AUTOFOCUS"):
        ok = _try_set(cap, cv2.CAP_PROP_AUTOFOCUS, 1.0)
        if verbose and not ok:
            print(
                "Warning: could not enable autofocus (CAP_PROP_AUTOFOCUS unsupported by this backend/device).",
                file=sys.stderr,
            )
    elif verbose:
        print(
            "Warning: OpenCV build lacks CAP_PROP_AUTOFOCUS; continuing without explicit autofocus.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stream live video from a USB camera index.")
    parser.add_argument("index", type=int, nargs="?", default=0, help="OpenCV camera index (default: 0).")
    parser.add_argument("--width", type=int, default=None, help="Requested capture width.")
    parser.add_argument("--height", type=int, default=None, help="Requested capture height.")
    parser.add_argument(
        "--fourcc",
        type=str,
        default=None,
        help="Optional 4CC pixel format hint (e.g. MJPG). Device/backend dependent.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        nargs="?",
        const=30,
        default=None,
        help="Requested capture FPS. If provided without a value, defaults to 30.",
    )
    parser.add_argument(
        "--warmup-frames",
        type=int,
        default=5,
        help="Number of warmup frames before showing video (default: 5).",
    )
    parser.add_argument(
        "--autofocus-seconds",
        type=float,
        default=1.0,
        help="Extra time to stream frames (without display) to let autofocus settle (default: 1.0).",
    )
    af_group = parser.add_mutually_exclusive_group()
    af_group.add_argument(
        "--autofocus",
        dest="autofocus",
        action="store_const",
        const=True,
        help="Force enabling autofocus (warn if unsupported).",
    )
    af_group.add_argument(
        "--no-autofocus",
        dest="autofocus",
        action="store_const",
        const=False,
        help="Disable autofocus.",
    )
    parser.set_defaults(autofocus=None)
    parser.add_argument(
        "--focus",
        type=int,
        default=None,
        help="Manual focus value (disables autofocus). Support varies by device/backend.",
    )
    parser.add_argument(
        "--window",
        type=str,
        default=None,
        help="Window title (default: 'camera {index}').",
    )
    parser.add_argument(
        "--no-overlay",
        action="store_true",
        help="Disable on-frame overlay showing negotiated resolution and FPS.",
    )

    args = parser.parse_args(argv)

    try:
        import cv2  # type: ignore
    except ModuleNotFoundError as e:
        if "cv2" in str(e):
            print("Missing dependency: opencv-python (cv2). Install it, then retry.", file=sys.stderr)
            return 2
        raise

    window_title = args.window or f"camera {args.index} (press q/esc to quit)"

    cap = cv2.VideoCapture(int(args.index))
    if not cap.isOpened():
        print(
            f"Error: Could not open camera index {args.index}. "
            f"Try a different index, or check OS camera permissions.",
            file=sys.stderr,
        )
        return 1

    try:
        _configure_camera(
            cap,
            width=args.width,
            height=args.height,
            fourcc=args.fourcc,
            fps=args.fps,
            autofocus=args.autofocus,
            focus=args.focus,
            verbose=(args.autofocus is True),
        )

        # Warm up auto-exposure/auto-whitebalance.
        for _ in range(max(0, int(args.warmup_frames))):
            cap.read()
            time.sleep(0.02)

        autofocus_effective = args.autofocus is not False
        if autofocus_effective and args.focus is None and float(args.autofocus_seconds) > 0:
            t0 = time.time()
            while (time.time() - t0) < float(args.autofocus_seconds):
                cap.read()
                time.sleep(0.02)

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)

        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("Warning: camera read failed (no frame).", file=sys.stderr)
                time.sleep(0.05)
                continue

            if not args.no_overlay:
                overlay = f"{actual_w}x{actual_h}  {actual_fps:g}fps"
                if args.fourcc:
                    overlay += f"  {str(args.fourcc).upper()}"
                cv2.putText(
                    frame,
                    overlay,
                    (10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2,
                    cv2.LINE_AA,
                )

            cv2.imshow(window_title, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord("q")):  # ESC or q
                break
    except Exception as e:
        # Common in headless envs (no GUI backend) or camera disconnects.
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        try:
            cap.release()
        finally:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

