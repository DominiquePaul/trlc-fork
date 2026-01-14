#   Copyright 2025 The Robot Learning Company UG (haftungsbeschränkt). All rights reserved.
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from dataclasses import dataclass, field
from functools import cached_property
import sys
import time
import logging
from typing import Any

from lerobot.cameras import CameraConfig
from lerobot.cameras.utils import make_cameras_from_configs
from lerobot.robots import Robot, RobotConfig

from trlc_dk1.motors.DM_Control_Python.DM_CAN import *
from trlc_dk1.follower import DK1Follower, DK1FollowerConfig
from trlc_dk1.logging_utils import configure_trlc_debug_logging

logger = logging.getLogger(__name__)



def map_range(x: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


@RobotConfig.register_subclass("bi_dk1_follower")
@dataclass
class BiDK1FollowerConfig(RobotConfig):
    left_arm_port: str
    right_arm_port: str
    debug: bool = False
    disable_torque_on_disconnect: bool = False
    joint_velocity_scaling: float = 0.2
    max_gripper_torque: float = 1.0 # Nm (/0.00875m spur gear radius = 114N gripper force)
    cameras: dict[str, CameraConfig] = field(default_factory=dict)


class BiDK1Follower(Robot):
    """
    Bimanual TRLC-DK1 Follower Arm designed by The Robot Learning Company.
    """

    config_class = BiDK1FollowerConfig
    name = "bi_dk1_follower"

    def __init__(self, config: BiDK1FollowerConfig):
        super().__init__(config)

        self.config = config
        
        left_arm_config = DK1FollowerConfig(
            port=self.config.left_arm_port,
            debug=self.config.debug,
            disable_torque_on_disconnect=self.config.disable_torque_on_disconnect,
            joint_velocity_scaling=self.config.joint_velocity_scaling,
            max_gripper_torque=self.config.max_gripper_torque,
        )
        right_arm_config = DK1FollowerConfig(
            port=self.config.right_arm_port,
            debug=self.config.debug,
            disable_torque_on_disconnect=self.config.disable_torque_on_disconnect,
            joint_velocity_scaling=self.config.joint_velocity_scaling,
            max_gripper_torque=self.config.max_gripper_torque,
        )
        
        self.left_arm = DK1Follower(left_arm_config)
        self.right_arm = DK1Follower(right_arm_config)
        self.cameras = make_cameras_from_configs(config.cameras)

    @property
    def _motors_ft(self) -> dict[str, type]:
        return {f"left_{motor}.pos": float for motor in self.left_arm.motors} | {
            f"right_{motor}.pos": float for motor in self.right_arm.motors
        }

    @property
    def _cameras_ft(self) -> dict[str, tuple]:
        return {
            cam: (self.config.cameras[cam].height, self.config.cameras[cam].width, 3) for cam in self.cameras
        }

    @cached_property
    def observation_features(self) -> dict[str, type | tuple]:
        return {**self._motors_ft, **self._cameras_ft}

    @cached_property
    def action_features(self) -> dict[str, type]:
        return self._motors_ft

    @property
    def is_connected(self) -> bool:
        return self.left_arm.is_connected and self.right_arm.is_connected and all(cam.is_connected for cam in self.cameras.values())

    def connect(self) -> None:
        configure_trlc_debug_logging(self.config.debug)
        print(f"[BiDK1Follower] Connecting {len(self.cameras)} cameras...")
        # OpenCV cameras can occasionally return a transient read failure during startup.
        # lerobot's OpenCVCamera.connect(warmup=True) will raise on the first failed read,
        # which makes teleop brittle. We connect with warmup disabled and do a tolerant
        # warmup here, with a few retries.
        # NOTE: We connect cameras *before* enabling motors/torque to avoid power/USB glitches
        # during motor bring-up that can make UVC cameras drop initial frames.
        camera_items = list(self.cameras.items())
        priority = {"context": 0, "right_wrist": 1, "left_wrist": 2}
        camera_items.sort(key=lambda kv: (priority.get(kv[0], 99), kv[0]))

        for cam_name, cam in camera_items:
            print(f"[BiDK1Follower] Connecting camera '{cam_name}'...")
            max_attempts = 5
            backoff_s = 0.25
            last_err: Exception | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    cam.connect(warmup=False)

                    # Tolerant warmup:
                    # - give the device a brief settle time after opening
                    # - accept transient read failures for a few seconds
                    # - require only the first good frame (subsequent reads will be in the main loop)
                    settle_s = 0.25
                    time.sleep(settle_s)

                    warmup_s = float(getattr(cam, "warmup_s", 1.0))
                    # Context camera is usually the most fragile under load; give it more time.
                    extra_s = 12.0 if cam_name == "context" else 0.0
                    warmup_deadline = time.time() + max(warmup_s, 3.0) + extra_s
                    ok_frames = 0
                    failures = 0
                    last_read_err: Exception | None = None

                    while time.time() < warmup_deadline and ok_frames < 1:
                        try:
                            cam.read()
                            ok_frames += 1
                        except Exception:
                            failures += 1
                            last_read_err = sys.exc_info()[1]  # type: ignore[assignment]
                            time.sleep(0.10)

                    if ok_frames < 1:
                        raise RuntimeError(
                            f"{self} camera '{cam_name}' failed to warm up "
                            f"(no frames, failures={failures}, last_err={last_read_err})."
                        )

                    last_err = None
                    break

                except Exception as e:
                    last_err = e
                    try:
                        if getattr(cam, "is_connected", False):
                            cam.disconnect()
                    except Exception:
                        pass

                    if attempt < max_attempts:
                        logger.warning(
                            f"{self} camera '{cam_name}' connect attempt {attempt}/{max_attempts} failed: {e}. "
                            f"Retrying in {backoff_s:.2f}s..."
                        )
                        time.sleep(backoff_s)
                        backoff_s = min(2.0, backoff_s * 2.0)

            if last_err is not None:
                raise RuntimeError(f"{self} camera '{cam_name}' failed to connect after {max_attempts} attempts.") from last_err
            print(f"[BiDK1Follower] Camera '{cam_name}' connected!")

        print("[BiDK1Follower] All cameras connected. Connecting LEFT arm...")
        self.left_arm.connect()
        print("[BiDK1Follower] LEFT arm connected. Connecting RIGHT arm...")
        self.right_arm.connect()
        print("[BiDK1Follower] All connections established!")

    @property
    def is_calibrated(self) -> bool:
        return True

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        self.left_arm.configure()
        self.right_arm.configure()

    def get_observation(self) -> dict[str, Any]:
        obs_dict = {}
        
        left_obs = self.left_arm.get_observation()
        obs_dict.update({f"left_{key}": value for key, value in left_obs.items()})

        right_obs = self.right_arm.get_observation()
        obs_dict.update({f"right_{key}": value for key, value in right_obs.items()})

        for cam_key, cam in self.cameras.items():
            start = time.perf_counter()
            obs_dict[cam_key] = cam.async_read()
            dt_ms = (time.perf_counter() - start) * 1e3
            logger.debug(f"{self} read {cam_key}: {dt_ms:.1f}ms")

        return obs_dict

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        left_action = {
            key.removeprefix("left_"): value for key, value in action.items() if key.startswith("left_")
        }
        right_action = {
            key.removeprefix("right_"): value for key, value in action.items() if key.startswith("right_")
        }

        send_action_left = self.left_arm.send_action(left_action)
        send_action_right = self.right_arm.send_action(right_action)

        prefixed_send_action_left = {f"left_{key}": value for key, value in send_action_left.items()}
        prefixed_send_action_right = {f"right_{key}": value for key, value in send_action_right.items()}

        return {**prefixed_send_action_left, **prefixed_send_action_right}

    def disconnect(self):
        self.left_arm.disconnect()
        self.right_arm.disconnect()

        for cam in self.cameras.values():
            cam.disconnect()
