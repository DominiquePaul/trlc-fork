import logging
import os
import subprocess
import time
from pathlib import Path

# Ensure FFmpeg doesn't create log files
os.environ.pop("FFREPORT", None)

# Track encoding times for summary output
_encoding_times: list[tuple[str, float]] = []


def fast_encode_video_frames(
    imgs_dir,
    video_path,
    fps,
    vcodec="libsvtav1",
    pix_fmt="yuv420p",
    g=2,
    crf=30,
    fast_decode=0,
    log_level=None,
    overwrite=False,
    timeout_s: float | None = 60.0,
    **_ignored_kwargs,
):
    imgs_dir = Path(imgs_dir)
    video_path = Path(video_path)

    # LeRobot uses "frame-%06d.png"
    first_frame = imgs_dir / "frame-000000.png"
    if not first_frame.exists():
        # Keep the error actionable (this can happen if encoding starts before images are flushed)
        raise FileNotFoundError(
            f"No frames found at {first_frame}. (imgs_dir={imgs_dir})"
        )

    if video_path.exists() and not overwrite:
        logging.warning(f"Video file already exists: {video_path}. Skipping encoding.")
        return

    video_path.parent.mkdir(parents=True, exist_ok=True)

    # Build FFmpeg command
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-nostats",  # Suppress encoding progress stats
        "-y" if overwrite else "-n",
        "-framerate",
        str(fps),
        "-i",
        str(imgs_dir / "frame-%06d.png"),
    ]

    # Map codecs to NVENC equivalents if available
    # Note: NVENC uses -cq or -qp instead of -crf usually, but ffmpeg might map it.
    # However, to be safe, we should check if the user wanted a specific codec.
    # If they passed default "libsvtav1", we can try "av1_nvenc".

    # On Jetson (Tegra), h264_nvenc doesn't work - use libx264 instead
    # Check for Tegra by looking at /proc/device-tree/model or uname
    import platform
    is_jetson = "tegra" in platform.release().lower() or "aarch64" in platform.machine().lower()
    
    if is_jetson:
        # Jetson: use fast software encoding (libx264 with ultrafast preset)
        if vcodec in ("libsvtav1", "h264", "hevc"):
            vcodec = "libx264"
    else:
        # Desktop: try NVENC
        if vcodec == "libsvtav1":
            vcodec = "h264_nvenc"  # av1_nvenc requires RTX 4000+
        elif vcodec == "h264":
            vcodec = "h264_nvenc"
        elif vcodec == "hevc":
            vcodec = "hevc_nvenc"

    cmd.extend(["-c:v", vcodec])
    # Debug: show which encoder is being used (only first time)
    if not hasattr(fast_encode_video_frames, "_logged_encoder"):
        print(f"[video_encoding] Using encoder: {vcodec} (is_jetson={is_jetson})")
        fast_encode_video_frames._logged_encoder = True

    # NVENC specific handling for pixel format
    # NVENC often prefers yuv420p
    cmd.extend(["-pix_fmt", pix_fmt])

    if g is not None:
        cmd.extend(["-g", str(g)])
        # NVENC fix: GOP length must be > B-frames + 1.
        # Since 'g' (GOP size) is often small in these datasets (e.g. 2), we must disable B-frames to be safe.
        if "nvenc" in vcodec and g < 5:
            cmd.extend(["-bf", "0"])

    # NVENC uses -cq (Constant Quality) or -qp instead of -crf
    # For simplicity, we map crf to cq for nvenc codecs
    if crf is not None:
        if "nvenc" in vcodec:
            cmd.extend(["-cq", str(crf)])
            cmd.extend(["-preset", "p4"])  # p4 is medium preset
        elif vcodec == "libx264":
            cmd.extend(["-crf", str(crf)])
            cmd.extend(["-preset", "ultrafast"])  # fast encoding for Jetson
        else:
            cmd.extend(["-crf", str(crf)])

    if fast_decode:
        if vcodec == "libsvtav1":
            cmd.extend(["-svtav1-params", f"fast-decode={fast_decode}"])
        elif "nvenc" not in vcodec:
            cmd.extend(["-tune", "fastdecode"])

    # Reduce log verbosity - only show fatal errors
    cmd.extend(["-loglevel", "fatal"])

    cmd.append(str(video_path))

    try:
        t0 = time.perf_counter()
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
        elapsed = time.perf_counter() - t0
        
        # Extract camera name from path (e.g., "observation.images.context" -> "context")
        cam_name = video_path.stem.split(".")[-1].split("_")[0]
        _encoding_times.append((cam_name, elapsed))
        
        # Print summary line when we have all 3 cameras encoded
        if len(_encoding_times) >= 3:
            summary = " | ".join(f"{name}: {t:.2f}s" for name, t in _encoding_times)
            print(f"Encoded: {summary}")
            _encoding_times.clear()
            
    except subprocess.TimeoutExpired as e:
        # `stderr` can be None if the process never produced any output
        stderr = (e.stderr or b"").decode(errors="replace")
        raise TimeoutError(
            "FFmpeg encoding timed out.\n"
            f"Command: {' '.join(cmd)}\n"
            f"timeout_s={timeout_s}\n"
            f"Last stderr:\n{stderr[-2000:]}"
        ) from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode(errors="replace")
        raise OSError(
            f"FFmpeg encoding failed.\nCommand: {' '.join(cmd)}\nError: {stderr}"
        ) from e