from trlc_dk1.bi_follower import BiDK1Follower, BiDK1FollowerConfig
from trlc_dk1.bi_leader import BiDK1Leader, BiDK1LeaderConfig
from trlc_dk1.config import FOLLOWER_LEFT, FOLLOWER_RIGHT, LEADER_LEFT, LEADER_RIGHT
import time
import logging

from lerobot.utils.utils import init_logging


print(f"FOLLOWER_LEFT: {FOLLOWER_LEFT}\nFOLLOWER_RIGHT: {FOLLOWER_RIGHT}\nLEADER_LEFT: {LEADER_LEFT}\nLEADER_RIGHT: {LEADER_RIGHT}")

VELOCITY_SCALING = 0.7

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

init_logging()

follower_config = BiDK1FollowerConfig(
    left_arm_port=FOLLOWER_LEFT,
    right_arm_port=FOLLOWER_RIGHT,
    joint_velocity_scaling=VELOCITY_SCALING,
)

leader_config = BiDK1LeaderConfig( 
    left_arm_port=LEADER_LEFT,
    right_arm_port=LEADER_RIGHT,
)

leader = BiDK1Leader(leader_config)
leader.connect()

follower = BiDK1Follower(follower_config)
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
