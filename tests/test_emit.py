from __future__ import annotations

import json
from pathlib import Path

from mythings.ledger import Ledger
from mythings.testing import FakeGh

from mydirector import emit
from mydirector.emit import DefaultPolicy, upsert_section
from mydirector.emit import emit as run_emit
from mydirector.interview import ScriptedPrompter
from mydirector.plan import Objective, SessionPlan, Task


def fake_gh(body: str = "existing body") -> FakeGh:
    # Stateful wiring over the shared FakeGh: issue create hands out numbers,
    # edit --body updates what view returns (kept on gh.body so tests can
    # assert the final board text).
    state = {"n": 100}

    def issue_create(argv: list[str]) -> str:
        state["n"] += 1
        return f"https://github.com/o/r/issues/{state['n']}\n"

    def issue_edit(argv: list[str]) -> str:
        if "--body" in argv:
            gh.body = argv[argv.index("--body") + 1]
        return ""

    gh = FakeGh(
        {
            ("issue", "create"): issue_create,
            ("issue", "view"): lambda argv: gh.body,
            ("issue", "edit"): issue_edit,
        }
    )
    gh.body = body
    return gh


def _plan(n_tasks: int = 2) -> SessionPlan:
    tasks = [
        Task(
            title=f"Task {i}",
            repo="MyThingsLab/my-things-core",
            label="core",
            acceptance="tests pass",
            rationale="because",
        )
        for i in range(n_tasks)
    ]
    return SessionPlan(
        objective=Objective(statement="Ship X", why="leverage", horizon="next"),
        tasks=tasks,
        guardrails=["seam-first"],
        engine_used=True,
    )


def test_dry_run_writes_ledger_and_artifact_but_no_gh(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    gh = fake_gh()
    result = run_emit(
        _plan(),
        ledger=ledger,
        artifact_dir=tmp_path / "art",
        execute=False,
        policy=DefaultPolicy(),
        prompter=ScriptedPrompter(answers=[]),
        runner=gh,
    )
    assert result.created_issues == []
    assert gh.calls == []  # zero gh mutations
    assert (tmp_path / "art" / "session_plan.md").exists()
    assert (tmp_path / "art" / "session_plan.json").exists()
    entries = ledger.read(tool=emit.TOOL, kind=emit.LEDGER_KIND)
    assert len(entries) == 1
    assert entries[0].data["objective"]["statement"] == "Ship X"


def test_execute_creates_one_issue_per_task_when_confirmed(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    gh = fake_gh(body="## Board\n\nsome text\n")
    result = run_emit(
        _plan(2),
        ledger=ledger,
        artifact_dir=tmp_path / "art",
        execute=True,
        policy=DefaultPolicy(),
        prompter=ScriptedPrompter(answers=[], confirms=[True, True, True]),
        tracking_repo="MyThingsLab/my-things-core",
        tracking_issue=1,
        runner=gh,
        unattended=False,
    )
    assert len(result.created_issues) == 2
    assert result.tracking_updated is True
    assert gh.saw("issue", "create")
    assert "## Next session — critical objective" in gh.body


def test_execute_respects_declined_confirmation(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    gh = fake_gh()
    result = run_emit(
        _plan(1),
        ledger=ledger,
        artifact_dir=tmp_path / "art",
        execute=True,
        policy=DefaultPolicy(),
        prompter=ScriptedPrompter(answers=[], confirms=[False]),
        runner=gh,
        unattended=False,
    )
    assert result.created_issues == []
    assert result.skipped and "not confirmed" in result.skipped[0]
    assert not gh.saw("issue", "create")


def test_unattended_ask_blocks_creation(tmp_path: Path) -> None:
    # In CI, ASK degrades to DENY regardless of the prompter's answer.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    gh = fake_gh()
    result = run_emit(
        _plan(1),
        ledger=ledger,
        artifact_dir=tmp_path / "art",
        execute=True,
        policy=DefaultPolicy(),
        prompter=ScriptedPrompter(answers=[], confirms=[True]),
        runner=gh,
        unattended=True,
    )
    assert result.created_issues == []
    assert not gh.saw("issue", "create")


def test_upsert_section_is_idempotent() -> None:
    section = "## Next session — critical objective\n\nfirst\n"
    once = upsert_section("## Board\n\nx\n", section)
    twice = upsert_section(once, "## Next session — critical objective\n\nsecond\n")
    assert once.count("## Next session — critical objective") == 1
    assert twice.count("## Next session — critical objective") == 1
    assert "second" in twice and "first" not in twice
    assert "## Board" in twice  # unrelated section preserved


def test_task_without_repo_is_skipped(tmp_path: Path) -> None:
    plan = SessionPlan(
        objective=Objective(statement="X", why="", horizon="next"),
        tasks=[Task(title="no repo", repo="", label="", acceptance="")],
    )
    gh = fake_gh()
    result = run_emit(
        plan,
        ledger=Ledger(tmp_path / "l.jsonl"),
        artifact_dir=tmp_path / "art",
        execute=True,
        policy=DefaultPolicy(),
        prompter=ScriptedPrompter(answers=[], confirms=[True]),
        runner=gh,
        unattended=False,
    )
    assert result.created_issues == []
    assert "no target repo" in result.skipped[0]


def test_artifact_json_round_trips(tmp_path: Path) -> None:
    run_emit(
        _plan(1),
        ledger=Ledger(tmp_path / "l.jsonl"),
        artifact_dir=tmp_path / "art",
        execute=False,
        policy=DefaultPolicy(),
        prompter=ScriptedPrompter(answers=[]),
    )
    data = json.loads((tmp_path / "art" / "session_plan.json").read_text())
    restored = SessionPlan.from_dict(data)
    assert restored.objective.statement == "Ship X"
