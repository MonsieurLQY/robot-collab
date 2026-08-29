import re
from typing import Iterable, List, Tuple

from .visual_plan import VisualJointAction


ACTION_LINE = re.compile(
    r"^\s*NAME\s+(?P<agent>\S+)\s+ACTION\s+(?P<action>.+?)\s*$"
)


class VisualPlanParser:
    """Parse the original EXECUTE/NAME/ACTION dialog response format."""

    def __init__(self, agent_names: Iterable[str]) -> None:
        self.agent_names = tuple(agent_names)
        if not self.agent_names:
            raise ValueError("VisualPlanParser requires at least one agent")

    def parse(
        self,
        _observation,
        response: str,
    ) -> Tuple[bool, str, List[VisualJointAction]]:
        if any(line.strip() == "TASK_DONE" for line in response.splitlines()):
            done = VisualJointAction(
                parsed_proposal="TASK_DONE",
                task_done=True,
            )
            return True, "TASK_DONE", [done]

        if "EXECUTE" not in response:
            return False, "Response does not contain EXECUTE.", []

        execute_text = response.rsplit("EXECUTE", 1)[1]
        parsed_actions = {}
        parsed_lines = []
        for line in execute_text.splitlines():
            match = ACTION_LINE.match(line)
            if match is None:
                continue
            agent_name = match.group("agent")
            action = match.group("action").strip()
            if agent_name not in self.agent_names:
                return False, f"Unknown agent name: {agent_name}.", []
            if agent_name in parsed_actions:
                return False, f"Duplicate action for agent {agent_name}.", []
            if not action:
                return False, f"Action for {agent_name} is empty.", []
            parsed_actions[agent_name] = action
            parsed_lines.append(f"NAME {agent_name} ACTION {action}")

        missing_agents = [
            agent_name
            for agent_name in self.agent_names
            if agent_name not in parsed_actions
        ]
        if missing_agents:
            missing = ", ".join(missing_agents)
            return False, f"Response missing action for: {missing}.", []
        if len(parsed_actions) != len(self.agent_names):
            return False, "Response must contain exactly one action per arm.", []

        ordered_actions = {
            agent_name: parsed_actions[agent_name]
            for agent_name in self.agent_names
        }
        parsed_proposal = "EXECUTE\n" + "\n".join(parsed_lines)
        plan = VisualJointAction(
            action_strs=ordered_actions,
            parsed_proposal=parsed_proposal,
        )
        return True, plan.get_action_desp(), [plan]
