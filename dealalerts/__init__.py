"""Deal Alerts: spot price errors and very deep discounts from public deal feeds."""

from dealalerts.config import Config, Settings, load_config
from dealalerts.models import REGION_LABELS, REGIONS, Deal, RawPost, Source, Verdict

__all__ = [
    "Config",
    "Deal",
    "RawPost",
    "REGION_LABELS",
    "REGIONS",
    "Settings",
    "Source",
    "Verdict",
    "load_config",
]
