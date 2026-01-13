#!/usr/bin/env python3
"""
Interactive helper to map robot USB serial devices to stable Linux paths.

This guides the user through unplug/plug cycles for each labeled device and prints:
- the volatile device node (e.g. /dev/ttyACM2)
- stable symlinks (e.g. /dev/serial/by-id/... and /dev/serial/by-path/...)

Typical usage:
  python examples/map_ports_guided.py

Notes:
- This relies on pyserial for device discovery.
- Prefer using /dev/serial/by-id when available and unique.
- If multiple devices share a serial number (common!), prefer /dev/serial/by-path.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


def _require_pyserial():
    try:
        import serial.tools.list_ports  # type: ignore

        return serial.tools.list_ports
    except ModuleNotFoundError:
        print("❌ Missing dependency: pyserial.")
        print("   Install with: pip install pyserial")
        raise SystemExit(2)


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    hwid: str
    vid: Optional[int]
    pid: Optional[int]
    serial_number: Optional[str]
    manufacturer: Optional[str]
    product: Optional[str]
    location: Optional[str]

    @staticmethod
    def from_pyserial(p) -> "PortInfo":
        return PortInfo(
            device=str(getattr(p, "device", "")),
            description=str(getattr(p, "description", "")),
            hwid=str(getattr(p, "hwid", "")),
            vid=getattr(p, "vid", None),
            pid=getattr(p, "pid", None),
            serial_number=getattr(p, "serial_number", None),
            manufacturer=getattr(p, "manufacturer", None),
            product=getattr(p, "product", None),
            location=getattr(p, "location", None),
        )


def _stable_symlinks_for_device(dev: str) -> Tuple[List[str], List[str]]:
    """
    Return (by_id, by_path) symlink(s) pointing to the same device node.
    """
    by_id: List[str] = []
    by_path: List[str] = []

    try:
        dev_real = os.path.realpath(dev)
    except Exception:
        return by_id, by_path

    for root, out in (
        ("/dev/serial/by-id", by_id),
        ("/dev/serial/by-path", by_path),
    ):
        try:
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                p = os.path.join(root, name)
                if not os.path.islink(p):
                    continue
                try:
                    if os.path.realpath(p) == dev_real:
                        out.append(p)
                except Exception:
                    continue
        except Exception:
            continue

    return by_id, by_path


def _snapshot_ports(list_ports_mod, include_prefixes: Sequence[str]) -> Dict[str, PortInfo]:
    ports: Dict[str, PortInfo] = {}
    for p in list_ports_mod.comports():
        pi = PortInfo.from_pyserial(p)
        if not pi.device:
            continue
        if include_prefixes and not any(pi.device.startswith(pref) for pref in include_prefixes):
            continue
        ports[pi.device] = pi
    return ports


def _diff(before: Dict[str, PortInfo], after: Dict[str, PortInfo]) -> Tuple[List[str], List[str]]:
    before_set = set(before.keys())
    after_set = set(after.keys())
    ejected = sorted(before_set - after_set)
    inserted = sorted(after_set - before_set)
    return ejected, inserted


def _wait_for_exactly_one_change(
    list_ports_mod,
    include_prefixes: Sequence[str],
    prev: Dict[str, PortInfo],
    want: str,
    timeout_s: float,
    poll_s: float,
) -> Tuple[str, Dict[str, PortInfo]]:
    """
    Wait until exactly one device is ejected/inserted.
    Returns (device_path, new_snapshot).
    """
    assert want in ("ejected", "inserted")
    end = time.time() + timeout_s

    while time.time() < end:
        cur = _snapshot_ports(list_ports_mod, include_prefixes)
        ejected, inserted = _diff(prev, cur)

        candidates = ejected if want == "ejected" else inserted
        if len(candidates) == 1:
            return candidates[0], cur
        if len(candidates) > 1:
            raise RuntimeError(
                f"Detected multiple ports {want} at once: {candidates}. "
                "Please ensure only one device is unplugged/plugged at a time."
            )

        time.sleep(max(0.02, poll_s))

    raise TimeoutError(f"Timed out waiting for a port to be {want}.")


def _fmt_vidpid(p: PortInfo) -> str:
    if p.vid is None or p.pid is None:
        return ""
    return f"{p.vid:04x}:{p.pid:04x}"


def _print_table(rows: List[dict]) -> None:
    # Minimal dependency: print a nice fixed-width table.
    headers = ["label", "temp", "by-id", "by-path", "vid:pid", "sn", "loc"]
    cols: Dict[str, int] = {h: len(h) for h in headers}
    for r in rows:
        for h in headers:
            cols[h] = max(cols[h], len(str(r.get(h, ""))))

    def line(ch: str = "-") -> str:
        return "+".join([ch * (cols[h] + 2) for h in headers])

    def fmt_row(r: dict) -> str:
        return " | ".join([str(r.get(h, "")).ljust(cols[h]) for h in headers])

    print()
    print(line("="))
    print(fmt_row({h: h for h in headers}))
    print(line("="))
    for r in rows:
        print(fmt_row(r))
    print(line("="))
    print()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Guided mapping for robot USB serial ports.")
    ap.add_argument(
        "--timeout",
        type=float,
        default=25.0,
        help="Seconds to wait for each unplug/plug event (default: 25).",
    )
    ap.add_argument(
        "--poll",
        type=float,
        default=0.25,
        help="Polling interval in seconds (default: 0.25).",
    )
    ap.add_argument(
        "--include-prefix",
        action="append",
        default=["/dev/ttyACM", "/dev/ttyUSB"],
        help="Device path prefixes to include. Can be repeated (default: /dev/ttyACM and /dev/ttyUSB).",
    )
    args = ap.parse_args(argv)

    list_ports_mod = _require_pyserial()
    include_prefixes: List[str] = list(args.include_prefix or [])

    steps = [
        ("leader_left", "Leader LEFT (teleop)"),
        ("follower_left", "Follower LEFT (robot)"),
        ("leader_right", "Leader RIGHT (teleop)"),
        ("follower_right", "Follower RIGHT (robot)"),
    ]

    print("This will map each device to both its volatile and stable Linux device paths.")
    print("Make sure you can unplug/plug only ONE device at a time.\n")

    prev = _snapshot_ports(list_ports_mod, include_prefixes)
    if not prev:
        print("⚠️  No serial ports found. Is everything plugged in? Do you need sudo permissions?")
        print("   Try: ls -l /dev/ttyACM* /dev/ttyUSB* 2>/dev/null")
        return 2

    rows: List[dict] = []

    for key, human in steps:
        print(f"### {human}")
        print("Unplug it now...")
        input("Press Enter once it is unplugged. ")

        try:
            gone, snap_after_unplug = _wait_for_exactly_one_change(
                list_ports_mod,
                include_prefixes,
                prev,
                want="ejected",
                timeout_s=float(args.timeout),
                poll_s=float(args.poll),
            )
        except Exception as e:
            print(f"❌ Could not detect an unplug event: {e}")
            return 2

        print(f"Detected unplug: {gone}")
        print("Now plug it back in...")
        input("Press Enter once it is plugged in. ")

        try:
            new_dev, snap_after_plug = _wait_for_exactly_one_change(
                list_ports_mod,
                include_prefixes,
                snap_after_unplug,
                want="inserted",
                timeout_s=float(args.timeout),
                poll_s=float(args.poll),
            )
        except Exception as e:
            print(f"❌ Could not detect a plug-in event: {e}")
            return 2

        pi = snap_after_plug.get(new_dev)
        by_id, by_path = _stable_symlinks_for_device(new_dev)
        rows.append(
            {
                "label": key,
                "temp": new_dev,
                "by-id": by_id[0] if by_id else "",
                "by-path": by_path[0] if by_path else "",
                "vid:pid": _fmt_vidpid(pi) if pi else "",
                "sn": (pi.serial_number or "") if pi else "",
                "loc": (pi.location or "") if pi else "",
            }
        )

        # Move forward
        prev = snap_after_plug
        print()

    print("✅ Mapping complete.")
    _print_table(rows)

    print("Copy/paste suggestions (prefer by-id if unique; otherwise by-path):\n")
    for r in rows:
        preferred = r["by-id"] or r["by-path"] or r["temp"]
        print(f'{r["label"].upper():<14} = "{preferred}"')

    print("\nTip: list all stable symlinks with:")
    print("  ls -l /dev/serial/by-id/ /dev/serial/by-path/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


