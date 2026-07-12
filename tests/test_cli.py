from __future__ import annotations

from pathlib import Path

import pytest
from mythings.engine import ClaudeCLIEngine, NoopEngine
from mythings.ledger import Ledger

from mydirector import emit
from mydirector.cli import build_engine, main
from mydirector.interview import ScriptedPrompter


def _director_answers() -> list[str]:
    return [
        "shipped my-bibliography",
        "nothing blocked",
        "Land the mastery seam",
        "unblocks study tools",
        "next",
        "my-things-core",
        "seam-first",
        "",  # task hints
    ]


def test_session_dry_run_writes_plan_and_no_gh(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    rc = main(
        [
            "session",
            "--ledger",
            str(ledger),
            "--artifact-dir",
            str(tmp_path / "art"),
        ],
        prompter=ScriptedPrompter(answers=_director_answers()),
    )
    assert rc == 0
    assert (tmp_path / "art" / "session_plan.md").exists()
    entries = Ledger(ledger).read(tool=emit.TOOL, kind=emit.LEDGER_KIND)
    assert len(entries) == 1
    assert entries[0].data["objective"]["statement"] == "Land the mastery seam"
    out = capsys.readouterr().out
    assert "dry run" in out


def test_session_aborts_on_empty_objective(tmp_path: Path) -> None:
    answers = _director_answers()
    answers[2] = ""  # no objective
    rc = main(
        ["session", "--ledger", str(tmp_path / "l.jsonl"), "--artifact-dir", str(tmp_path / "a")],
        prompter=ScriptedPrompter(answers=answers),
    )
    assert rc == 1


def test_show_prints_latest_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = tmp_path / "ledger.jsonl"
    main(
        ["session", "--ledger", str(ledger), "--artifact-dir", str(tmp_path / "art")],
        prompter=ScriptedPrompter(answers=_director_answers()),
    )
    capsys.readouterr()  # drain
    rc = main(["show", "--ledger", str(ledger)])
    assert rc == 0
    assert "Land the mastery seam" in capsys.readouterr().out


def test_show_without_a_plan_exits_nonzero(tmp_path: Path) -> None:
    assert main(["show", "--ledger", str(tmp_path / "empty.jsonl")]) == 1


def test_build_engine_selects_backend() -> None:
    assert isinstance(build_engine("noop"), NoopEngine)
    assert isinstance(build_engine("claude-cli"), ClaudeCLIEngine)


def test_escalate_pushes_a_new_needs_human_blocker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mydirector import escalate as escalate_module

    dispatch_ledger = Ledger(tmp_path / "dispatch.jsonl")
    dispatch_ledger.record(
        tool="fleet_dispatch", kind="dispatch", outcome="needs_human",
        detail="my-guard#3: gave up", candidate="my-guard#3", attempt=3,
    )
    monkeypatch.setattr(escalate_module, "push_blocker", lambda *a, **k: True)

    rc = main(
        [
            "escalate",
            "--ledger", str(tmp_path / "director.jsonl"),
            "--dispatch-ledger", str(tmp_path / "dispatch.jsonl"),
            "--bot-ledger", str(tmp_path / "bot.jsonl"),
        ]
    )

    assert rc == 0
    assert "1 new blocker(s), 1 pushed" in capsys.readouterr().out
    escalated = Ledger(tmp_path / "director.jsonl").read(kind="escalated")
    assert escalated[0].data["candidate"] == "my-guard#3"


def test_escalate_with_nothing_new_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        [
            "escalate",
            "--ledger", str(tmp_path / "director.jsonl"),
            "--dispatch-ledger", str(tmp_path / "dispatch.jsonl"),
            "--bot-ledger", str(tmp_path / "bot.jsonl"),
        ]
    )

    assert rc == 0
    assert "no new needs_human blocker" in capsys.readouterr().out


def test_escalate_exits_nonzero_when_a_push_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mydirector import escalate as escalate_module

    dispatch_ledger = Ledger(tmp_path / "dispatch.jsonl")
    dispatch_ledger.record(
        tool="fleet_dispatch", kind="dispatch", outcome="needs_human",
        detail="my-guard#3: gave up", candidate="my-guard#3", attempt=3,
    )
    monkeypatch.setattr(escalate_module, "push_blocker", lambda *a, **k: False)

    rc = main(
        [
            "escalate",
            "--ledger", str(tmp_path / "director.jsonl"),
            "--dispatch-ledger", str(tmp_path / "dispatch.jsonl"),
            "--bot-ledger", str(tmp_path / "bot.jsonl"),
        ]
    )

    assert rc == 1
    assert Ledger(tmp_path / "director.jsonl").read(kind="escalated") == []
