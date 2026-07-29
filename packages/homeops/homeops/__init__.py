"""HomeOps: Home maintenance operations CLI.

Recurring tasks, pest control, service providers, and cost tracking.
"""

__version__ = "0.1.0"

from homeops.config import load_config

__all__ = [
    "load_config",
]
