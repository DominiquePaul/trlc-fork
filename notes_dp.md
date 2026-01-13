

# Additions Dominique

Check which cameras are on which index: `make checkcamera`
Run an improved version of `lerobot-find-port` which runs continuously and doesn't require pressing enter: `make watchports`
Guided mapping for leader/follower left/right ports (prints temp + stable `/dev/serial/by-id` and `/dev/serial/by-path`): `make mapports`
Teleop both arms (uses config.py):  `make biteleop`

## Keep USB ports fixed (Jetson / Linux)

On Linux, `/dev/ttyACM0`, `/dev/ttyUSB0`, `/dev/video0`, etc. are **not stable**: the kernel assigns those numbers in the order devices enumerate, which can change across reboots/unplug/replug.

Use one of these instead:

- **Serial devices (recommended)**: `/dev/serial/by-id/...` (stable per device) or `/dev/serial/by-path/...` (stable per physical USB port path)

```bash
ls -l /dev/serial/by-id/
ls -l /dev/serial/by-path/
```

Then pass those paths directly:

```bash
lerobot-teleoperate \
  --teleop.left_arm_port=/dev/serial/by-id/usb-... \
  --robot.left_arm_port=/dev/serial/by-id/usb-... \
  --teleop.right_arm_port=/dev/serial/by-id/usb-... \
  --robot.right_arm_port=/dev/serial/by-id/usb-...
```

- **Cameras**: `/dev/v4l/by-id/...` (stable per camera) or `/dev/v4l/by-path/...` (stable per physical port)

```bash
ls -l /dev/v4l/by-id/
ls -l /dev/v4l/by-path/
```

You can use these in OpenCV-based configs by setting `index_or_path` to the device path (string), not just an integer index.

### Optional: create your own stable names via udev rules

If you want friendly names like `/dev/robot_left_arm`, create a udev rule:

1) Pick the device and inspect attributes:

```bash
udevadm info -n /dev/ttyACM0 --query=property
udevadm info -a -n /dev/ttyACM0 | head -n 80
```

2) Create `/etc/udev/rules.d/99-robot-ports.rules` (edit values to match your device):

```bash
sudo tee /etc/udev/rules.d/99-robot-ports.rules >/dev/null <<'RULES'
# Example for a specific USB serial device (match by VID/PID + serial if available)
SUBSYSTEM=="tty", KERNEL=="ttyACM*", ATTRS{idVendor}=="0000", ATTRS{idProduct}=="0000", ATTRS{serial}=="YOUR_SERIAL", SYMLINK+="robot_left_arm"
RULES
```

3) Reload rules:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

After that, you can use `/dev/robot_left_arm` in your scripts.

Collect data

```bash
lerobot-record \
    --robot.type=bi_dk1_follower \
    --teleop.type=bi_dk1_leader \
    --robot.joint_velocity_scaling=0.5 \
    --dataset.repo_id=$USER/dk1-training-run7 \
    --dataset.single_task="Pick up marker" \
    --dataset.push_to_hub=false \
    --display_data=true \
    --display_url=172.20.10.7 \
    --teleop.left_arm_port=/dev/ttyACM1 \
    --robot.left_arm_port=/dev/ttyACM0 \
    --teleop.right_arm_port=/dev/ttyACM3 \
    --robot.right_arm_port=/dev/ttyACM2
```
lerobot-teleoperate \
    --robot.type=bi_dk1_follower \
    --teleop.type=bi_dk1_leader \
    --teleop.left_arm_port=/dev/ttyACM3 \
    --robot.left_arm_port=/dev/ttyACM1 \
    --teleop.right_arm_port=/dev/ttyACM2 \
    --robot.right_arm_port=/dev/ttyACM0 \
    --robot.joint_velocity_scaling=1.0 \
    --robot.cameras="{ 
        context: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30},
        right_wrist: {type: opencv, index_or_path: 2, width: 640, height: 360, fps: 30},
        left_wrist: {type: opencv, index_or_path: 4, width: 640, height: 360, fps: 30},
        }"  \
    --display_data=true \
    --display_url=127.0.0.1 \
    --display_port=9876


lerobot-teleoperate \
    --robot.type=bi_dk1_follower \
    --teleop.type=bi_dk1_leader \
    --teleop.left_arm_port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AB0181138-if00 \
    --robot.left_arm_port=/dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.3:1.0 \
    --teleop.right_arm_port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5A46081965-if00 \
    --robot.right_arm_port=/dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.1:1.0 \
    --robot.joint_velocity_scaling=1.0 \
    --robot.cameras="{ 
        right_wrist: {type: opencv, index_or_path: 2, width: 640, height: 360, fps: 30},
        left_wrist: {type: opencv, index_or_path: 4, width: 640, height: 360, fps: 30},
        }"  \
    --display_data=true \
    --display_url=127.0.0.1 \
    --display_port=9876

