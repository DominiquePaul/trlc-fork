import os
import sys

# Suppress FFmpeg logs BEFORE importing av/lerobot (environment variables must be set early)
os.environ["AV_LOG_LEVEL"] = "fatal"
# Force disable FFmpeg report files (FFREPORT="" or unset prevents log file creation)
os.environ.pop("FFREPORT", None)  # Remove if set

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
# Make it runnable as `python scripts/record_dataset.py` without requiring an editable install.
for p in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from lerobot.datasets import lerobot_dataset as _ld
from lerobot.datasets import video_utils

try:
    # Historical location (if you installed a package variant that vendors these patches)
    from trlc_dk1.patches.video_encoding import fast_encode_video_frames  # type: ignore
except ModuleNotFoundError:
    # Local repo location
    print("Using local repo location for video encoding")
    from patches.video_encoding import fast_encode_video_frames

# Patch both the source module and the module that imported it directly
video_utils.encode_video_frames = _ld.encode_video_frames = fast_encode_video_frames

import argparse
import select
import signal
import termios
import time
import traceback
import tty
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Thread

import numpy as np

# Suppress PyAV (FFmpeg) info/warning messages during video concatenation
try:
    import av
    # Set to FATAL (most severe level) to suppress INFO/WARNING/ERROR messages
    av.logging.set_level(av.logging.FATAL)
    
    # Also install a null callback to completely silence FFmpeg's internal logging
    # This catches logs like "[mov,mp4,m4a,3gp...] Auto-inserting h264_mp4toannexb..."
    @av.logging.Callback
    def _silence_ffmpeg(level, msg):
        pass  # Swallow all FFmpeg log messages
    
    av.logging.set_callback(_silence_ffmpeg)
except ImportError:
    print("Could not import av. Logging not suppressed.")
except Exception:
    # If callback setup fails, at least we have FATAL level set
    pass

# Monkey Patch: Speed up image stats computation during recording
from lerobot.cameras.opencv import OpenCVCameraConfig
from lerobot.datasets.lerobot_dataset import LeRobotDataset
from lerobot.datasets.utils import hw_to_dataset_features
from lerobot.processor.factory import make_default_processors
from lerobot.scripts import lerobot_record as _lerobot_record_module
from lerobot.utils.utils import log_say
from lerobot.utils.visualization_utils import init_rerun
from PIL import Image


def _make_progress_bar(elapsed: float, total: float, width: int = 30) -> str:
    """Create a simple ASCII progress bar."""
    if total <= 0:
        return f"[{'?' * width}] {elapsed:.1f}s / ??s"
    
    ratio = min(elapsed / total, 1.0)
    filled = int(width * ratio)
    empty = width - filled
    bar = "█" * filled + "░" * empty
    return f"[{bar}] {elapsed:.1f}s / {total:.1f}s"


