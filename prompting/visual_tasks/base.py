from abc import ABC
from typing import Dict, Sequence

from prompting.visual_plan import VisualObservation


class VisualDualArmTask(ABC):
    """Prompt-only task adapter used by the existing dialog prompter."""

    task_id = "base"
    task_name = "Base visual task"
    task_description = ""
    task_instruction = ""
    task_rules = ""
    supports_task_done = False
    default_image_paths: Sequence[str] = ()
    robot_name_map: Dict[str, str] = {
        "left_arm": "LeftArm",
        "right_arm": "RightArm",
    }

    def get_action_prompt(self) -> str:
        return """
[Action Options]
1) <atomic action>: The atomic action library is intentionally unrestricted in
this version. Choose one concise physical operation and do not combine several
operations into one action.
2) WAIT, do nothing.
Plan exactly one action for each arm at every round.

[Action Output Instruction]
Must first output 'EXECUTE\n', then give exactly one action per robot, put each
on a new line.
Example: 'EXECUTE\nNAME LeftArm ACTION <one concise action>\nNAME RightArm ACTION WAIT\n'
""".strip()

    def get_agent_prompt(
        self,
        observation: VisualObservation,
        agent_name: str,
    ) -> str:
        if agent_name not in self.robot_name_map.values():
            raise ValueError(f"Unknown visual planning agent: {agent_name}")
        other_agent = next(
            name
            for name in self.robot_name_map.values()
            if name != agent_name
        )
        arm_side = "left" if agent_name == "LeftArm" else "right"
        other_side = "right" if arm_side == "left" else "left"
        return f"""
You are a robot {agent_name}, collaborating with {other_agent} as the
{arm_side} and {other_side} arms of one shared dual-arm robot.
{self.task_instruction.strip()}
{self.task_rules.strip()}

At the current round:
You can observe the current environment from the attached first-person image.
The actions in [History] are accepted planning steps, not physically executed
actions. Continue planning from their expected effects.
Think step-by-step about the task and {other_agent}'s response. Carefully check
and correct them if they made a mistake.
Improve your plans if given [Environment Feedback].
Respond very concisely but informatively, and do not repeat what others have
said. Discuss with others to come up with the best plan.
Propose exactly one action for yourself at the **current** round, select from
[Action Options].
End your response by either: 1) output PROCEED, if the plans require further
discussion; 2) If everyone has made proposals and got approved, output the
final plan, must strictly follow [Action Output Instruction]!
""".strip()
