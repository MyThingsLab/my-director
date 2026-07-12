from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from mythings.engine import Engine, EngineRequest

from mydirector.interview import DirectorAnswers

HORIZONS = ("next", "soon", "later")

# The single Engine call is narrow on purpose: the *human* decides the objective,
# the guardrails, and the target repos. The model only decomposes that stated
# objective into concrete, fleet-ready task-issues. It never invents a different
# objective and never overrides the human's guardrails.
_SYSTEM = (
    "You decompose one operator-chosen objective into concrete task-issues for a "
    "fleet of autonomous coding workers. Each task becomes a GitHub issue a worker "
    "picks up and closes with one pull request, so it must be small, self-contained, "
    "and independently verifiable. Do NOT restate or second-guess the objective; only "
    "break it into tasks. Reply with ONLY a JSON object, nothing else: "
    '{"tasks": [{"title": "<imperative, <=70 chars>", "repo": "<owner/name or bare '
    'repo>", "label": "<backlog label a worker filters on>", "acceptance": "<one '
    'sentence: what makes this done>", "agent_type": "<claude|explore|plan>", '
    '"rationale": "<why this task, one sentence>"}]}'
)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Task:
    title: str
    repo: str
    label: str
    acceptance: str
    agent_type: str = "claude"
    rationale: str = ""


@dataclass(frozen=True)
class Objective:
    statement: str
    why: str
    horizon: str = "next"


@dataclass
class SessionPlan:
    objective: Objective
    tasks: list[Task] = field(default_factory=list)
    guardrails: list[str] = field(default_factory=list)
    engine_used: bool = False
    generated_ts: str = field(default_factory=_utc_now)

    def to_dict(self) -> dict:
        return {
            "objective": asdict(self.objective),
            "tasks": [asdict(t) for t in self.tasks],
            "guardrails": list(self.guardrails),
            "engine_used": self.engine_used,
            "generated_ts": self.generated_ts,
        }

    @classmethod
    def from_dict(cls, obj: dict) -> SessionPlan:
        o = obj["objective"]
        return cls(
            objective=Objective(
                statement=o["statement"], why=o.get("why", ""), horizon=o.get("horizon", "next")
            ),
            tasks=[
                Task(
                    title=t["title"],
                    repo=t.get("repo", ""),
                    label=t.get("label", ""),
                    acceptance=t.get("acceptance", ""),
                    agent_type=t.get("agent_type", "claude"),
                    rationale=t.get("rationale", ""),
                )
                for t in obj.get("tasks", [])
            ],
            guardrails=list(obj.get("guardrails", [])),
            engine_used=obj.get("engine_used", False),
            generated_ts=obj.get("generated_ts", _utc_now()),
        )


def _request(answers: DirectorAnswers, briefing: str) -> EngineRequest:
    repos = ", ".join(answers.repos) if answers.repos else "(operator did not specify)"
    hints = answers.task_hints.strip() or "(none — you decide the decomposition)"
    prompt = (
        f"OBJECTIVE (decided by the operator, do not change it):\n{answers.objective}\n\n"
        f"WHY IT MATTERS NOW:\n{answers.why}\n\n"
        f"TARGET REPO(S): {repos}\n\n"
        f"OPERATOR'S TASK HINTS: {hints}\n\n"
        f"GUARDRAILS (must be respected, never contradicted): "
        f"{'; '.join(answers.guardrails) or '(none)'}\n\n"
        f"CONTEXT / TODAY'S STATE:\n{briefing}"
    )
    return EngineRequest(prompt=prompt, system=_SYSTEM)


def _degraded_tasks(answers: DirectorAnswers) -> list[Task]:
    # No usable Engine reply: fall back to a single task that is the objective
    # itself, targeting the first named repo. Plumbing only — an honest "the
    # model gave us nothing" placeholder, not a real decomposition.
    repo = answers.repos[0] if answers.repos else ""
    return [
        Task(
            title=answers.objective[:70],
            repo=repo,
            label="",
            acceptance=answers.objective,
            agent_type="claude",
            rationale="degraded: no Engine decomposition — operator must refine",
        )
    ]


def _parse_tasks(text: str) -> list[Task] | None:
    if not text.strip():
        return None
    try:
        obj = json.loads(text)
        raw = obj["tasks"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    tasks: list[Task] = []
    for t in raw:
        if not isinstance(t, dict) or not t.get("title"):
            continue
        tasks.append(
            Task(
                title=str(t["title"]),
                repo=str(t.get("repo", "")),
                label=str(t.get("label", "")),
                acceptance=str(t.get("acceptance", "")),
                agent_type=str(t.get("agent_type", "claude")),
                rationale=str(t.get("rationale", "")),
            )
        )
    return tasks or None


def synthesize(answers: DirectorAnswers, briefing: str, engine: Engine) -> SessionPlan:
    result = engine.run(_request(answers, briefing))
    tasks = _parse_tasks(result.text)
    horizon = answers.horizon if answers.horizon in HORIZONS else "next"
    return SessionPlan(
        objective=Objective(statement=answers.objective, why=answers.why, horizon=horizon),
        tasks=tasks if tasks is not None else _degraded_tasks(answers),
        guardrails=list(answers.guardrails),
        engine_used=tasks is not None,
    )
