from __future__ import annotations

import json

from mythings.engine import EngineRequest, EngineResult, NoopEngine

from mydirector.interview import DirectorAnswers
from mydirector.plan import SessionPlan, synthesize


def _answers(**over) -> DirectorAnswers:
    base = dict(
        shipped="",
        blocked="",
        objective="Ship the mastery seam",
        why="unblocks study tools",
        horizon="next",
        repos=["my-things-core", "my-professor"],
        guardrails=["seam-first"],
        task_hints="",
    )
    base.update(over)
    return DirectorAnswers(**base)


class _JsonEngine:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def run(self, request: EngineRequest) -> EngineResult:
        return EngineResult(text=json.dumps(self._payload))


def test_engine_reply_is_parsed_into_tasks() -> None:
    engine = _JsonEngine(
        {
            "tasks": [
                {
                    "title": "Add mythings.mastery seam",
                    "repo": "MyThingsLab/my-things-core",
                    "label": "core",
                    "acceptance": "JSONL store with read/write + tests",
                    "agent_type": "claude",
                    "rationale": "the shared primitive",
                }
            ]
        }
    )
    plan = synthesize(_answers(), "briefing text", engine)
    assert plan.engine_used is True
    assert plan.objective.statement == "Ship the mastery seam"
    assert plan.guardrails == ["seam-first"]
    assert len(plan.tasks) == 1
    assert plan.tasks[0].repo == "MyThingsLab/my-things-core"


def test_noop_engine_degrades_to_a_single_objective_task() -> None:
    plan = synthesize(_answers(), "briefing", NoopEngine())
    assert plan.engine_used is False
    assert len(plan.tasks) == 1
    assert plan.tasks[0].repo == "my-things-core"  # first named repo
    assert "degraded" in plan.tasks[0].rationale


def test_malformed_reply_degrades_never_raises() -> None:
    class Bad:
        def run(self, request: EngineRequest) -> EngineResult:
            return EngineResult(text="not json at all {")

    plan = synthesize(_answers(), "briefing", Bad())
    assert plan.engine_used is False
    assert len(plan.tasks) == 1


def test_round_trip_dict() -> None:
    plan = synthesize(_answers(), "b", NoopEngine())
    restored = SessionPlan.from_dict(plan.to_dict())
    assert restored.to_dict() == plan.to_dict()
