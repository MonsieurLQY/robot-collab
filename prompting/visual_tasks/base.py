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
    default_image_paths: Sequence[str] = ()
    robot_name_map: Dict[str, str] = {
        "left_arm": "LeftArm",
        "right_arm": "RightArm",
    }

    def get_action_prompt(self) -> str:
        return """
[Action Options]
The atomic action library is intentionally unrestricted in this version.
Choose one concise atomic action for each arm. Use WAIT when an arm does not
need to act in the current planning round. Do not combine several physical
operations into one action.

[Action Output Instruction]
The two agents may discuss first. End a discussion message with PROCEED when
more discussion is needed. Once both agents agree on the next joint planning
step, output exactly:

EXECUTE
NAME LeftArm ACTION <one concise action>
NAME RightArm ACTION <one concise action>

When the accumulated planned actions completely cover the task, output only:

TASK_DONE
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
        image_list = "\n".join(
            f"- {path}" for path in observation.image_paths
        )
        return f"""
[Task]
{self.task_instruction.strip()}

[Task Rules]
{self.task_rules.strip()}

[Robot Configuration]
You are {agent_name}, the planning agent for the {arm_side} arm of one shared
dual-arm robot. {other_agent} controls the other arm of the same robot. The two
agents must produce one coordinated plan, not two unrelated plans.

[Visual Observation]
The attached image or images are the robot's first-person observation of the
initial environment:
{image_list}
Use visible evidence from the images. The actions in [History] are accepted
planning steps, not physically executed actions. Continue from their expected
effects when choosing the next planning step.

[Current Round]
Reason about the next atomic action for your arm and inspect {other_agent}'s
proposal for task-order or coordination mistakes. Respond concisely. Propose
exactly one current action for yourself. Use PROCEED while discussion is still
needed, EXECUTE to summarize the agreed next joint action, or TASK_DONE after
the accumulated plan fully covers the task.
""".strip()
