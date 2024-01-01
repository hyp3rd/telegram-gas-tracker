"""Application Enums"""

from enum import Enum


class GasTrackerState(Enum):
    """Gas Emoji Enum"""

    GREEN = "🟢"
    YELLOW = "🟡"
    RED = "🔴"


class AwaitInterval(Enum):
    """Awaiting Enum"""

    TRACKING = 1
    THRESHOLDS = 2
    WALLET_ADDRESS = 3
    WALLET_UNTRACKED = 4