### If 2 cameras work but 3 cameras don't

This is usually **USB bandwidth / hub scheduling**. A common pitfall on Jetson is that a “directly plugged” camera still enumerates on the **USB2 (480M)** root hub (or the hub/cable is USB2), so adding a third UVC stream causes the “third camera” to drop frames.

Quick checks:

```bash
# Shows which devices are on USB2 (480M) vs USB3 (5000M+)
lsusb -t

# For each camera node, shows bus path like usb-... (helps correlate with lsusb -t)
v4l2-ctl --device /dev/video0 --all | grep -E "Card type|Bus info"
v4l2-ctl --device /dev/video2 --all | grep -E "Card type|Bus info"
v4l2-ctl --device /dev/video4 --all | grep -E "Card type|Bus info"
```

Mitigations (pick one, in order of effectiveness):

- Put at least one camera on a **USB3 root hub** (it should show up as `5000M` or higher in `lsusb -t`).
- Use a **powered USB3 hub** and avoid daisy-chaining hubs.
- Reduce camera bandwidth: prefer **MJPG** + lower FPS / resolution.

Example “low bandwidth” 3-camera config (good starting point on USB2-heavy setups):

### Teleoperate

```bash
lerobot-teleoperate \
    --robot.type=bi_dk1_follower \
    --teleop.type=bi_dk1_leader \
    --teleop.left_arm_port=/dev/ttyACM3 \
    --robot.left_arm_port=/dev/ttyACM1 \
    --teleop.right_arm_port=/dev/ttyACM2 \
    --robot.right_arm_port=/dev/ttyACM0 \
    --robot.joint_velocity_scaling=1.0 \
    --robot.cameras="{ 
        right_wrist: {type: opencv, index_or_path: 2, width: 640, height: 360, fps: 30, fourcc: MJPG},
        left_wrist: {type: opencv, index_or_path: 4, width: 640, height: 360, fps: 30, fourcc: MJPG},
        }"
```

```bash
lerobot-teleoperate \
    --robot.type=bi_dk1_follower \
    --teleop.type=bi_dk1_leader \
    --teleop.left_arm_port=/dev/ttyACM1 \
    --robot.left_arm_port=/dev/ttyACM3 \
    --teleop.right_arm_port=/dev/ttyACM0 \
    --robot.right_arm_port=/dev/ttyACM2 \
    --robot.joint_velocity_scaling=1.0 \
    --robot.cameras="{ 
        right_wrist: {type: opencv, index_or_path: 2, width: 640, height: 360, fps: 30},
        left_wrist: {type: opencv, index_or_path: 4, width: 640, height: 360, fps: 30},
        context: {type: opencv, index_or_path: 0, width: 640, height: 480, fps: 30},
        }"
```


### Collect data

```bash
lerobot-record \
    --robot.type=bi_dk1_follower \
    --teleop.type=bi_dk1_leader \
    --teleop.left_arm_port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5AB0181138-if00 \
    --robot.left_arm_port=/dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.3:1.0 \
    --teleop.right_arm_port=/dev/serial/by-id/usb-1a86_USB_Single_Serial_5A46081965-if00 \
    --robot.right_arm_port=/dev/serial/by-path/platform-a80aa10000.usb-usb-0:4.2.1.1:1.0 \
    --robot.joint_velocity_scaling=1.0 \
    --robot.cameras="{ 
        right_wrist: {type: opencv, index_or_path: 0, width: 640, height: 360, fps: 30, fourcc: MJPG},
        left_wrist: {type: opencv, index_or_path: 2, width: 640, height: 360, fps: 30, fourcc: MJPG},
        }" \
    --dataset.repo_id=$USER/pcb_dummy_v1 \
    --dataset.push_to_hub=true \
    --dataset.num_episodes=10 \
    --dataset.episode_time_s=60 \
    --dataset.reset_time_s=0 \
    --dataset.single_task="Place the PCB from left bin into testing device, close the lid, then open lid, and place the PCB in right bin" \
    --resume=true
```



### Create HF data repo

hf repo create dopaul/pcb_dummy_v1 --repo-type dataset --private
hf upload-large-folder dopaul/pcb_dummy_v1 /home/dominique/.cache/huggingface/lerobot/dominique/pcb_dummy_v1 --repo-type dataset