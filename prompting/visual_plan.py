from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class VisualObservation:
    """Initial visual observation shared by both planning agents."""

    image_paths: Tuple[Path, ...]


@dataclass
class VisualJointAction:
    """One accepted planning round for the two arms."""

    action_strs: Dict[str, str] = field(default_factory=dict)
    parsed_proposal: str = ""
    task_done: bool = False

    def get_action_desp(self) -> str:
        if self.task_done:
            return "TASK_DONE"
        return "\n".join(
            f"NAME {agent_name} ACTION {action}"
            for agent_name, action in self.action_strs.items()
        )

    def to_dict(self, step_id: int) -> Dict[str, object]:
        return {
            "step_id": step_id,
            "actions": dict(self.action_strs),
            "raw_plan": self.parsed_proposal,
        }


@dataclass
class VisualPlan:
    """Accumulated text-only plan produced across dialog rounds."""

    task_id: str
    task_name: str
    image_paths: List[str]
    steps: List[VisualJointAction] = field(default_factory=list)
    status: str = "planning"

    def to_dict(self) -> Dict[str, object]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "image_paths": self.image_paths,
            "status": self.status,
            "steps": [
                step.to_dict(step_id=index)
                for index, step in enumerate(self.steps, start=1)
            ],
        }

    def to_text(self) -> str:
        lines = [
            "TASK",
            f"{self.task_name} ({self.task_id})",
            "",
            "SOURCE IMAGES",
            *self.image_paths,
            "",
            "PLAN",
        ]
        for index, step in enumerate(self.steps, start=1):
            lines.extend(["", f"STEP {index}", step.get_action_desp()])
        if self.status == "completed":
            lines.extend(["", "TASK_DONE"])
        else:
            lines.extend(["", f"STATUS {self.status}"])
        return "\n".join(lines) + "\n"
