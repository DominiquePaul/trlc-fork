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
# Current mapping (from user, using volatile /dev/ttyACM* numbers):
# - leader left   -> ttyACM1
# - follower left -> ttyACM0
# - leader right  -> ttyACM3
# - follower right-> ttyACM2
#
# Observed stable symlinks (from `python examples/watch_ports.py --verbose`):
# - 1a86:55d3 SN 5AB0181138 -> /dev/serial/by-id/usb-1a86_USB_Single_Serial_5AB0181138-if00
# - 1a86:55d3 SN 5A46081965 -> /dev/serial/by-id/usb-1a86_USB_Single_Serial_5A46081965-if00
# - HDSC (loc 4.2.1.3)      -> /dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.3:1.0
# - HDSC (loc 4.2.1.1)      -> /dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.1:1.0

LEADER_LEFT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AB0181138-if00"
LEADER_RIGHT = "/dev/serial/by-id/usb-1a86_USB_Single_Serial_5A46081965-if00"

# NOTE: the HDSC devices reported the same serial number, so by-id can be ambiguous.
# Using by-path keeps left/right stable as long as you keep the same physical ports.
FOLLOWER_LEFT = "/dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.3:1.0"
FOLLOWER_RIGHT = "/dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.1:1.0"

CAMERA_CONTEXT_INDEX = 0
CAMERA_RIGHT_INDEX = 2
CAMERA_LEFT_INDEX = 4