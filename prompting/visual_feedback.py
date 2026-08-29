from typing import Tuple

from .visual_plan import VisualJointAction


class VisualFeedbackManager:
    """Compatibility feedback hook for text-only visual planning."""

    def give_feedback(
        self,
        _plan: VisualJointAction,
    ) -> Tuple[bool, str]:
        # Format validation is handled by VisualPlanParser. This intentionally
        # performs no simulation, action-library, or repetition checks.
        return True, ""
