"""Run original RoCo dialog planning against the state-based RoboPARA track."""
import argparse
import json
from pathlib import Path
import time

from prompting.budget import PlanningBudget, PlanningStopped, deadline
from prompting.dialog_prompter import DialogPrompter
from prompting.llm_config import DEFAULT_GEMINI_MODEL, detect_image_mime_type
from prompting.terminal_log import record_terminal
from prompting.robopara.adapter import DesktopTask, PlannerClient, PlatformFeedback, SemanticParser


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INSTRUCTION = PROJECT_ROOT / "instruction.txt"

def resolve_visual_input(args):
    # Without --image the platform's latest-frame buffer is read every round.
    images = [Path(path).expanduser().resolve() for path in (args.image or [])]
    for image in images:
        detect_image_mime_type(image.read_bytes())
    instruction = args.instruction
    if args.instruction_file or instruction is None:
        path = Path(args.instruction_file).expanduser() if args.instruction_file else DEFAULT_INSTRUCTION
        instruction = path.read_text(encoding="utf-8")
    if instruction is not None and not instruction.strip():
        raise ValueError("Task instruction must not be empty")
    return images, instruction


def capture_scene_image(result, round_dir):
    """Copy the platform's latest frame into the round log for the model to read.

    The platform buffer keeps only the newest frame and is overwritten as the
    robots move, so the model reads this copy: the logged image is exactly the
    one it saw. Returns the copy's path and the frame record.
    """
    frame = result.get("image")
    if not frame:
        raise RuntimeError(
            result.get("image_error") or "platform response has no image; start the planner "
            "server without --no-frame-buffer, or pass --image for a static scene image")
    if frame.get("steps") != result.get("steps"):
        raise RuntimeError(
            f"platform frame is stale: frame step {frame.get('steps')}, state step "
            f"{result.get('steps')}; {result.get('image_error') or 'no capture error reported'}")
    source = Path(frame["path"])
    data = source.read_bytes()
    detect_image_mime_type(data)
    target = Path(round_dir) / ("scene" + source.suffix)
    target.write_bytes(data)
    return target, {**frame, "saved_path": str(target)}


def run_episode(args, seed):
    images, instruction = resolve_visual_input(args)
    directory = Path(args.data_dir) / args.run_name / f"seed_{seed}"
    directory.mkdir(parents=True, exist_ok=False)
    with record_terminal(directory / "terminal.txt"):
        return _run_episode(args, seed, images, instruction, directory)


