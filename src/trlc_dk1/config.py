"""
Device paths for Jetson/Linux.

Avoid using volatile names like /dev/ttyACM0 which can change across reboots/replugs.
Prefer stable symlinks under /dev/serial/by-id (per-device) or /dev/serial/by-path
(per physical USB port).
"""

# Mapping intent:
# - Leader (teleop) uses `DynamixelMotorsBus` (see `trlc_dk1/leader.py`) and should be on the
#   "USB Single Serial" (1a86:55d3) adapters (best as /dev/serial/by-id/...).
# - Follower (robot) uses `MotorControl` over a raw serial link at 921600 (see `trlc_dk1/follower.py`)
#   and should be on the HDSC CDC devices (best as /dev/serial/by-path/... because serial can be duplicated).
#
# Identified by disconnecting (follower first, then leader) via `make watchports`:
# - 1a86:55d3 SN 5A46081965 -> left leader
# - 1a86:55d3 SN 5AB0181138 -> right leader
# - HDSC (loc 4.2.1.4)      -> left follower
# - HDSC (loc 4.2.1.1)      -> right follower

LEADER_LEFT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5A46081965-if00"
LEADER_RIGHT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AB0181138-if00"

# NOTE: the HDSC devices reported the same serial number, so by-id can be ambiguous.
# Using by-path keeps left/right stable as long as you keep the same physical ports.
FOLLOWER_LEFT = "/dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.4:1.0"
FOLLOWER_RIGHT = "/dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.1:1.0"

CAMERA_CONTEXT_INDEX = 4
CAMERA_RIGHT_INDEX = 0
CAMERA_LEFT_INDEX = 2

# ---------------------------------------------------------------------------
# Recording defaults (used by scripts like `scripts/record_dataset.py`)
# ---------------------------------------------------------------------------

# Control / recording
FPS = 30
NUM_EPISODES = 50
EPISODE_TIME_S = 1200
RESET_TIME_S = 0.0
TASK_DESCRIPTION = "Place PCB into testing device, wait, and place into right box."

# Robot behavior
JOINT_VELOCITY_SCALING = 1.0

# Camera capture defaults (OpenCV cameras)
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 360
CAMERA_FOURCC = "MJPG"

# Dataset behavior
PUSH_TO_HUB = False
RESUME = False

# Visualization
DISPLAY_DATA = False

# If you don't pass `--dataset.repo_id`, scripts can build it as "{USER}/{DATASET_NAME}".
# Set to None to keep auto-generating a timestamped dataset name.
DATASET_NAME: str | None = "pcb_basic"