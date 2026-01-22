#!/usr/bin/env python3
"""
Simple indefinite teleoperation script for TRLC DK1.

Runs teleoperation continuously until stopped with Ctrl+C.
Does not record any data - just mirrors leader arm movements to follower arms.

Usage:
    python scripts/teleop.py
    python scripts/teleop.py --fps 60
    python scripts/teleop.py --robot.joint_velocity_scaling 0.5
"""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Make it runnable as `python scripts/teleop.py` without requiring an editable install.
for p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import argparse
import signal
import time
from collections import deque

from trlc_dk1.bi_follower import BiDK1Follower, BiDK1FollowerConfig
from trlc_dk1.bi_leader import BiDK1Leader, BiDK1LeaderConfig
from trlc_dk1.config import (
    FOLLOWER_LEFT,
    FOLLOWER_RIGHT,
    FPS as DEFAULT_FPS,
    JOINT_VELOCITY_SCALING as DEFAULT_JOINT_VELOCITY_SCALING,
    LEADER_LEFT,
    LEADER_RIGHT,
)


def _make_progress_bar(elapsed: float, width: int = 20) -> str:
    """Create a simple spinning animation."""
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    idx = int(elapsed * 10) % len(spinner)
    return spinner[idx]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run indefinite teleoperation for TRLC DK1.")

    ap.add_argument(
        "--fps",
        type=int,
        default=DEFAULT_FPS,
        help=f"Control loop frequency (default: {DEFAULT_FPS})",
    )
    ap.add_argument(
        "--robot.joint_velocity_scaling",
        dest="joint_velocity_scaling",
        type=float,
        default=DEFAULT_JOINT_VELOCITY_SCALING,
        help=f"Joint velocity scaling factor (default: {DEFAULT_JOINT_VELOCITY_SCALING})",
    )
    ap.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return ap.parse_args()


def make_robot_and_teleop(*, joint_velocity_scaling: float, debug: bool = False):
    """Create robot (follower) and teleop (leader) without cameras."""
    robot = BiDK1Follower(
        BiDK1FollowerConfig(
            left_arm_port=FOLLOWER_LEFT,
            right_arm_port=FOLLOWER_RIGHT,
            joint_velocity_scaling=joint_velocity_scaling,
            debug=debug,
            cameras={},  # No cameras for teleop-only mode
        )
    )
    teleop = BiDK1Leader(
        BiDK1LeaderConfig(
            left_arm_port=LEADER_LEFT,
            right_arm_port=LEADER_RIGHT,
            debug=debug,
        )
    )
    return robot, teleop


def teleop_loop(robot, teleop, fps: int):
    """
    Run teleoperation indefinitely until interrupted.
    
    Reads positions from leader arms and sends them to follower arms.
    """
    from lerobot.utils.robot_utils import busy_wait
    
    print(f"\nTeleoperation running at {fps} Hz")
    print("Press Ctrl+C to stop\n")
    
    running = True
    
    def signal_handler(sig, frame):
        nonlocal running
        print("\n\nStopping teleoperation...")
        running = False
    
    signal.signal(signal.SIGINT, signal_handler)
    
    frame_times = deque(maxlen=30)
    start_time = time.perf_counter()
    frame_count = 0
    
    while running:
        loop_start = time.perf_counter()
        
        try:
            # Get action from leader arms
            action = teleop.get_action()
            
            # Send action to follower arms
            robot.send_action(action)
            
            # Timing
            dt_s = time.perf_counter() - loop_start
            busy_wait(1 / fps - dt_s)
            
            loop_time = time.perf_counter() - loop_start
            frame_times.append(loop_time)
            frame_count += 1
            
            # Display status
            elapsed = time.perf_counter() - start_time
            avg_hz = len(frame_times) / sum(frame_times) if frame_times else 0
            spinner = _make_progress_bar(elapsed)
            
            # Format elapsed time
            mins, secs = divmod(int(elapsed), 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                time_str = f"{hours}h {mins:02d}m {secs:02d}s"
            elif mins > 0:
                time_str = f"{mins}m {secs:02d}s"
            else:
                time_str = f"{secs}s"
            
            print(f"\r{spinner} Teleop: {time_str} | {avg_hz:.1f} Hz | {frame_count} frames", end="", flush=True)
            
        except Exception as e:
            print(f"\nError in teleop loop: {e}")
            running = False
    
    print(f"\n\nTeleoperation finished after {frame_count} frames")


def main():
    args = parse_args()
    
    print("=" * 50)
    print("TRLC DK1 Teleoperation")
    print("=" * 50)
    print(f"FPS: {args.fps}")
    print(f"Joint velocity scaling: {args.joint_velocity_scaling}")
    print()
    
    print("Creating robot and teleop...")
    robot, teleop = make_robot_and_teleop(
        joint_velocity_scaling=args.joint_velocity_scaling,
        debug=args.debug,
    )
    
    # Connect teleop (leader) FIRST - same order as lerobot-teleoperate
    print("Connecting teleop (leader arms)...")
    teleop.connect()
    print("Teleop connected.")
    
    print("Connecting robot (follower arms)...")
    robot.connect()
    print("Robot connected.")
    
    try:
        teleop_loop(robot, teleop, args.fps)
    finally:
        print("\nDisconnecting...")
        robot.disconnect()
        teleop.disconnect()
        print("Done.")


if __name__ == "__main__":
    main()
