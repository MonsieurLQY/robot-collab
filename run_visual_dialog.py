import argparse
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from prompting.budget import PlanningBudget, PlanningStopped
from prompting.dialog_prompter import DialogPrompter
from prompting.llm_config import DEFAULT_GEMINI_MODEL
from prompting.terminal_log import record_terminal
from prompting.visual_feedback import VisualFeedbackManager
from prompting.visual_plan import VisualObservation, VisualPlan
from prompting.visual_plan_parser import VisualPlanParser
from prompting.visual_tasks import (
    create_visual_task,
    get_visual_task_descriptions,
)


PROJECT_ROOT = Path(__file__).resolve().parent
SAFE_RUN_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolve_images(
    configured_images: Optional[Sequence[str]],
    default_images: Sequence[str],
) -> List[Path]:
    if configured_images:
        image_paths = [Path(path).expanduser().resolve() for path in configured_images]
    else:
        image_paths = [
            (PROJECT_ROOT / path).resolve() for path in default_images
        ]
    if not image_paths:
        raise ValueError(
            "No images configured. Pass --image/-i or set default_image_paths "
            "in the selected task."
        )
    missing = [str(path) for path in image_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError("Image file not found: " + ", ".join(missing))
    return image_paths


def _create_run_directory(
    data_dir: str,
    run_name: str,
) -> Path:
    if not SAFE_RUN_NAME.fullmatch(run_name):
        raise ValueError(
            "run_name may contain only letters, numbers, '.', '_' and '-'"
        )
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = Path(data_dir).expanduser() / "visual_dialog" / run_name / timestamp
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir.resolve()


def run_visual_planning(args: argparse.Namespace) -> Path:
    task = create_visual_task(args.task)
    image_paths = _resolve_images(args.image, task.default_image_paths)
    run_name = args.run_name or task.task_id
    run_dir = _create_run_directory(args.data_dir, run_name)
    with record_terminal(run_dir / "terminal.txt"):
        return _run_visual_planning(args, task, image_paths, run_dir)


def _run_visual_planning(args, task, image_paths, run_dir) -> Path:
    rounds_dir = run_dir / "rounds"
    rounds_dir.mkdir()

    budget = PlanningBudget(args.max_model_calls, args.max_episode_seconds, args.model_timeout)
    observation = VisualObservation(image_paths=tuple(image_paths))
    parser = VisualPlanParser(
        task.robot_name_map.values(),
        allowed_action_names=task.atomic_action_names,
    )
    feedback_manager = VisualFeedbackManager()
    prompter = DialogPrompter(
        env=task,
        parser=parser,
        feedback_manager=feedback_manager,
        max_tokens=args.max_tokens,
        debug_mode=args.debug_mode,
        use_waypoints=False,
        robot_name_map=task.robot_name_map,
        num_replans=args.num_replans,
        max_calls_per_round=args.max_calls_per_round,
        use_history=True,
        use_feedback=True,
        temperature=args.temperature,
        llm_source=args.llm_source,
        credentials_path=args.credentials_path,
        vertex_project=args.vertex_project,
        vertex_location=args.vertex_location,
        query_budget=budget,
    )
    plan = VisualPlan(
        task_id=task.task_id,
        task_name=task.task_name,
        image_paths=[str(path) for path in image_paths],
    )
    all_chat_history: List[Dict[str, object]] = []

    _write_json(
        run_dir / "request.json",
        {
            "task_id": task.task_id,
            "task_name": task.task_name,
            "task_instruction": task.task_instruction.strip(),
            "task_rules": task.task_rules.strip(),
            "image_paths": [str(path) for path in image_paths],
            "model": args.llm_source,
            "vertex_project": args.vertex_project,
            "vertex_location": args.vertex_location,
            "temperature": args.temperature,
            "max_tokens": args.max_tokens,
            "max_plan_steps": args.max_plan_steps,
            "max_calls_per_round": args.max_calls_per_round,
            "num_replans": args.num_replans,
        },
    )

    error = None
    try:
        with budget.episode():
            for round_index in range(args.max_plan_steps + 1):
                round_dir = rounds_dir / f"round_{round_index:03d}"
                calls_dir = round_dir / "calls"
                calls_dir.mkdir(parents=True)
                logging.info("Planning round %s starts", round_index)

                ready, candidate_plans, feedbacks, chat_history = (
                    prompter.prompt_one_round(
                        observation,
                        save_path=str(calls_dir),
                        image_paths=image_paths,
                    )
                )
                all_chat_history.append(
                    {
                        "round": round_index,
                        "chat": chat_history,
                        "feedback": feedbacks,
                    }
                )

                if not ready or not candidate_plans:
                    plan.status = "planning_failed"
                    _write_json(
                        round_dir / "result.json",
                        {
                            "status": plan.status,
                            "feedback": feedbacks,
                            "chat": chat_history,
                        },
                    )
                    break

                current_plan = candidate_plans[0]
                if current_plan.task_done:
                    plan.status = "completed"
                    _write_json(
                        round_dir / "result.json",
                        {
                            "status": "task_done",
                            "response": current_plan.get_action_desp(),
                            "chat": chat_history,
                        },
                    )
                    break

                if round_index == args.max_plan_steps:
                    plan.status = "max_plan_steps_reached"
                    _write_json(
                        round_dir / "result.json",
                        {
                            "status": plan.status,
                            "discarded_action": current_plan.get_action_desp(),
                            "chat": chat_history,
                        },
                    )
                    break

                plan.steps.append(current_plan)
                accepted_action = current_plan.get_action_desp()
                _write_json(
                    round_dir / "result.json",
                    {
                        "status": "accepted",
                        **current_plan.to_dict(step_id=len(plan.steps)),
                        "chat": chat_history,
                    },
                )
                prompter.post_plan_update(accepted_action)
            else:
                plan.status = "max_plan_steps_reached"

    except PlanningStopped as exc:
        plan.status = exc.reason
    except KeyboardInterrupt:
        plan.status = "user_interrupted"
    except Exception as exc:
        plan.status, error = "run_error", str(exc)

    _write_json(run_dir / "summary.json", {
        "termination_reason": plan.status, "error": error,
        "model_calls": budget.calls, "max_model_calls": budget.max_calls,
        "max_episode_seconds": budget.max_seconds,
        "model_timeout": budget.request_seconds,
    })
    _write_json(run_dir / "chat_history.json", all_chat_history)
    _write_json(run_dir / "plan.json", plan.to_dict())
    (run_dir / "plan.txt").write_text(plan.to_text(), encoding="utf-8")

    print(f"Visual planning status: {plan.status}")
    print(f"Accepted plan steps: {len(plan.steps)}")
    print(f"Plan JSON: {run_dir / 'plan.json'}")
    print(f"Plan text: {run_dir / 'plan.txt'}")
    return run_dir


def _build_parser() -> argparse.ArgumentParser:
    task_descriptions = get_visual_task_descriptions()
    parser = argparse.ArgumentParser(
        description="Image-conditioned dual-arm dialog planner",
    )
    parser.add_argument("--list_tasks", action="store_true")
    parser.add_argument(
        "--task",
        "-t",
        choices=sorted(task_descriptions),
        help="Short task ID defined in prompting/visual_tasks/",
    )
    parser.add_argument(
        "--image",
        "-i",
        action="append",
        help=(
            "First-person image path; repeat for multiple views. If omitted, "
            "the selected task's default image is used."
        ),
    )
    parser.add_argument("--run_name", "-rn", default=None)
    parser.add_argument("--data_dir", "-d", default="data")
    parser.add_argument("--max_plan_steps", type=int, default=12)
    parser.add_argument("--max_calls_per_round", type=int, default=2)
    parser.add_argument("--num_replans", type=int, default=2)
    parser.add_argument("--max-model-calls", type=int, default=30)
    parser.add_argument("--max-episode-seconds", type=float, default=600)
    parser.add_argument("--model-timeout", type=float, default=60)
    parser.add_argument("--max_tokens", type=int, default=2048)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--debug_mode", action="store_true")
    parser.add_argument("--llm_source", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument("--credentials_path", default=None)
    parser.add_argument("--vertex_project", default=None)
    parser.add_argument("--vertex_location", default=None)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.list_tasks:
        print("Available visual tasks:")
        for task_id, description in get_visual_task_descriptions().items():
            print(f"  {task_id:<12} {description}")
        return 0
    if args.task is None:
        parser.error("--task/-t is required unless --list_tasks is used")
    if args.max_plan_steps < 1:
        parser.error("--max_plan_steps must be at least 1")
    if args.max_calls_per_round < 2:
        parser.error("--max_calls_per_round must be at least 2")
    if args.num_replans < 1:
        parser.error("--num_replans must be at least 1")
    try:
        PlanningBudget(args.max_model_calls, args.max_episode_seconds, args.model_timeout)
    except ValueError as exc:
        parser.error(str(exc))
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s - %(asctime)s - %(message)s",
    )
    run_dir = run_visual_planning(args)
    plan_data = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    return 0 if plan_data["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
