"""LawnOps - Lawn care operations CLI.

Weather monitoring, irrigation control, treatment tracking, and cost management.
"""

__version__ = "1.0.0"

from lawnops.advisory import pre_emergent_advisory, spray_advisory
from lawnops.config import load_config
from lawnops.recommend import get_recommendation
from lawnops.weather import aggregate_daily, fetch_data, get_current

__all__ = [
    "load_config",
    "fetch_data",
    "aggregate_daily",
    "get_current",
    "pre_emergent_advisory",
    "spray_advisory",
    "get_recommendation",
]
