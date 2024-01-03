"""Application Enums"""

from enum import Enum


class Env(Enum):
    """Environment Enum"""

    DOCKER = "DOCKER"
    LOCAL = "LOCAL"


class TrackerSemaphore(Enum):
    """Semaphore Emoji Enum"""

    GREEN = "🟢"
    YELLOW = "🟡"
    RED = "🔴"


class TrackerState(Enum):
    """Awaiting Enum"""

    TRACKING = 1
    THRESHOLDS = 2
    WALLET_ADDRESS = 3
    WALLET_UNTRACKED = 4