def _patched_record_loop(
    robot,
    events,
    fps,
    teleop_action_processor,
    robot_action_processor,
    robot_observation_processor,
    dataset=None,
    teleop=None,
    policy=None,
    preprocessor=None,
    postprocessor=None,
    control_time_s=None,
    single_task=None,
    display_data=False,
):
    """Patched record_loop with cleaner progress bar display."""
    from lerobot.utils.robot_utils import busy_wait
    from lerobot.datasets.utils import build_dataset_frame
    from lerobot.scripts.lerobot_record import (
        ACTION, OBS_STR, log_rerun_data, make_robot_action, predict_action,
    )
    from lerobot.teleoperators import Teleoperator
    from lerobot.utils.utils import get_safe_torch_device
    import logging
    
    if dataset is not None and dataset.fps != fps:
        raise ValueError(f"The dataset fps should be equal to requested fps ({dataset.fps} != {fps}).")

    # Reset policy and processor if they are provided
    if policy is not None and preprocessor is not None and postprocessor is not None:
        policy.reset()
        preprocessor.reset()
        postprocessor.reset()

    timestamp = 0
    start_episode_t = time.perf_counter()
    frame_times = deque(maxlen=10)
    frame_count = 0
    
    while timestamp < control_time_s:
        start_loop_t = time.perf_counter()

        if events["exit_early"]:
            events["exit_early"] = False
            break

        # Get robot observation
        obs = robot.get_observation()

        # Applies a pipeline to the raw robot observation
        obs_processed = robot_observation_processor(obs)

        if policy is not None or dataset is not None:
            observation_frame = build_dataset_frame(dataset.features, obs_processed, prefix=OBS_STR)

        # Get action from either policy or teleop
        if policy is not None and preprocessor is not None and postprocessor is not None:
            action_values = predict_action(
                observation=observation_frame,
                policy=policy,
                device=get_safe_torch_device(policy.config.device),
                preprocessor=preprocessor,
                postprocessor=postprocessor,
                use_amp=policy.config.use_amp,
                task=single_task,
                robot_type=robot.robot_type,
            )
            act_processed_policy = make_robot_action(action_values, dataset.features)
            action_values = act_processed_policy
            robot_action_to_send = robot_action_processor((act_processed_policy, obs))

        elif policy is None and isinstance(teleop, Teleoperator):
            act = teleop.get_action()
            act_processed_teleop = teleop_action_processor((act, obs))
            action_values = act_processed_teleop
            robot_action_to_send = robot_action_processor((act_processed_teleop, obs))

        else:
            logging.info(
                "No policy or teleoperator provided, skipping action generation."
            )
            continue

        # Send action to robot
        _sent_action = robot.send_action(robot_action_to_send)

        # Write to dataset
        if dataset is not None:
            action_frame = build_dataset_frame(dataset.features, action_values, prefix=ACTION)
            frame = {**observation_frame, **action_frame, "task": single_task}
            dataset.add_frame(frame)

        if display_data:
            log_rerun_data(observation=obs_processed, action=action_values)

        dt_s = time.perf_counter() - start_loop_t
        busy_wait(1 / fps - dt_s)

        loop_s = time.perf_counter() - start_loop_t
        timestamp = time.perf_counter() - start_episode_t
        frame_count += 1
        frame_times.append(loop_s)
        avg_s = sum(frame_times) / len(frame_times)
        
        # Clean progress bar display
        progress = _make_progress_bar(timestamp, control_time_s)
        hz = int(1 / avg_s) if avg_s > 0 else 0
        print(f"\r{progress} @ {hz}Hz", end="", flush=True)


# Patch the record_loop function
record_loop = _patched_record_loop

from trlc_dk1.bi_follower import BiDK1Follower, BiDK1FollowerConfig
from trlc_dk1.bi_leader import BiDK1Leader, BiDK1LeaderConfig
from trlc_dk1.config import (
    CAMERA_CONTEXT_INDEX,
    CAMERA_FOURCC as DEFAULT_CAMERA_FOURCC,
    CAMERA_HEIGHT as DEFAULT_CAMERA_HEIGHT,
    CAMERA_LEFT_INDEX,
    CAMERA_RIGHT_INDEX,
    CAMERA_WIDTH as DEFAULT_CAMERA_WIDTH,
    DATASET_NAME as DEFAULT_DATASET_NAME,
    DISPLAY_DATA as DEFAULT_DISPLAY_DATA,
    EPISODE_TIME_S as DEFAULT_EPISODE_TIME_S,
    FOLLOWER_LEFT,
    FOLLOWER_RIGHT,
    FPS as DEFAULT_FPS,
    JOINT_VELOCITY_SCALING as DEFAULT_JOINT_VELOCITY_SCALING,
    LEADER_LEFT,
    LEADER_RIGHT,
    NUM_EPISODES as DEFAULT_NUM_EPISODES,
    PUSH_TO_HUB as DEFAULT_PUSH_TO_HUB,
    RESET_TIME_S as DEFAULT_RESET_TIME_S,
    RESUME as DEFAULT_RESUME,
    TASK_DESCRIPTION as DEFAULT_TASK_DESCRIPTION,
)

# _original_sample_images = compute_stats.sample_images
# _original_load_image = compute_stats.load_image_as_numpy


def fast_load_image_as_numpy(path, dtype=np.float32, channel_first=False):
    """Faster image loading using PIL directly without extra conversions."""
    img = Image.open(path)
    # Convert to RGB if needed
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Convert to numpy array
    arr = np.array(img, dtype=dtype)
    if channel_first:
        arr = arr.transpose(2, 0, 1)  # HWC -> CHW
    return arr


