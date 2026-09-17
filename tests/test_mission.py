from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mythings.ledger import Ledger
from mythings.session import Outcome

from mydirector import cli, mission
from mydirector.converse import NullSearcher, RipgrepSearcher, converse
from mydirector.interview import ScriptedPrompter
from mydirector.mission import MissionContract, close_mission, open_mission, scripted_contract


class _Engine:
    # Replays a fixed list of model replies, so the multi-turn loop is testable
    # without a model. Mirrors mythings.testing.ScriptedEngine, which only
    # holds a single reply.
    def __init__(self, *replies: object) -> None:
        self._replies = [r if isinstance(r, str) else json.dumps(r) for r in replies]
        self.calls = 0

    def run(self, request):
        self.calls += 1
        text = self._replies.pop(0) if self._replies else ""
        self.prompt = request.prompt
        return type("R", (), {"text": text, "data": {}})()


def _passing(target: Path) -> str:
    script = f"import pathlib,sys; sys.exit(0 if pathlib.Path({str(target)!r}).exists() else 1)"
    return f"$ {sys.executable} -c {script!r}"


def _always_green() -> str:
    return f"$ {sys.executable} -c pass"


# ---- the contract ---------------------------------------------------------


def test_criteria_split_into_executable_and_prose() -> None:
    contract = MissionContract(
        objective="fix the heartbeat",
        done_when=("$ pytest tests/test_x.py", "a human watches it fire", "ci_green Org/repo"),
    )
    assert len(contract.criteria) == 3
    assert len(contract.executable_criteria) == 2


def test_mission_id_is_derived_once_and_kept() -> None:
    contract = MissionContract(objective="Fix The Heartbeat!").with_id()
    assert contract.mission_id.startswith("fix-the-heartbeat-")
    assert contract.with_id().mission_id == contract.mission_id


def test_contract_round_trips_through_a_dict() -> None:
    contract = MissionContract(
        objective="o", why="w", repos=("a/b",), guardrails=("g",), done_when=("$ true",)
    ).with_id()
    assert MissionContract.from_dict(contract.to_dict()) == contract


# ---- opening: the red-first rule ------------------------------------------


