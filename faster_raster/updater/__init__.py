"""Read-only release discovery and update recommendations.

The updater deliberately has no apply operation.  It reports installation state,
validates bounded release metadata, and returns a human-reviewable recommendation.
"""

from .models import UpdateCheckResult, UpdateChannel

__all__ = ["UpdateCheckResult", "UpdateChannel"]