def fast_sample_images(image_paths: list[str]) -> np.ndarray:
    """Parallel image loading for faster stats computation."""
    from lerobot.datasets.compute_stats import (
        auto_downsample_height_width,
        sample_indices,
    )

    sampled_indices = sample_indices(len(image_paths))

    def load_single_image(idx):
        path = image_paths[idx]
        img = fast_load_image_as_numpy(path, dtype=np.uint8, channel_first=True)
        return auto_downsample_height_width(img)

    # Load images in parallel using thread pool
    with ThreadPoolExecutor(max_workers=8) as executor:
        images_list = list(executor.map(load_single_image, sampled_indices))

    # Stack into single array
    images = np.stack(images_list, axis=0)
    return images


# Apply monkey patches
# compute_stats.sample_images = fast_sample_images
# compute_stats.load_image_as_numpy = fast_load_image_as_numpy


_terminal_old_settings = None

def start_terminal_keyboard_listener(events: dict) -> Thread | None:
    """
    Terminal-based keyboard listener that works over SSH (no X11 needed).
    
    Controls:
    - Right arrow (→): Exit current episode early
    - Left arrow (←): Mark for re-recording
    - Escape: Stop recording entirely
    """
    global _terminal_old_settings
    
    # Check if we have a real TTY
    if not sys.stdin.isatty():
        return None
    
    # Save terminal settings globally so we can restore on exit
    _terminal_old_settings = termios.tcgetattr(sys.stdin)
    
    def listener_thread():
        try:
            # Set terminal to raw mode (no echo, immediate input)
            tty.setcbreak(sys.stdin.fileno())
            
            while not events.get("_listener_stop", False):
                # Check if input is available (non-blocking)
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    ch = sys.stdin.read(1)
                    
                    # Handle escape sequences (arrow keys)
                    if ch == '\x1b':  # Escape character
                        # Read the rest of the escape sequence
                        if select.select([sys.stdin], [], [], 0.1)[0]:
                            ch2 = sys.stdin.read(1)
                            if ch2 == '[':
                                if select.select([sys.stdin], [], [], 0.1)[0]:
                                    ch3 = sys.stdin.read(1)
                                    if ch3 == 'C':  # Right arrow
                                        print("\n[→] Exit early from current episode")
                                        events["exit_early"] = True
                                    elif ch3 == 'D':  # Left arrow
                                        print("\n[←] Re-record this episode")
                                        events["rerecord_episode"] = True
                                        events["exit_early"] = True
                            else:
                                # Just Escape key (no arrow) - discard and re-record (same as left arrow)
                                print("\n[Esc] Discard & re-record this episode")
                                events["rerecord_episode"] = True
                                events["exit_early"] = True
                        else:
                            # Just Escape key - discard and re-record (same as left arrow)
                            print("\n[Esc] Discard & re-record this episode")
                            events["rerecord_episode"] = True
                            events["exit_early"] = True
        except Exception:
            pass
        finally:
            # Restore terminal settings
            restore_terminal()
    
    thread = Thread(target=listener_thread, daemon=True)
    thread.start()
    return thread


def restore_terminal():
    """Restore terminal to original settings."""
    global _terminal_old_settings
    if _terminal_old_settings is not None:
        try:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _terminal_old_settings)
        except Exception:
            pass


FPS = DEFAULT_FPS
NUM_EPISODES = DEFAULT_NUM_EPISODES
EPISODE_TIME_SEC = DEFAULT_EPISODE_TIME_S
RESET_TIME_SEC = DEFAULT_RESET_TIME_S
TASK_DESCRIPTION = DEFAULT_TASK_DESCRIPTION

JOINT_VELOCITY_SCALING = DEFAULT_JOINT_VELOCITY_SCALING

CAMERA_WIDTH = DEFAULT_CAMERA_WIDTH
CAMERA_HEIGHT = DEFAULT_CAMERA_HEIGHT
CAMERA_FOURCC = DEFAULT_CAMERA_FOURCC