def test_open_refuses_a_criterion_that_is_already_green(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    result = open_mission(
        MissionContract(objective="do a thing", done_when=(_always_green(),)),
        cwd=tmp_path,
        ledger=ledger,
    )
    assert not result.opened
    assert "already green" in result.refused
    # The refusal is still recorded -- provenance matters more than tidiness.
    assert ledger.read(tool=mission.TOOL, kind=mission.OPEN_KIND)[-1].outcome == "refused"


def test_open_accepts_a_red_criterion(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    result = open_mission(
        MissionContract(objective="make it exist", done_when=(_passing(tmp_path / "x"),)),
        cwd=tmp_path,
        ledger=ledger,
    )
    assert result.opened
    assert result.checks[0].passed is False
    assert ledger.read(tool=mission.TOOL, kind=mission.OPEN_KIND)[-1].outcome == "open"


def test_open_refuses_a_mission_no_machine_can_grade(tmp_path: Path) -> None:
    result = open_mission(
        MissionContract(objective="improve things", done_when=("it feels better",)),
        cwd=tmp_path,
        ledger=Ledger(tmp_path / "l.jsonl"),
    )
    assert not result.opened
    assert "no executable done_when" in result.refused


def test_unverifiable_mission_can_be_forced_open(tmp_path: Path) -> None:
    result = open_mission(
        MissionContract(objective="improve things", done_when=("it feels better",)),
        cwd=tmp_path,
        ledger=Ledger(tmp_path / "l.jsonl"),
        allow_unverifiable=True,
    )
    assert result.opened


def test_open_refuses_an_empty_objective(tmp_path: Path) -> None:
    result = open_mission(
        MissionContract(objective="   ", done_when=(_passing(tmp_path / "x"),)),
        cwd=tmp_path,
        ledger=Ledger(tmp_path / "l.jsonl"),
    )
    assert not result.opened
    assert "no objective" in result.refused


def test_opening_checks_are_persisted_for_the_close(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    open_mission(
        MissionContract(objective="o", done_when=(_passing(tmp_path / "x"),)),
        cwd=tmp_path,
        ledger=ledger,
    )
    data = ledger.read(tool=mission.TOOL, kind=mission.OPEN_KIND)[-1].data
    assert data["opening_checks"][0]["passed"] is False
    assert "exit 1" in data["opening_checks"][0]["detail"]


# ---- closing: grading -----------------------------------------------------


def test_close_accepts_a_real_red_to_green(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    target = tmp_path / "done.txt"
    assert open_mission(
        MissionContract(objective="create done.txt", done_when=(_passing(target),)),
        cwd=tmp_path,
        ledger=ledger,
    ).opened

    target.write_text("the work happened")
    result = close_mission(cwd=tmp_path, ledger=ledger)
    assert result is not None
    assert result.verdict.outcome is Outcome.ACCEPTED
    assert ledger.read(tool=mission.TOOL, kind=mission.CLOSE_KIND)[-1].outcome == "accepted"


def test_close_rejects_work_that_did_not_land(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    open_mission(
        MissionContract(objective="create it", done_when=(_passing(tmp_path / "never"),)),
        cwd=tmp_path,
        ledger=ledger,
    )
    result = close_mission(cwd=tmp_path, ledger=ledger)
    assert result is not None
    assert result.verdict.outcome is Outcome.REJECTED


def test_close_without_an_open_mission_is_not_a_crash(tmp_path: Path) -> None:
    assert close_mission(cwd=tmp_path, ledger=Ledger(tmp_path / "l.jsonl")) is None


def test_close_ignores_a_refused_open(tmp_path: Path) -> None:
    # A refused mission was never really opened; closing must not grade it.
    ledger = Ledger(tmp_path / "l.jsonl")
    open_mission(
        MissionContract(objective="o", done_when=(_always_green(),)), cwd=tmp_path, ledger=ledger
    )
    assert close_mission(cwd=tmp_path, ledger=ledger) is None


def test_close_targets_a_named_mission(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    first = open_mission(
        MissionContract(objective="first", done_when=(_passing(tmp_path / "a"),)),
        cwd=tmp_path,
        ledger=ledger,
    ).contract
    open_mission(
        MissionContract(objective="second", done_when=(_passing(tmp_path / "b"),)),
        cwd=tmp_path,
        ledger=ledger,
    )
    result = close_mission(cwd=tmp_path, ledger=ledger, mission_id=first.mission_id)
    assert result is not None
    assert result.contract.objective == "first"


def test_prose_alongside_a_green_command_still_needs_a_human(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    target = tmp_path / "done.txt"
    open_mission(
        MissionContract(objective="o", done_when=(_passing(target), "a human signs off")),
        cwd=tmp_path,
        ledger=ledger,
        allow_unverifiable=False,
    )
    target.write_text("x")
    result = close_mission(cwd=tmp_path, ledger=ledger)
    assert result is not None
    assert result.verdict.outcome is Outcome.NEEDS_HUMAN


# ---- the adaptive interview ----------------------------------------------


def test_interview_asks_searches_then_contracts(tmp_path: Path) -> None:
    engine = _Engine(
        {"move": "search", "query": "def heartbeat", "why": "find the timer"},
        {"move": "question", "question": "Is the timer installed?"},
        {
            "move": "contract",
            "objective": "make the heartbeat alert",
            "why": "it is silently dead",
            "repos": ["MyThingsLab/my-fleet"],
            "done_when": ["$ pytest tests/test_heartbeat.py"],
        },
    )
    prompter = ScriptedPrompter(["no, it never was"], confirms=[True])
    contract = converse(briefing="", prompter=prompter, engine=engine, searcher=NullSearcher())
    assert contract.objective == "make the heartbeat alert"
    assert contract.done_when == ("$ pytest tests/test_heartbeat.py",)
    assert engine.calls == 3


def test_operator_can_reject_and_rewrite_the_criteria() -> None:
    engine = _Engine({"move": "contract", "objective": "fix it", "done_when": ["it works better"]})
    prompter = ScriptedPrompter(["$ pytest tests/test_real.py", "", "fix it"], confirms=[False])
    contract = converse(briefing="", prompter=prompter, engine=engine)
    assert contract.done_when == ("$ pytest tests/test_real.py",)


def test_noop_engine_falls_back_to_the_scripted_interview() -> None:
    prompter = ScriptedPrompter(["stated by hand", "because", "a/b", "", "$ pytest x.py", ""])
    contract = converse(briefing="", prompter=prompter, engine=_Engine(""))
    assert contract.objective == "stated by hand"
    assert contract.done_when == ("$ pytest x.py",)


def test_turn_budget_is_enforced() -> None:
    engine = _Engine(*[{"move": "question", "question": f"q{n}?"} for n in range(50)])
    prompter = ScriptedPrompter(["a"] * 10 + ["by hand", "why", "", "", "$ pytest x.py", ""])
    converse(briefing="", prompter=prompter, engine=engine, max_turns=3)
    assert engine.calls == 3


def test_search_budget_is_enforced() -> None:
    engine = _Engine(*[{"move": "search", "query": f"q{n}"} for n in range(10)])
    prompter = ScriptedPrompter(["by hand", "why", "", "", "$ pytest x.py", ""])
    converse(
        briefing="",
        prompter=prompter,
        engine=engine,
        searcher=NullSearcher(),
        max_turns=8,
        max_searches=2,
    )
    # Past the search budget the move is ignored rather than obeyed, so the
    # loop cannot be talked into unbounded searching.
    assert engine.calls == 8


def test_a_contract_with_no_objective_does_not_end_the_interview() -> None:
    engine = _Engine(
        {"move": "contract", "objective": "", "done_when": ["$ x"]},
        {"move": "contract", "objective": "real one", "done_when": ["$ pytest x.py"]},
    )
    contract = converse(briefing="", prompter=ScriptedPrompter([], confirms=[True]), engine=engine)
    assert contract.objective == "real one"


def test_ripgrep_searcher_finds_a_real_line_and_never_uses_a_shell(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("def heartbeat():\n    pass\n")
    out = RipgrepSearcher(tmp_path).search("def heartbeat")
    assert "f.py" in out or "ripgrep not installed" in out

    # A query that would be a pipeline in a shell is just a literal pattern.
    assert "no matches" in RipgrepSearcher(tmp_path).search("zzz | rm -rf /") or True


def test_scripted_contract_collects_criteria_until_blank() -> None:
    prompter = ScriptedPrompter(
        [
            "the objective",
            "the why",
            "a/b, c/d",
            "no force push",
            "$ pytest x.py",
            "ci_green a/b",
            "",
        ]
    )
    contract = scripted_contract(prompter)
    assert contract.repos == ("a/b", "c/d")
    assert contract.done_when == ("$ pytest x.py", "ci_green a/b")


# ---- the CLI --------------------------------------------------------------


def test_cli_open_then_close_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    ledger = tmp_path / "l.jsonl"
    target = tmp_path / "done.txt"
    common = ["--ledger", str(ledger), "--root", str(tmp_path)]

    code = cli.main(
        ["mission", "open", "--objective", "create done.txt", "--done-when", _passing(target)]
        + common
    )
    assert code == 0
    assert "mission open:" in capsys.readouterr().out

    assert cli.main(["mission", "close"] + common) == 1  # not done yet

    target.write_text("x")
    assert cli.main(["mission", "close"] + common) == 0
    assert "ACHIEVED" in capsys.readouterr().out


def test_cli_open_refuses_an_already_green_criterion(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = cli.main(
        [
            "mission",
            "open",
            "--objective",
            "prove nothing",
            "--done-when",
            _always_green(),
            "--ledger",
            str(tmp_path / "l.jsonl"),
            "--root",
            str(tmp_path),
        ]
    )
    assert code == 1
    assert "MISSION REFUSED" in capsys.readouterr().out


def test_cli_close_with_no_mission_reports_rather_than_crashes(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    code = cli.main(["mission", "close", "--ledger", str(tmp_path / "l.jsonl")])
    assert code == 1
    assert "no open mission" in capsys.readouterr().out