def _run_episode(args, seed, images, instruction, directory):
    live_images = not images
    observation_mode = "latest_frame" if live_images else "initial_image"
    client = PlannerClient(args.server_url, args.timeout)
    result, records = {}, []
    budget = PlanningBudget(args.max_model_calls, args.max_episode_seconds, args.model_timeout)
    termination_reason, error, close_error = "max_rounds", None, None
    consecutive_failures = 0
    planning_s = 0.0
    started = time.perf_counter()
    try:
        with budget.episode():
            result = client.call("reset", task="desktop_cleanup", seed=seed)
            task_spec = dict(result["task"])
            if instruction is not None:
                task_spec["instruction"] = instruction.strip()
            task = DesktopTask(task_spec, image_input=True, live_image=live_images)
            if live_images:
                images = [Path(result.get("image", {}).get("path", ""))]
            (directory / "request.json").write_text(json.dumps({
                "task": task_spec, "image_paths": [str(path) for path in images],
                "observation_mode": observation_mode,
                "success_contract": "desktop_cleanup (unchanged)",
            }, indent=2, ensure_ascii=False), encoding="utf-8")
            prompter = DialogPrompter(
                env=task, parser=SemanticParser(), feedback_manager=PlatformFeedback(client, enabled=not args.no_feedback),
                robot_name_map=({"left": "LeftArm"} if args.single_agent else
                                {"left": "LeftArm", "right": "RightArm"}),
                use_waypoints=False, num_replans=args.num_replans,
                max_calls_per_round=args.max_calls_per_round,
                use_history=not args.no_history, use_feedback=not args.no_feedback,
                debug_mode=args.debug_mode, llm_source=args.llm_source,
                credentials_path=args.credentials_path, vertex_project=args.vertex_project,
                vertex_location=args.vertex_location, temperature=args.temperature,
                query_budget=budget,
            )
            for index in range(args.max_rounds):
                if result.get("terminal"):
                    termination_reason = "task_success" if result.get("task_success") else result.get("reason", "platform_terminal")
                    break
                round_dir = directory / f"round_{index}"
                round_dir.mkdir()
                # Each round needs fresh environment information: read the newest frame.
                round_images, frame = images, None
                if live_images:
                    scene, frame = capture_scene_image(result, round_dir)
                    round_images = [scene]
                began = time.perf_counter()
                try:
                    ready, plans, feedback, chat = prompter.prompt_one_round(
                        result["state"], save_path=str(round_dir), image_paths=round_images)
                finally:
                    planning_s += time.perf_counter() - began
                if not ready:
                    records.append({"accepted": False, "feedback": feedback, "chat": chat,
                                    "image": frame})
                    consecutive_failures += 1
                    if consecutive_failures >= args.max_consecutive_failures:
                        termination_reason = "consecutive_failure_limit"
                        break
                    continue
                plan = plans[0]
                result = client.call("execute", actions=plan.actions)
                task.last_execution = None if args.no_feedback else {key: result.get(key) for key in (
                    "reason", "execution_success", "completed", "failed", "interrupted", "diagnostics")}
                prompter.post_execute_update(json.dumps(result["state"]),
                                             (True if args.no_feedback else result.get("execution_success", False)),
                                             plan.get_action_desp())
                records.append({"accepted": True, "proposal": plan.get_action_desp(),
                                "feedback": feedback, "image": frame, "result": result})
                if result.get("terminal"):
                    termination_reason = "task_success" if result.get("task_success") else result.get("reason", "platform_terminal")
                    break
                consecutive_failures = (0 if result.get("execution_success") else consecutive_failures + 1)
                if consecutive_failures >= args.max_consecutive_failures:
                    termination_reason = "consecutive_failure_limit"
                    break
    except PlanningStopped as exc:
        termination_reason = exc.reason
    except KeyboardInterrupt:
        termination_reason = "user_interrupted"
    except Exception as exc:
        termination_reason, error = "run_error", str(exc)
    finally:
        try:
            if client.episode_id:
                with deadline(5, "close_timeout"):
                    client.call("close")
        except Exception as exc:
            close_error = str(exc)
        finally:
            summary = {"seed": seed, "track": "state_based_shared_executor",
                       "algorithm": "roco_adapted", "model": args.llm_source,
                       "observation_mode": observation_mode,
                       "image_paths": [str(path) for path in images],
                       "single_agent": args.single_agent, "feedback": not args.no_feedback,
                       "planning_wallclock_s": planning_s,
                       "episode_wallclock_s": time.perf_counter() - started,
                       "termination_reason": termination_reason, "error": error, "close_error": close_error,
                       "limits": {"max_model_calls": args.max_model_calls,
                                  "max_rounds": args.max_rounds,
                                  "num_replans": args.num_replans,
                                  "max_calls_per_round": args.max_calls_per_round,
                                  "max_episode_seconds": args.max_episode_seconds,
                                  "model_timeout": args.model_timeout,
                                  "max_consecutive_failures": args.max_consecutive_failures},
                       "task_success": bool(result.get("task_success", False)),
                       "result": result, "rounds": records}
            summary["model_calls"] = budget.calls
            summary["saved_model_responses"] = len(list(directory.glob("round_*/replan*_call*_agent*.json")))
            (directory / "summary.json").write_text(
                json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"seed={seed} task_success={summary['task_success']} reason={termination_reason} model_calls={budget.calls}/{budget.max_calls} logs={directory}")
    return summary


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", "-i", action="append",
                        help="Static scene image sent every round instead of the platform's latest "
                             "frame; default: read the latest frame each round. Repeat for multiple views.")
    instruction_group = parser.add_mutually_exclusive_group()
    instruction_group.add_argument("--instruction", help="Task instruction text")
    instruction_group.add_argument("--instruction-file", help="UTF-8 task instruction file; default: project instruction.txt")
    parser.add_argument("--server-url", default="http://127.0.0.1:8010")
    parser.add_argument("--max-model-calls", type=int, default=30)
    parser.add_argument("--max-episode-seconds", type=float, default=600)
    parser.add_argument("--model-timeout", type=float, default=60)
    parser.add_argument("--max-consecutive-failures", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--num-replans", type=int, default=2)
    parser.add_argument("--max-calls-per-round", type=int, default=2)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--run-name", default=time.strftime("robopara_%Y%m%d_%H%M%S"))
    parser.add_argument("--single-agent", action="store_true")
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--no-feedback", action="store_true")
    parser.add_argument("--debug-mode", action="store_true")
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument("--llm-source", default=DEFAULT_GEMINI_MODEL)
    parser.add_argument("--credentials-path", default=None)
    parser.add_argument("--vertex-project", default=None)
    parser.add_argument("--vertex-location", default=None)
    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()
    if min(args.episodes, args.max_rounds, args.num_replans, args.max_consecutive_failures) < 1 or args.max_calls_per_round < 2:
        parser.error("positive budgets required and max-calls-per-round must be >= 2")
    try:
        resolve_visual_input(args)
        PlanningBudget(args.max_model_calls, args.max_episode_seconds, args.model_timeout)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    results = [run_episode(args, args.seed + i) for i in range(args.episodes)]
    aggregate = {"episodes": len(results), "success_rate": sum(r["task_success"] for r in results) / len(results)}
    (Path(args.data_dir) / args.run_name / "aggregate.json").write_text(json.dumps(aggregate, indent=2))
    return 0 if all(r["task_success"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
