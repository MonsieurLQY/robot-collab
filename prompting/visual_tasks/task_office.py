from .base import VisualDualArmTask


class OfficeTask(VisualDualArmTask):
    """Editable long-form task definition selected by the short ID `office`."""

    task_id = "office"
    task_name = "Dual-arm office desk organization"
    task_description = "Complete the ordered eight-step office desk task"
    default_image_paths = ("rocobench/envs/env_office_p2.jpg",)

    task_instruction = """
Use the dual-arm robot to complete the following ordered office task:

1. Pick up the marker from the desktop.
2. Put the marker into the pen holder.
3. Close the laptop.
4. Pick up the trash from the desktop.
5. Put the trash into the trash bin.
6. Pick up the mouse from the desktop.
7. Put the mouse on the laptop.
8. Adjust the position of the cup on the desktop.
"""

    task_rules = """
- LeftArm and RightArm are the two arms of the same robot.
- Each EXECUTE response must contain one action for each arm.
- Use WAIT when an arm has no required action in the current planning step.
- The image is the initial observation; no action is physically executed.
- Output TASK_DONE only after the accumulated plan covers all eight items.
"""