def _parse_bool(v: object) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return True
    s = str(v).strip().lower()
    if s in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected a boolean, got: {v!r}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Record a LeRobot dataset using TRLC DK1 configs.")

    # Keep the same override flag names used by lerobot-record / notes_dp.md
    ap.add_argument(
        "--robot.joint_velocity_scaling",
        dest="robot_joint_velocity_scaling",
        type=float,
        default=JOINT_VELOCITY_SCALING,
    )

    ap.add_argument("--dataset.repo_id", dest="dataset_repo_id", default=None)
    ap.add_argument(
        "--dataset.push_to_hub",
        dest="dataset_push_to_hub",
        type=_parse_bool,
        nargs="?",
        const=True,
        default=DEFAULT_PUSH_TO_HUB,
    )
    ap.add_argument(
        "--dataset.num_episodes",
        dest="dataset_num_episodes",
        type=int,
        default=NUM_EPISODES,
    )
    ap.add_argument(
        "--dataset.episode_time_s",
        dest="dataset_episode_time_s",
        type=float,
        default=EPISODE_TIME_SEC,
    )
    ap.add_argument(
        "--dataset.reset_time_s",
        dest="dataset_reset_time_s",
        type=float,
        default=RESET_TIME_SEC,
    )
    ap.add_argument(
        "--dataset.single_task",
        dest="dataset_single_task",
        default=TASK_DESCRIPTION,
    )
    ap.add_argument(
        "--resume",
        dest="resume",
        type=_parse_bool,
        nargs="?",
        const=True,
        default=DEFAULT_RESUME,
        help="Resume recording into an existing local dataset cache for --dataset.repo_id.",
    )
    ap.add_argument(
        "--display",
        dest="display_data",
        type=_parse_bool,
        nargs="?",
        const=True,
        default=DEFAULT_DISPLAY_DATA,
        help="Display data visualization during recording (rerun).",
    )
    return ap.parse_args()


def make_robot_and_teleop(*, joint_velocity_scaling: float):
    cameras = {
        "context": OpenCVCameraConfig(
            index_or_path=CAMERA_CONTEXT_INDEX,
            width=CAMERA_WIDTH,
            height=CAMERA_HEIGHT,
            fps=FPS,
            fourcc=CAMERA_FOURCC,
        ),
        "right_wrist": OpenCVCameraConfig(
            index_or_path=CAMERA_RIGHT_INDEX,
            width=CAMERA_WIDTH,
            height=CAMERA_HEIGHT,
            fps=FPS,
            fourcc=CAMERA_FOURCC,
        ),
        "left_wrist": OpenCVCameraConfig(
            index_or_path=CAMERA_LEFT_INDEX,
            width=CAMERA_WIDTH,
            height=CAMERA_HEIGHT,
            fps=FPS,
            fourcc=CAMERA_FOURCC,
        ),
    }

    robot = BiDK1Follower(
        BiDK1FollowerConfig(
            left_arm_port=FOLLOWER_LEFT,
            right_arm_port=FOLLOWER_RIGHT,
            joint_velocity_scaling=joint_velocity_scaling,
            cameras=cameras,
        )
    )
    teleop = BiDK1Leader(
        BiDK1LeaderConfig(
            left_arm_port=LEADER_LEFT,
            right_arm_port=LEADER_RIGHT,
        )
    )
    return robot, teleop


