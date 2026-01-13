#!/usr/bin/env python3
"""
Summarize USB topology and speeds on Linux (e.g., Jetson).

What it prints:
- Root hubs (usb1, usb2, ...) and their current speed
- All USB devices with:
  - sysfs name (e.g. 1-4.1.1.2)
  - bus number
  - port chain (devpath)
  - negotiated link speed (Mb/s) from sysfs `speed`
  - device advertised USB version (bcdUSB) as a hint of max capability
  - idVendor:idProduct + manufacturer/product strings when available
- Optional: map /dev/video* to underlying USB device + speed

Notes:
- USB-C is just the connector; the actual negotiated speed is what matters.
- sysfs `speed` is the negotiated link speed (what you actually get).
- bcdUSB is the device's advertised USB version (upper bound for the device, not the hub).
"""

from __future__ import annotations

import argparse
import glob
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


SYS_USB = Path("/sys/bus/usb/devices")


def _read_text(p: Path) -> Optional[str]:
    try:
        return p.read_text().strip()
    except Exception:
        return None


def _read_int(p: Path) -> Optional[int]:
    s = _read_text(p)
    if s is None:
        return None
    try:
        return int(s)
    except Exception:
        return None


def _read_float(p: Path) -> Optional[float]:
    s = _read_text(p)
    if s is None:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _bcdusb_to_str(bcdusb: Optional[str]) -> Optional[str]:
    # bcdUSB is hex-ish like "2.00" or "3.10" or sometimes "0x0310" depending on kernel/driver.
    if not bcdusb:
        return None
    s = bcdusb.strip().lower()
    if s.startswith("0x"):
        try:
            v = int(s, 16)
            major = (v >> 8) & 0xFF
            minor = v & 0xFF
            return f"{major}.{minor:02d}"
        except Exception:
            return bcdusb
    return bcdusb


@dataclass
class UsbNode:
    sys_name: str  # e.g. "1-4.2" or "usb1"
    path: Path
    busnum: Optional[int]
    devnum: Optional[int]
    devpath: Optional[str]  # e.g. "4.2"
    speed_mbps: Optional[float]
    bcdusb: Optional[str]
    vid: Optional[str]
    pid: Optional[str]
    manufacturer: Optional[str]
    product: Optional[str]

    @property
    def is_root_hub(self) -> bool:
        return self.sys_name.startswith("usb") and self.busnum is not None

    @property
    def is_usb_device(self) -> bool:
        return self.busnum is not None and self.devpath is not None and self.speed_mbps is not None

    @property
    def port_chain(self) -> Optional[str]:
        if self.devpath is None:
            return None
        return "->".join(self.devpath.split("."))


def load_usb_nodes() -> list[UsbNode]:
    nodes: list[UsbNode] = []
    if not SYS_USB.exists():
        return nodes

    for p in sorted(SYS_USB.iterdir(), key=lambda x: x.name):
        if not p.is_dir():
            continue
        sys_name = p.name
        busnum = _read_int(p / "busnum")
        devnum = _read_int(p / "devnum")
        devpath = _read_text(p / "devpath")
        speed = _read_float(p / "speed")
        bcdusb = _bcdusb_to_str(_read_text(p / "bcdUSB"))
        vid = _read_text(p / "idVendor")
        pid = _read_text(p / "idProduct")
        manufacturer = _read_text(p / "manufacturer")
        product = _read_text(p / "product")

        nodes.append(
            UsbNode(
                sys_name=sys_name,
                path=p,
                busnum=busnum,
                devnum=devnum,
                devpath=devpath,
                speed_mbps=speed,
                bcdusb=bcdusb,
                vid=vid,
                pid=pid,
                manufacturer=manufacturer,
                product=product,
            )
        )
    return nodes


def _video_to_usb_sysfs(video: str) -> Optional[Path]:
    # /sys/class/video4linux/videoX/device -> ... -> USB device node that contains idVendor
    p = Path("/sys/class/video4linux") / video / "device"
    if not p.exists():
        return None
    cur = Path(os.path.realpath(p))
    for _ in range(12):
        if (cur / "idVendor").exists():
            return cur
        cur = cur.parent
    return None


def print_summary(nodes: list[UsbNode], *, include_video: bool) -> None:
    roots = [n for n in nodes if n.is_root_hub]
    devices = [n for n in nodes if n.is_usb_device and not n.is_root_hub]

    print("== USB root hubs ==")
    if not roots:
        print("(none found)")
    for r in roots:
        print(
            f"- {r.sys_name}: bus={r.busnum} speed={r.speed_mbps}Mb/s"
            + (f" bcdUSB={r.bcdusb}" if r.bcdusb else "")
        )

    print("\n== USB devices (negotiated link speeds) ==")
    if not devices:
        print("(none found)")
    for d in devices:
        id_str = f"{d.vid}:{d.pid}" if d.vid and d.pid else "????:????"
        name = " ".join([x for x in [d.manufacturer, d.product] if x]) or "-"
        print(
            f"- {d.sys_name}: bus={d.busnum} dev={d.devnum} devpath={d.devpath} ports={d.port_chain} "
            f"speed={d.speed_mbps}Mb/s dev_bcdUSB={d.bcdusb or '-'} id={id_str} name={name}"
        )

    if include_video:
        print("\n== /dev/video* -> USB mapping ==")
        videos = sorted(glob.glob("/dev/video[0-9]*"), key=lambda s: int(s.split("video")[-1]))
        if not videos:
            print("(no /dev/video* found)")
            return
        for vp in videos:
            v = os.path.basename(vp)
            usb = _video_to_usb_sysfs(v)
            if not usb:
                print(f"- {v}: (no USB sysfs mapping found)")
                continue
            busnum = _read_text(usb / "busnum")
            devnum = _read_text(usb / "devnum")
            devpath = _read_text(usb / "devpath")
            speed = _read_text(usb / "speed")
            vid = _read_text(usb / "idVendor")
            pid = _read_text(usb / "idProduct")
            product = _read_text(usb / "product")
            print(
                f"- {v}: usb={usb.name} bus={busnum} dev={devnum} devpath={devpath} speed={speed}Mb/s "
                f"id={vid}:{pid} product={product}"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--include-video", action="store_true", help="Also map /dev/video* -> USB device + speed")
    args = ap.parse_args()

    nodes = load_usb_nodes()
    if not nodes:
        print(f"No USB sysfs nodes found under {SYS_USB}")
        return 2

    print_summary(nodes, include_video=args.include_video)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


