from trlc_dk1.follower import DK1Follower, DK1FollowerConfig
from trlc_dk1.leader import DK1Leader, DK1LeaderConfig
import time
from trlc_dk1.config import FOLLOWER_LEFT, FOLLOWER_RIGHT, LEADER_LEFT, LEADER_RIGHT


print(f"FOLLOWER_LEFT: {FOLLOWER_LEFT}\nFOLLOWER_RIGHT: {FOLLOWER_RIGHT}\nLEADER_LEFT: {LEADER_LEFT}\nLEADER_RIGHT: {LEADER_RIGHT}")


# Leader_port = LEADER_RIGHT
# Follower_port = FOLLOWER_RIGHT

Leader_port = LEADER_LEFT
Follower_port = FOLLOWER_LEFT

follower_config = DK1FollowerConfig(
    port=Follower_port,
    joint_velocity_scaling=1.0,
)

leader_config = DK1LeaderConfig(
    port=Leader_port,
)

leader = DK1Leader(leader_config)
leader.connect()

follower = DK1Follower(follower_config)
follower.connect()

freq = 200 # Hz

try:
    while True:
        action = leader.get_action()
        follower.send_action(action)    
        time.sleep(1/freq)
except KeyboardInterrupt:
    print("\nStopping teleop...")
    leader.disconnect()
    follower.disconnect()
