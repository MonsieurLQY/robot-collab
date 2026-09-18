"""Prompting interfaces, loaded lazily to keep backend dependencies separate."""
from importlib import import_module

_EXPORTS = {
    "LLMResponseParser": "parser", "FeedbackManager": "feedback",
    "DialogPrompter": "dialog_prompter", "SingleThreadPrompter": "plan_prompter",
    "save_episode_html": "display_utils",
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module("." + _EXPORTS[name], __name__), name)
    globals()[name] = value
    return value
