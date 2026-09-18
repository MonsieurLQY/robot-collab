from typing import Dict, Type

from .base import VisualDualArmTask
from .task_office import OfficeTask


VISUAL_TASK_MAP: Dict[str, Type[VisualDualArmTask]] = {
    OfficeTask.task_id: OfficeTask,
}


def create_visual_task(task_id: str) -> VisualDualArmTask:
    try:
        task_class = VISUAL_TASK_MAP[task_id]
    except KeyError as exc:
        available = ", ".join(sorted(VISUAL_TASK_MAP))
        raise ValueError(
            f"Unknown visual task {task_id!r}; available tasks: {available}"
        ) from exc
    return task_class()


def get_visual_task_descriptions() -> Dict[str, str]:
    return {
        task_id: task_class.task_description
        for task_id, task_class in VISUAL_TASK_MAP.items()
    }
