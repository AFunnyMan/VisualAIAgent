"""Compatibility imports for production person visibility helpers."""

from visual_ai_agent.behavior_visibility import *  # noqa: F403
from visual_ai_agent.behavior_visibility import _iou, _person_candidates  # noqa: F401

if __name__ == "__main__":
    main()  # noqa: F405
