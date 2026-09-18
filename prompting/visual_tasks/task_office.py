from pathlib import Path

from .base import VisualDualArmTask


class OfficeTask(VisualDualArmTask):
    """Editable long-form task definition selected by the short ID `office`."""

    task_id = "office"
    task_name = "Dual-arm office desk organization"
    task_description = "Complete the office task from instruction.txt"
    supports_task_done = True
    atomic_action_names = (
        "pick",
        "place",
        "flap_close",
        "adjust",
        "handover",
    )
    default_image_paths = ("env_office_p4.png",)

    def __init__(self):
        instruction_path = Path(__file__).resolve().parents[2] / "instruction.txt"
        self.task_instruction = instruction_path.read_text(encoding="utf-8").strip()
        if not self.task_instruction:
            raise ValueError("instruction.txt must not be empty")

    task_rules = """
- LeftArm and RightArm are the two arms of the same robot.
- Each EXECUTE response must contain one action for each arm.
- Use WAIT when an arm has no required action in the current planning step.
- The image is the initial observation; no action is physically executed.
- Output TASK_DONE only after the accumulated plan covers all items in the task instruction.
"""
