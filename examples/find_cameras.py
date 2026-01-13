#!/usr/bin/env python3
"""
Utility script to identify which camera indices correspond to which physical cameras.

This script will:
1. Try to open each camera index (0-10 by default)
2. Display a preview window for each working camera
3. Show device information if available (Linux only)

Usage:
    python examples/find_cameras.py
    python examples/find_cameras.py --max-index 5  # Check only indices 0-5
    python examples/find_cameras.py --capture-images  # Save ONLY a combined grid image from all cameras
    python examples/find_cameras.py --capture-images --save-individual-images  # Also save per-camera images
    python examples/find_cameras.py --capture-images --output-dir ./camera_images  # Save to specific directory
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime
import math


def _require_cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except ModuleNotFoundError:
        print("❌ Missing dependency: OpenCV (cv2).")
        print("   Install with: pip install opencv-python")
        print("   Or (headless): pip install opencv-python-headless")
        sys.exit(2)


def _require_numpy():
    try:
        import numpy as np  # type: ignore
        return np
    except ModuleNotFoundError:
        print("❌ Missing dependency: numpy.")
        print("   Install with: pip install numpy")
        sys.exit(2)


def _optional_pillow():
    """Return (Image, ImageDraw, ImageFont) if Pillow is available, else (None, None, None)."""
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        return Image, ImageDraw, ImageFont
    except ModuleNotFoundError:
        return None, None, None


def _find_default_sans_font():
    """Best-effort lookup for a standard sans-serif TTF on Linux."""
    candidates = [
        # Common on Ubuntu/Debian/Jetson images
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        # Noto Sans (often installed)
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        # Liberation Sans (Arial-like)
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        # Sometimes present
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def get_v4l2_device_info(index):
    """Get device information using v4l2 on Linux."""
    try:
        import subprocess
        # Try to get device info
        result = subprocess.run(
            ['v4l2-ctl', '--device', f'/dev/video{index}', '--all'],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            # Extract device name and other useful info
            card_name = None
            driver_name = None
            bus_info = None
            
            for line in result.stdout.split('\n'):
                if 'Card type' in line or 'Card' in line:
                    card_name = line.split(':', 1)[1].strip() if ':' in line else line.strip()
                elif 'Driver name' in line:
                    driver_name = line.split(':', 1)[1].strip() if ':' in line else line.strip()
                elif 'Bus info' in line:
                    bus_info = line.split(':', 1)[1].strip() if ':' in line else line.strip()
            
            # Return the most useful info
            if card_name:
                info = card_name
                if bus_info:
                    info += f" ({bus_info})"
                return info
            elif driver_name:
                return driver_name
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        pass
    return None


def check_gui_available():
    """Check if OpenCV GUI (highgui) is available."""
    cv2 = _require_cv2()
    try:
        # Try to create a test window
        test_window = "___test_window___"
        cv2.namedWindow(test_window, cv2.WINDOW_NORMAL)
        cv2.destroyWindow(test_window)
        return True
    except cv2.error:
        return False


def test_camera(index, show_preview=True, preview_time=3, gui_available=True):
    """Test if a camera index is available and show preview."""
    cv2 = _require_cv2()
    cap = cv2.VideoCapture(index)
    
    if not cap.isOpened():
        return False, None
    
    # Try to read a frame to confirm it's working
    ret, frame = cap.read()
    if not ret or frame is None:
        cap.release()
        return False, None
    
    # Get camera properties
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    
    device_info = None
    if sys.platform.startswith('linux'):
        device_info = get_v4l2_device_info(index)
    
    # Only show preview if requested and GUI is available
    if show_preview and gui_available:
        print(f"\n📹 Camera index {index} is available!")
        print(f"   Resolution: {width}x{height}")
        print(f"   FPS: {fps}")
        if device_info:
            print(f"   Device: {device_info}")
        print(f"   Showing preview for {preview_time} seconds...")
        print(f"   Press 'q' to skip, or wait for auto-advance")
        
        try:
            window_name = f"Camera {index} - Press 'q' to skip"
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window_name, 640, 480)
            
            start_time = cv2.getTickCount()
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                
                # Add text overlay
                cv2.putText(frame, f"Camera Index: {index}", (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.putText(frame, f"Resolution: {width}x{height}", (10, 70),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                
                cv2.imshow(window_name, frame)
                
                # Check if time elapsed or 'q' pressed
                elapsed = (cv2.getTickCount() - start_time) / cv2.getTickFrequency()
                if elapsed >= preview_time:
                    break
                
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
            
            cv2.destroyWindow(window_name)
        except cv2.error as e:
            # GUI not available, fall back to no preview
            print(f"   (GUI not available, skipping preview)")
    
    cap.release()
    
    info = {
        'width': width,
        'height': height,
        'fps': fps,
        'device_info': device_info
    }
    
    return True, info


def capture_camera_image(index, output_dir=None, save_image=True):
    """Capture a single frame from a camera and save it as an image file.

    Returns:
        (filename, frame) on success, or (None, None) on failure.
        If save_image is False, filename will be None but frame will still be returned.
    """
    cv2 = _require_cv2()
    cap = cv2.VideoCapture(index)
    
    if not cap.isOpened():
        return None, None
    
    # Read a few frames to let the camera stabilize
    for _ in range(5):
        cap.read()
    
    # Capture the actual frame
    ret, frame = cap.read()
    cap.release()
    
    if not ret or frame is None:
        return None, None

    if not save_image:
        return None, frame

    # Create output directory if specified
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
    else:
        output_path = Path.cwd()

    # Generate filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = output_path / f"camera_{index}_{timestamp}.jpg"

    # Save the image
    cv2.imwrite(str(filename), frame)
    return filename, frame


def _compute_grid_shape(num_images, columns=None):
    """Return (rows, cols) for a near-square grid."""
    if num_images <= 0:
        return 0, 0
    if columns is not None and columns > 0:
        cols = int(columns)
    else:
        cols = int(math.ceil(math.sqrt(num_images)))
    rows = int(math.ceil(num_images / cols))
    return rows, cols


def _draw_title_on_tile(tile_bgr, title, font_path=None, font_size=28):
    """Draw a title bar onto a BGR tile. Prefers Pillow for real fonts; falls back to OpenCV."""
    cv2 = _require_cv2()

    # Always draw a dark bar for legibility
    h, w = tile_bgr.shape[:2]
    bar_h = min(52, max(36, int(h * 0.1)))

    Image, ImageDraw, ImageFont = _optional_pillow()
    if Image is not None:
        np = _require_numpy()
        # Convert BGR -> RGB for Pillow
        tile_rgb = cv2.cvtColor(tile_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(tile_rgb)
        draw = ImageDraw.Draw(img)

        # Background bar
        draw.rectangle([0, 0, w, bar_h], fill=(0, 0, 0))

        # Choose font
        chosen_font_path = font_path or _find_default_sans_font()
        font = None
        if chosen_font_path:
            try:
                font = ImageFont.truetype(chosen_font_path, font_size)
            except Exception:
                font = None
        if font is None:
            font = ImageFont.load_default()

        # Draw text in a standard "OpenCV-like" green
        draw.text((10, max(4, (bar_h - font_size) // 2)), title, fill=(0, 255, 0), font=font)

        # Back to BGR
        out_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        return out_bgr

    # Fallback: OpenCV Hershey fonts (not Arial/Helvetica)
    cv2.rectangle(tile_bgr, (0, 0), (w, bar_h), (0, 0, 0), thickness=-1)
    cv2.putText(tile_bgr, title, (10, int(bar_h * 0.7)), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
    return tile_bgr


def compose_camera_grid(
    captured_frames,
    output_dir=None,
    tile_width=640,
    tile_height=480,
    columns=None,
    font_path=None,
    font_size=28,
):
    """Compose a single grid image from captured frames and save it.

    Args:
        captured_frames: list of (index, frame) tuples
        output_dir: directory to save the grid image (defaults to cwd)
        tile_width/tile_height: size for each tile in the grid
        columns: optional fixed number of columns

    Returns:
        Path to saved grid image, or None if nothing to compose.
    """
    cv2 = _require_cv2()
    np = _require_numpy()
    if not captured_frames:
        return None

    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
    else:
        output_path = Path.cwd()

    rows, cols = _compute_grid_shape(len(captured_frames), columns=columns)
    if rows == 0 or cols == 0:
        return None

    # Create black canvas
    grid_h = rows * tile_height
    grid_w = cols * tile_width
    grid = np.zeros((grid_h, grid_w, 3), dtype=np.uint8)

    for idx, (cam_index, frame) in enumerate(captured_frames):
        r = idx // cols
        c = idx % cols

        # Ensure 3-channel BGR
        if frame is None:
            continue
        if len(frame.shape) == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

        tile = cv2.resize(frame, (tile_width, tile_height), interpolation=cv2.INTER_AREA)

        # Title overlay (camera index) using a real sans-serif font if available
        title = f"Camera {cam_index}"
        tile = _draw_title_on_tile(tile, title, font_path=font_path, font_size=font_size)

        y0, y1 = r * tile_height, (r + 1) * tile_height
        x0, x1 = c * tile_width, (c + 1) * tile_width
        grid[y0:y1, x0:x1] = tile

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    grid_filename = output_path / f"camera_grid_{timestamp}.jpg"
    cv2.imwrite(str(grid_filename), grid)
    return grid_filename


def capture_all_cameras(
    available_cameras,
    output_dir=None,
    grid_tile_width=640,
    grid_tile_height=480,
    grid_columns=None,
    grid_font_path=None,
    grid_font_size=28,
    save_individual_images=False,
):
    """Capture images from all available cameras.

    By default, writes ONLY one combined grid image.
    Optionally also saves per-camera images when save_individual_images=True.
    """
    print("\n📸 Capturing images from all cameras...")
    print("=" * 60)
    
    captured_files = []
    captured_frames = []
    
    for index, info in available_cameras:
        if save_individual_images:
            print(f"Capturing from camera {index}...", end=' ', flush=True)
        else:
            print(f"Capturing from camera {index}...", end=' ', flush=True)

        filename, frame = capture_camera_image(index, output_dir, save_image=save_individual_images)

        if frame is None:
            print("✗ Failed to capture")
            continue

        captured_frames.append((index, frame))

        if save_individual_images:
            if filename:
                print(f"✓ Saved to {filename}")
                captured_files.append((index, filename))
            else:
                # Shouldn't happen, but keep output consistent
                print("✓ Captured")
        else:
            print("✓ Captured")
    
    print("\n" + "=" * 60)
    if save_individual_images:
        print("\n📁 Captured images:")
        print("=" * 60)
        for index, filename in captured_files:
            print(f"  Camera {index}: {filename}")

    # Also create a single grid image with titles showing indices
    grid_file = compose_camera_grid(
        captured_frames,
        output_dir=output_dir,
        tile_width=grid_tile_width,
        tile_height=grid_tile_height,
        columns=grid_columns,
        font_path=grid_font_path,
        font_size=grid_font_size,
    )
    if grid_file:
        print("\n🧩 Combined grid image:")
        print("=" * 60)
        print(f"  {grid_file}")
    
    return captured_files, grid_file


def main():
    parser = argparse.ArgumentParser(
        description='Find and identify camera indices for your cameras'
    )
    parser.add_argument(
        '--max-index',
        type=int,
        default=10,
        help='Maximum camera index to check (default: 10)'
    )
    parser.add_argument(
        '--no-preview',
        action='store_true',
        help='Skip preview windows, just list available cameras'
    )
    parser.add_argument(
        '--preview-time',
        type=int,
        default=3,
        help='Seconds to show each camera preview (default: 3)'
    )
    parser.add_argument(
        '--capture-images',
        action='store_true',
        help='Capture frames from all available cameras and save a single combined grid image'
    )
    parser.add_argument(
        '--save-individual-images',
        action='store_true',
        help='When used with --capture-images, also save per-camera images (in addition to the grid)'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default=None,
        help='Directory to save captured images (default: current directory)'
    )
    parser.add_argument(
        '--grid-tile-width',
        type=int,
        default=640,
        help='Tile width for the combined grid image (default: 640)'
    )
    parser.add_argument(
        '--grid-tile-height',
        type=int,
        default=480,
        help='Tile height for the combined grid image (default: 480)'
    )
    parser.add_argument(
        '--grid-columns',
        type=int,
        default=None,
        help='Fixed number of columns for the combined grid image (default: auto)'
    )
    parser.add_argument(
        '--grid-font-path',
        type=str,
        default=None,
        help='Path to a .ttf font for grid labels (default: tries system DejaVuSans)'
    )
    parser.add_argument(
        '--grid-font-size',
        type=int,
        default=28,
        help='Font size for grid labels when using Pillow (default: 28)'
    )
    
    args = parser.parse_args()
    
    # Check if GUI is available
    gui_available = check_gui_available()
    if not gui_available and not args.no_preview:
        print("⚠️  Warning: OpenCV GUI support not available (no GTK+).")
        print("   Preview windows will be skipped. Use --no-preview to suppress this message.")
        print()
    
    print("🔍 Scanning for available cameras...")
    print("=" * 60)
    
    available_cameras = []
    
    for index in range(args.max_index + 1):
        print(f"Checking camera index {index}...", end=' ', flush=True)
        is_available, info = test_camera(
            index,
            show_preview=not args.no_preview and gui_available,
            preview_time=args.preview_time,
            gui_available=gui_available
        )
        
        if is_available:
            print("✓ Available")
            available_cameras.append((index, info))
        else:
            print("✗ Not available")
    
    print("\n" + "=" * 60)
    print("\n📋 Summary of available cameras:")
    print("=" * 60)
    
    if not available_cameras:
        print("❌ No cameras found!")
        print("\nTips:")
        print("  - Make sure your cameras are connected via USB")
        print("  - Try increasing --max-index if you have many cameras")
        print("  - On Linux, check /dev/video* devices: ls -l /dev/video*")
        return
    
    for index, info in available_cameras:
        print(f"\n📹 Camera Index: {index}")
        print(f"   Resolution: {info['width']}x{info['height']}")
        print(f"   FPS: {info['fps']}")
        if info['device_info']:
            print(f"   Device: {info['device_info']}")
    
    # Capture images if requested
    if args.capture_images:
        print("\nℹ️  Generating a combined grid image (titled by camera index).")
        print(f"   Grid tile size: {args.grid_tile_width}x{args.grid_tile_height}")
        if args.grid_columns:
            print(f"   Grid columns: {args.grid_columns}")
        if args.save_individual_images:
            print("   Also saving per-camera images.")
        capture_all_cameras(
            available_cameras,
            output_dir=args.output_dir,
            grid_tile_width=args.grid_tile_width,
            grid_tile_height=args.grid_tile_height,
            grid_columns=args.grid_columns,
            grid_font_path=args.grid_font_path,
            grid_font_size=args.grid_font_size,
            save_individual_images=args.save_individual_images,
        )
    
    print("\n" + "=" * 60)
    print("\n💡 Usage in lerobot-record:")
    print("   Add cameras to your command like this:")
    print("   --robot.cameras=\"{")
    for index, info in available_cameras:
        print(f"       camera_{index}: {{type: opencv, index_or_path: {index}, width: {info['width']}, height: {info['height']}, fps: 30}},")
    print("     }\"")
    print("\n   Then visually identify which camera is which by:")
    print("   1. Running lerobot-record with --display_data=true")
    print("   2. Observing which camera feed corresponds to which physical camera")
    print("   3. Or use --capture-images to save images from each camera")
    print("   4. Update the camera names (e.g., wrist1, wrist2) accordingly")


if __name__ == '__main__':
    main()

