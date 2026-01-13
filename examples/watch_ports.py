#!/usr/bin/env python3
"""
Continuously watch serial ports and report hotplug events.

This is similar to `lerobot-find-port`, but non-interactive: you can unplug/plug
USB devices and this script will keep printing events like:

  /dev/ttyACM2 ejected
  /dev/ttyACM1 inserted

Usage:
  python examples/watch_ports.py
  python examples/watch_ports.py --interval 0.2
  python examples/watch_ports.py --verbose
  python examples/watch_ports.py --include ttyACM --include ttyUSB
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def _require_pyserial():
    try:
        import serial.tools.list_ports  # type: ignore
        return serial.tools.list_ports
    except ModuleNotFoundError:
        print("❌ Missing dependency: pyserial.")
        print("   Install with: pip install pyserial")
        sys.exit(2)


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
        # `ListPortInfo` has these attrs on most platforms; be defensive.
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


def _snapshot_ports(list_ports_mod, include_globs: Sequence[str]) -> Dict[str, PortInfo]:
    ports: Dict[str, PortInfo] = {}
    for p in list_ports_mod.comports():
        pi = PortInfo.from_pyserial(p)
        if not pi.device:
            continue
        if include_globs and not _matches_any(pi.device, include_globs):
            continue
        ports[pi.device] = pi
    return ports


def _matches_any(s: str, globs: Sequence[str]) -> bool:
    return any(fnmatch.fnmatch(s, g) for g in globs)


def _stable_symlinks_for_device(dev: str) -> List[str]:
    """
    Return stable symlink(s) for a serial device.

    On Linux this is usually available under:
      - /dev/serial/by-id/   (stable per device)
      - /dev/serial/by-path/ (stable per physical port path)
    """
    out: List[str] = []
    try:
        dev_real = os.path.realpath(dev)
    except Exception:
        return out

    for root in ("/dev/serial/by-id", "/dev/serial/by-path"):
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

    return out


def _fmt_meta(p: PortInfo) -> str:
    parts: List[str] = []
    if p.vid is not None and p.pid is not None:
        parts.append(f"VID:PID {p.vid:04x}:{p.pid:04x}")
    if p.serial_number:
        parts.append(f"SN {p.serial_number}")
    if p.location:
        parts.append(f"loc {p.location}")
    stable = _stable_symlinks_for_device(p.device)
    if stable:
        parts.append("stable " + " | ".join(stable))
    # Prefer product/manufacturer if present, else description.
    human = " / ".join([x for x in [p.manufacturer, p.product] if x])
    if human:
        parts.append(human)
    elif p.description and p.description != "n/a":
        parts.append(p.description)
    elif p.hwid and p.hwid != "n/a":
        parts.append(p.hwid)
    return ", ".join(parts)


def _diff(
    before: Dict[str, PortInfo], after: Dict[str, PortInfo]
) -> Tuple[List[str], List[str]]:
    before_set = set(before.keys())
    after_set = set(after.keys())
    ejected = sorted(before_set - after_set)
    inserted = sorted(after_set - before_set)
    return ejected, inserted


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Watch serial ports and report hotplug events.")
    ap.add_argument(
        "--interval",
        type=float,
        default=0.25,
        help="Polling interval in seconds (default: 0.25).",
    )
    ap.add_argument(
        "--include",
        action="append",
        default=[],
        help=(
            "Glob(s) to include (can be repeated). "
            "Examples: '/dev/ttyACM*', '/dev/ttyUSB*'. "
            "If omitted, includes all ports returned by pyserial."
        ),
    )
    ap.add_argument(
        "--no-initial",
        action="store_true",
        help="Do not print the initial snapshot of currently-present ports.",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Include port metadata (VID:PID, serial, product) on events.",
    )
    ap.add_argument(
        "--timestamp",
        action="store_true",
        help="Prefix events with a local timestamp.",
    )
    args = ap.parse_args(argv)

    list_ports_mod = _require_pyserial()

    # If the user passes short patterns like "ttyACM", treat it as a basename match.
    include_globs = list(args.include)
    include_globs = [
        g if any(ch in g for ch in "*?[]") else f"*{g}*"
        for g in include_globs
    ]

    prev = _snapshot_ports(list_ports_mod, include_globs)
    if not args.no_initial:
        for dev in sorted(prev.keys()):
            if args.verbose:
                meta = _fmt_meta(prev[dev])
                print(f"{dev} present ({meta})" if meta else f"{dev} present")
            else:
                print(f"{dev} present")

    try:
        while True:
            time.sleep(max(0.01, float(args.interval)))
            cur = _snapshot_ports(list_ports_mod, include_globs)
            ejected, inserted = _diff(prev, cur)
            if ejected or inserted:
                ts = ""
                if args.timestamp:
                    ts = time.strftime("%Y-%m-%d %H:%M:%S ")  # local time
                for dev in ejected:
                    print(f"{ts}{dev} ejected")
                for dev in inserted:
                    if args.verbose:
                        meta = _fmt_meta(cur[dev])
                        print(f"{ts}{dev} inserted ({meta})" if meta else f"{ts}{dev} inserted")
                    else:
                        print(f"{ts}{dev} inserted")
                sys.stdout.flush()
            prev = cur
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())


