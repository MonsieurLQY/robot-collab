"""Prompting package with lazy imports for simulation-only dependencies."""

__all__ = [
    "DialogPrompter",
    "FeedbackManager",
    "LLMResponseParser",
    "SingleThreadPrompter",
    "save_episode_html",
]


def __getattr__(name):
    if name == "LLMResponseParser":
        from prompting.parser import LLMResponseParser

        return LLMResponseParser
    if name == "FeedbackManager":
        from prompting.feedback import FeedbackManager

        return FeedbackManager
    if name == "DialogPrompter":
        from prompting.dialog_prompter import DialogPrompter

        return DialogPrompter
    if name == "SingleThreadPrompter":
        from prompting.plan_prompter import SingleThreadPrompter

        return SingleThreadPrompter
    if name == "save_episode_html":
        from prompting.display_utils import save_episode_html

        return save_episode_html
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
