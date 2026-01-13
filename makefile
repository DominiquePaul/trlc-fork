.PHONY: py camera checkcamera biteleop watchports debugcamera
.ONESHELL:

# Always run Python inside Poetry's virtualenv.
# Override if needed: `make PY="python" ...`
PY ?= poetry run python

py:
	@echo "PY=$(PY)"
	$(PY) -c "import sys; print(sys.executable)"
	$(PY) --version
	
checkcamera:
	$(PY) examples/find_cameras.py --capture-images --output-dir ./camera_images

watchports:
	$(PY) examples/watch_ports.py --include '/dev/ttyACM*' --include '/dev/ttyUSB*' --verbose

mapports:
	$(PY) examples/map_ports_guided.py

debugcamera:
	$(PY) examples/debug_lerobot_camera.py --indices "4,0,2" --width 640 --height 480 --fps 30 --backend v4l2 --v4l2-info


biteleop:
	$(PY) - <<'PY'
	import sys
	import subprocess
	
	sys.path.insert(0, "src")
	import trlc_dk1.config as cfg
	
	cameras = (
	    "{ "
	    f"right_wrist: {{type: opencv, index_or_path: {cfg.CAMERA_RIGHT_INDEX}, width: 640, height: 480, fps: 30}}, "
	    f"left_wrist: {{type: opencv, index_or_path: {cfg.CAMERA_LEFT_INDEX}, width: 640, height: 480, fps: 30}}, "
	    "}"
	)
	
	cmd = [
	    "lerobot-teleoperate",
	    "--robot.type=bi_dk1_follower",
	    "--teleop.type=bi_dk1_leader",
	    f"--teleop.left_arm_port={cfg.LEADER_LEFT}",
	    f"--robot.left_arm_port={cfg.FOLLOWER_LEFT}",
	    f"--teleop.right_arm_port={cfg.LEADER_RIGHT}",
	    f"--robot.right_arm_port={cfg.FOLLOWER_RIGHT}",
	    "--robot.joint_velocity_scaling=1.0",
	    f"--robot.cameras={cameras}",
	]
	
	print("Running:", " ".join(cmd))
	subprocess.run(cmd, check=True)
	PY


# --display_data=true \
# --display_url=127.0.0.1 \
# --display_port=9876''')
# f"context: {{type: opencv, index_or_path: {cfg.CAMERA_CONTEXT_INDEX}, width: 640, height: 480, fps: 30}} "