def main():
    args = parse_args()
    print("Starting record dataset...")

    print("Creating robot and teleop...")
    robot, teleop = make_robot_and_teleop(joint_velocity_scaling=args.robot_joint_velocity_scaling)
    # Connect teleop (leader) FIRST - same order as lerobot-teleoperate
    print("Connecting teleop (leader arms)...")
    teleop.connect()
    print("Teleop connected. Connecting robot (follower + cameras)...")
    robot.connect()
    print("Robot connected.")

    # Configure the dataset features (must be after connect for camera features)
    action_features = hw_to_dataset_features(robot.action_features, "action")
    obs_features = hw_to_dataset_features(robot.observation_features, "observation")
    dataset_features = {**action_features, **obs_features}

    # Create the dataset
    user = os.environ.get("HF_USERNAME") or os.environ.get("USER") or "user"
    repo_id = args.dataset_repo_id
    if repo_id is None:
        if DEFAULT_DATASET_NAME:
            repo_id = f"{user}/{DEFAULT_DATASET_NAME}"
        else:
            repo_id = f"{user}/dk1-dataset-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    if args.resume:
        dataset = LeRobotDataset(repo_id)
        if hasattr(robot, "cameras") and getattr(robot, "cameras", None):
            dataset.start_image_writer(num_threads=4)
    else:
        dataset = LeRobotDataset.create(
            repo_id=repo_id,
            fps=FPS,
            features=dataset_features,
            robot_type=robot.name,
            use_videos=True,
            image_writer_threads=4,
        )

    # Standard Processors. Take the to_transitation and to output functions and apply
    # a noop step in between
    teleop_action_processor, robot_action_processor, robot_observation_processor = (
        make_default_processors()
    )

    # Initialize keyboard listener (terminal-based, works over SSH)
    events = {"exit_early": False, "rerecord_episode": False, "stop_recording": False}
    kb_thread = start_terminal_keyboard_listener(events)
    if kb_thread:
        print("Keyboard controls (during recording):")
        print("  Esc / ←  = Discard & re-record this episode")
        print("  →        = Exit episode early (keep it)")
        print("  Ctrl+C   = Stop recording (saves current episode)")
    else:
        print("No TTY - keyboard controls disabled. Use Ctrl+C to stop.")
    
    # Set up Ctrl+C handler for graceful stop (force exit on 3rd press)
    ctrl_c_count = [0]  # Use list to allow modification in nested function
    def signal_handler(sig, frame):
        ctrl_c_count[0] += 1
        if ctrl_c_count[0] >= 3:
            print("\n[Ctrl+C x3] Force exit!")
            restore_terminal()
            sys.exit(1)
        print(f"\n[Ctrl+C] Stopping... (press {3 - ctrl_c_count[0]} more times to force quit)")
        events["stop_recording"] = True
        events["exit_early"] = True
    signal.signal(signal.SIGINT, signal_handler)
    
    if args.display_data:
        init_rerun(session_name="recording")

    episode_idx = 0
    while episode_idx < args.dataset_num_episodes and not events["stop_recording"]:
        try:
            print(f"\n{'='*60}")
            print(f"Recording episode {episode_idx + 1} of {args.dataset_num_episodes} ({args.dataset_episode_time_s}s)...")
            print(f"{'='*60}")
            log_say(f"Recording episode {episode_idx + 1} of {args.dataset_num_episodes}")

            episode_start = time.perf_counter()
            record_loop(
                robot=robot,
                events=events,
                fps=FPS,
                teleop_action_processor=teleop_action_processor,
                robot_action_processor=robot_action_processor,
                robot_observation_processor=robot_observation_processor,
                teleop=teleop,
                dataset=dataset,
                control_time_s=args.dataset_episode_time_s,
                single_task=args.dataset_single_task,
                display_data=args.display_data,
            )
            episode_duration = time.perf_counter() - episode_start
            print()  # Newline after progress bar
            print(f"Episode {episode_idx + 1} recorded in {episode_duration:.2f}s")

            # Reset the environment if not stopping or re-recording
            if not events["stop_recording"] and (
                episode_idx < args.dataset_num_episodes - 1 or events["rerecord_episode"]
            ):
                log_say("Reset the environment")
                print("Reset the environment")
                start = time.perf_counter()
                record_loop(
                    robot=robot,
                    events=events,
                    fps=FPS,
                    teleop_action_processor=teleop_action_processor,
                    robot_action_processor=robot_action_processor,
                    robot_observation_processor=robot_observation_processor,
                    teleop=teleop,
                    control_time_s=args.dataset_reset_time_s,
                    single_task=args.dataset_single_task,
                    display_data=args.display_data,
                )
                print()  # Newline after progress bar
                end = time.perf_counter()
                print(f"Reset took {end - start:.1f}s")

            if events["rerecord_episode"]:
                log_say("Re-recording episode")
                events["rerecord_episode"] = False
                events["exit_early"] = False
                dataset.clear_episode_buffer()
                continue

            # Clear keyboard events before save - keypresses during save should be ignored
            events["exit_early"] = False
            events["rerecord_episode"] = False
            
            print("Saving episode (keypresses ignored during save)...")
            start = time.perf_counter()
            dataset.save_episode()
            end = time.perf_counter()
            print(f"Save episode took {end - start:.3f} seconds")
            episode_idx += 1

            if episode_idx >= args.dataset_num_episodes:
                break
        except Exception as e:
            print(f"Error recording episode {episode_idx}: {e}")
            traceback.print_exc()
            log_say(
                f"Error recording episode {episode_idx}. Saving dataset and exiting..."
            )
            break

    # Stop keyboard listener and restore terminal
    events["_listener_stop"] = True
    restore_terminal()
    
    # Finalize dataset
    print("Finalizing dataset...")
    dataset.finalize()
    print("Dataset finalized.")
    
    if args.dataset_push_to_hub:
        dataset.push_to_hub()

    print("Disconnecting...")
    robot.disconnect()
    teleop.disconnect()
    print("Done.")


if __name__ == "__main__":
    main()