# Copied verbatim from RoboPARA-2.0 robopara2/benchmark/scoring.py (stdlib only), so
# client-side reports share the platform schema without installing robopara2.
"""与任务、路线和仿真器无关的运行打分：成功数/总数 + 规划/执行/总用时。

本模块只依赖标准库，可在其他分支、其他项目或更换任务后直接复用。

成功率有两种口径（都记录）：

* 目标级（``goal_*``，主指标）：按任务声明的每个目标谓词（如
  ``marker_in_holder`` = 笔在笔筒里）在回合结束时是否成立计数，
  例如 5 个目标完成 2 个记 ``2/5``。汇总型谓词 ``success`` 不计入。
* 回合级（``success_*``）：全部目标完成才算该回合成功。

时间口径（两种都记录，互不替代）：

* 墙钟时间（``*_wallclock_s``）：``time.perf_counter`` 测得的真实时间。
  ``total_wallclock_s`` 从 ``EpisodeTimer.start()`` 一直计到 ``finish()``，
  覆盖规划、执行以及二者之间的复位、日志等全部开销；各阶段（如
  ``planning``、``execution``）单独累计，同名阶段多次进入（多轮重规划）会累加，
  因此 ``total >= sum(phase_totals)``，差值记为 ``unattributed_wallclock_s``。
* 仿真时间（``sim_time_s``）：仿真器推进的时间（步数 × 控制步长），由调用方
  通过 ``add_sim_time`` 报告。规划期间仿真器通常暂停，所以仿真时间不含规划。

典型用法::

    timer = EpisodeTimer().start()
    with timer.phase("planning"):
        plan = planner.plan(observation)
    with timer.phase("execution"):
        trace = execute(plan)
    timer.add_sim_time(trace.total_sim_time_s)
    score = timer.finish(task_id="desktop_cleanup", seed=0, success=trace.success)
    report = build_score_report([score])
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
import time
from typing import Any, Callable, Iterable, Iterator, Mapping

SCORE_SCHEMA = "robopara.score.v2"
PLANNING = "planning"
EXECUTION = "execution"
AGGREGATE_PREDICATES = frozenset({"success"})


def goal_results(
    goal_predicates: Iterable[str],
    final_predicates: Mapping[str, Any],
    *,
    aggregate: Iterable[str] = AGGREGATE_PREDICATES,
) -> dict[str, bool]:
    """按任务目标谓词逐项判定；缺失的谓词视为未完成，汇总谓词被排除。"""

    excluded = set(aggregate)
    return {
        name: bool(final_predicates.get(name, False))
        for name in goal_predicates
        if name not in excluded
    }


def _utc_iso(unix_s: float) -> str:
    return datetime.fromtimestamp(unix_s, tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class PhaseSpan:
    """一次阶段区间，偏移量相对回合开始（墙钟秒）。"""

    name: str
    start_offset_s: float
    end_offset_s: float

    @property
    def duration_s(self) -> float:
        return self.end_offset_s - self.start_offset_s

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start_offset_s": self.start_offset_s,
            "end_offset_s": self.end_offset_s,
            "duration_s": self.duration_s,
        }


@dataclass(frozen=True)
class EpisodeScore:
    """一个回合的打分记录；成功与否必须来自任务成功判定，而非计划跑完。"""

    task_id: str
    seed: int | None
    success: bool
    total_wallclock_s: float
    phase_wallclock_s: Mapping[str, float] = field(default_factory=dict)
    sim_time_s: float | None = None
    phases: tuple[PhaseSpan, ...] = ()
    episode_id: str | None = None
    termination_reason: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    goal_results: Mapping[str, bool] = field(default_factory=dict)

    @property
    def goals_achieved(self) -> int:
        return sum(bool(value) for value in self.goal_results.values())

    @property
    def goals_total(self) -> int:
        return len(self.goal_results)

    @property
    def planning_wallclock_s(self) -> float:
        return float(self.phase_wallclock_s.get(PLANNING, 0.0))

    @property
    def execution_wallclock_s(self) -> float:
        return float(self.phase_wallclock_s.get(EXECUTION, 0.0))

    @property
    def unattributed_wallclock_s(self) -> float:
        return max(0.0, self.total_wallclock_s - sum(self.phase_wallclock_s.values()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCORE_SCHEMA,
            "episode_id": self.episode_id,
            "task_id": self.task_id,
            "seed": self.seed,
            "success": bool(self.success),
            "goals_achieved": self.goals_achieved,
            "goals_total": self.goals_total,
            "goal_ratio": f"{self.goals_achieved}/{self.goals_total}",
            "goal_results": dict(self.goal_results),
            "termination_reason": self.termination_reason,
            "total_wallclock_s": self.total_wallclock_s,
            "planning_wallclock_s": self.planning_wallclock_s,
            "execution_wallclock_s": self.execution_wallclock_s,
            "unattributed_wallclock_s": self.unattributed_wallclock_s,
            "phase_wallclock_s": dict(self.phase_wallclock_s),
            "sim_time_s": self.sim_time_s,
            "phases": [span.to_dict() for span in self.phases],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EpisodeScore":
        return cls(
            task_id=str(payload["task_id"]),
            seed=payload.get("seed"),
            success=bool(payload["success"]),
            total_wallclock_s=float(payload["total_wallclock_s"]),
            phase_wallclock_s={
                str(name): float(value)
                for name, value in dict(payload.get("phase_wallclock_s", {})).items()
            },
            sim_time_s=(
                None if payload.get("sim_time_s") is None
                else float(payload["sim_time_s"])
            ),
            phases=tuple(
                PhaseSpan(
                    str(item["name"]),
                    float(item["start_offset_s"]),
                    float(item["end_offset_s"]),
                )
                for item in payload.get("phases", ())
            ),
            episode_id=payload.get("episode_id"),
            termination_reason=payload.get("termination_reason"),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            extra=dict(payload.get("extra", {})),
            goal_results={
                str(name): bool(value)
                for name, value in dict(payload.get("goal_results", {})).items()
            },
        )


class EpisodeTimer:
    """回合秒表：总墙钟时间 + 分阶段墙钟时间 + 仿真时间。

    阶段不可嵌套，保证各阶段之和不超过总时间。阶段内抛异常时仍会记录。
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.perf_counter,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._start: float | None = None
        self._started_unix: float | None = None
        self._active: str | None = None
        self._spans: list[PhaseSpan] = []
        self._sim_time_s: float | None = None
        self._score: EpisodeScore | None = None

    @property
    def started(self) -> bool:
        return self._start is not None

    def start(self) -> "EpisodeTimer":
        if self._start is not None:
            raise RuntimeError("episode timer already started")
        self._start = self._clock()
        self._started_unix = self._wall_clock()
        return self

    def elapsed_s(self) -> float:
        if self._start is None:
            raise RuntimeError("episode timer has not started")
        return self._clock() - self._start

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if self._start is None:
            self.start()
        if self._score is not None:
            raise RuntimeError("episode timer already finished")
        if self._active is not None:
            raise RuntimeError(
                f"phase {name!r} cannot start while {self._active!r} is active"
            )
        self._active = name
        begin = self.elapsed_s()
        try:
            yield
        finally:
            self._spans.append(PhaseSpan(name, begin, self.elapsed_s()))
            self._active = None

    def record_phase(self, name: str, duration_s: float) -> None:
        """补记一个外部测得的阶段（如远端规划器自报耗时）。"""

        if duration_s < 0:
            raise ValueError("phase duration must be non-negative")
        end = self.elapsed_s()
        self._spans.append(PhaseSpan(name, max(0.0, end - duration_s), end))

    def add_sim_time(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("sim time must be non-negative")
        self._sim_time_s = (self._sim_time_s or 0.0) + float(seconds)

    def finish(
        self,
        *,
        task_id: str,
        success: bool,
        seed: int | None = None,
        episode_id: str | None = None,
        termination_reason: str | None = None,
        extra: Mapping[str, Any] | None = None,
        goal_results: Mapping[str, bool] | None = None,
    ) -> EpisodeScore:
        if self._score is not None:
            return self._score
        if self._active is not None:
            raise RuntimeError(f"phase {self._active!r} is still active")
        total = self.elapsed_s()
        totals: dict[str, float] = {}
        for span in self._spans:
            totals[span.name] = totals.get(span.name, 0.0) + span.duration_s
        started_unix = float(self._started_unix or 0.0)
        self._score = EpisodeScore(
            task_id=task_id,
            seed=seed,
            success=bool(success),
            total_wallclock_s=total,
            phase_wallclock_s=totals,
            sim_time_s=self._sim_time_s,
            phases=tuple(self._spans),
            episode_id=episode_id,
            termination_reason=termination_reason,
            started_at=_utc_iso(started_unix),
            finished_at=_utc_iso(started_unix + total),
            extra=dict(extra or {}),
            goal_results=dict(goal_results or {}),
        )
        return self._score


def _stats(values: list[float]) -> dict[str, float | None]:
    return {
        "sum": sum(values) if values else 0.0,
        "mean": mean(values) if values else None,
        "min": min(values) if values else None,
        "max": max(values) if values else None,
    }


def _timing_block(scores: list[EpisodeScore]) -> dict[str, Any]:
    phase_names = sorted({name for score in scores for name in score.phase_wallclock_s})
    for required in (PLANNING, EXECUTION):
        if required not in phase_names:
            phase_names.append(required)
    sim_times = [float(s.sim_time_s) for s in scores if s.sim_time_s is not None]
    return {
        "total_wallclock_s": _stats([s.total_wallclock_s for s in scores]),
        "planning_wallclock_s": _stats([s.planning_wallclock_s for s in scores]),
        "execution_wallclock_s": _stats([s.execution_wallclock_s for s in scores]),
        "unattributed_wallclock_s": _stats(
            [s.unattributed_wallclock_s for s in scores]
        ),
        "phase_wallclock_s": {
            name: _stats([float(s.phase_wallclock_s.get(name, 0.0)) for s in scores])
            for name in sorted(phase_names)
        },
        "sim_time_s": _stats(sim_times),
        "sim_time_reported_episodes": len(sim_times),
    }


def summarize_scores(scores: Iterable[EpisodeScore]) -> dict[str, Any]:
    """汇总目标级与回合级成功数/总数及用时；成功回合的用时另列。"""

    items = list(scores)
    successes = [score for score in items if score.success]
    total = len(items)
    goals_achieved = sum(score.goals_achieved for score in items)
    goals_total = sum(score.goals_total for score in items)
    per_goal: dict[str, dict[str, Any]] = {}
    for score in items:
        for name, value in score.goal_results.items():
            entry = per_goal.setdefault(name, {"achieved": 0, "total": 0})
            entry["achieved"] += int(bool(value))
            entry["total"] += 1
    for entry in per_goal.values():
        entry["ratio"] = f"{entry['achieved']}/{entry['total']}"
        entry["rate"] = entry["achieved"] / entry["total"]
    return {
        "goal_success_count": goals_achieved,
        "goal_total_count": goals_total,
        "goal_success_ratio": f"{goals_achieved}/{goals_total}",
        "goal_success_rate": goals_achieved / goals_total if goals_total else None,
        "per_goal": dict(sorted(per_goal.items())),
        "success_count": len(successes),
        "total_count": total,
        "success_ratio": f"{len(successes)}/{total}",
        "success_rate": len(successes) / total if total else None,
        "timing": _timing_block(items),
        "timing_successful_only": _timing_block(successes),
    }


def build_score_report(
    scores: Iterable[EpisodeScore],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """最终运行报告：总体汇总 + 按 task 分组 + 逐回合明细。"""

    items = list(scores)
    by_task: dict[str, list[EpisodeScore]] = {}
    for score in items:
        by_task.setdefault(score.task_id, []).append(score)
    return {
        "schema": SCORE_SCHEMA,
        "generated_at": _utc_iso(time.time()),
        "metadata": dict(metadata or {}),
        "overall": summarize_scores(items),
        "by_task": {task: summarize_scores(group) for task, group in sorted(by_task.items())},
        "episodes": [score.to_dict() for score in items],
    }


def write_score_report(
    path: str | Path,
    scores: Iterable[EpisodeScore],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = build_score_report(scores, metadata=metadata)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return report


def load_score_report(path: str | Path) -> list[EpisodeScore]:
    """读回报告中的逐回合记录，可跨多次运行合并后重新汇总。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return [EpisodeScore.from_dict(item) for item in payload.get("episodes", ())]


def format_score_summary(report: Mapping[str, Any]) -> str:
    """一行可读摘要，便于打印在终端末尾。"""

    overall = report["overall"]
    timing = overall["timing"]

    def _fmt(block: Mapping[str, Any], key: str) -> str:
        value = block[key]["sum"]
        return "n/a" if value is None else f"{value:.2f}s"

    def _ratio(prefix: str) -> str:
        rate = overall[f"{prefix}_rate"]
        return overall[f"{prefix}_ratio"] + (
            f" ({rate:.1%})" if rate is not None else ""
        )

    return (
        f"goals {_ratio('goal_success')}"
        + f" | full-task episodes {_ratio('success')}"
        + f" | wallclock total {_fmt(timing, 'total_wallclock_s')}"
        + f" (planning {_fmt(timing, 'planning_wallclock_s')},"
        + f" execution {_fmt(timing, 'execution_wallclock_s')})"
        + " | sim time "
        + (
            _fmt(timing, "sim_time_s")
            if timing["sim_time_reported_episodes"] else "n/a"
        )
    )


__all__ = [
    "AGGREGATE_PREDICATES",
    "EXECUTION",
    "PLANNING",
    "SCORE_SCHEMA",
    "EpisodeScore",
    "EpisodeTimer",
    "PhaseSpan",
    "build_score_report",
    "format_score_summary",
    "goal_results",
    "load_score_report",
    "summarize_scores",
    "write_score_report",
]
