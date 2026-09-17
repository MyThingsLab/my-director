from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger

from mydirector import escalate


def _needs_human_entry(ledger: Ledger, candidate: str, *, attempt: int = 3) -> None:
    ledger.record(
        tool="fleet_dispatch",
        kind="dispatch",
        outcome="needs_human",
        detail=f"{candidate}: gave up after {attempt} attempts",
        candidate=candidate,
        attempt=attempt,
    )


def test_unescalated_blockers_finds_a_new_needs_human_entry(tmp_path: Path) -> None:
    dispatch_ledger = Ledger(tmp_path / "dispatch.jsonl")
    _needs_human_entry(dispatch_ledger, "my-guard#3")
    ledger = Ledger(tmp_path / "director.jsonl")

    blockers = escalate.unescalated_blockers(dispatch_ledger=dispatch_ledger, ledger=ledger)

    assert len(blockers) == 1
    assert blockers[0].candidate == "my-guard#3"
    assert blockers[0].attempt == 3


def test_unescalated_blockers_ignores_other_outcomes(tmp_path: Path) -> None:
    dispatch_ledger = Ledger(tmp_path / "dispatch.jsonl")
    dispatch_ledger.record(
        tool="fleet_dispatch", kind="dispatch", outcome="success", detail="fine",
        candidate="my-guard#1",
    )
    ledger = Ledger(tmp_path / "director.jsonl")

    assert escalate.unescalated_blockers(dispatch_ledger=dispatch_ledger, ledger=ledger) == []


def test_a_previously_escalated_candidate_is_not_offered_again(tmp_path: Path) -> None:
    dispatch_ledger = Ledger(tmp_path / "dispatch.jsonl")
    _needs_human_entry(dispatch_ledger, "my-guard#3")
    ledger = Ledger(tmp_path / "director.jsonl")
    blocker = escalate.unescalated_blockers(dispatch_ledger=dispatch_ledger, ledger=ledger)[0]
    escalate.record_escalated(ledger, blocker)

    assert escalate.unescalated_blockers(dispatch_ledger=dispatch_ledger, ledger=ledger) == []


def test_unescalated_blockers_ignores_a_needs_human_entry_with_no_candidate(
    tmp_path: Path,
) -> None:
    dispatch_ledger = Ledger(tmp_path / "dispatch.jsonl")
    dispatch_ledger.record(
        tool="fleet_dispatch", kind="dispatch", outcome="needs_human", detail="no candidate field",
    )
    ledger = Ledger(tmp_path / "director.jsonl")

    assert escalate.unescalated_blockers(dispatch_ledger=dispatch_ledger, ledger=ledger) == []


def test_missing_dispatch_ledger_degrades_to_empty(tmp_path: Path) -> None:
    dispatch_ledger = Ledger(tmp_path / "nope.jsonl")
    ledger = Ledger(tmp_path / "director.jsonl")

    assert escalate.unescalated_blockers(dispatch_ledger=dispatch_ledger, ledger=ledger) == []


def test_push_blocker_shells_out_with_the_right_flags(tmp_path: Path, monkeypatch) -> None:
    seen = tmp_path / "flags.txt"
    script = tmp_path / "mytelegrambot"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" > "{seen}"\n'
        "exit 0\n"
    )
    script.chmod(0o755)
    monkeypatch.setattr(escalate, "_bot_binary", lambda: script)

    blocker = escalate.Blocker(candidate="my-guard#3", detail="gave up after 3 attempts", attempt=3)
    ok = escalate.push_blocker(blocker, bot_ledger=tmp_path / "bot.jsonl")

    assert ok is True
    flags = seen.read_text()
    assert "escalate-blocker" in flags
    assert "my-guard#3" in flags


def test_push_blocker_returns_false_on_a_failing_command(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "mytelegrambot"
    script.write_text("#!/bin/sh\nexit 1\n")
    script.chmod(0o755)
    monkeypatch.setattr(escalate, "_bot_binary", lambda: script)

    blocker = escalate.Blocker(candidate="my-guard#3", detail="stuck", attempt=3)
    ok = escalate.push_blocker(blocker, bot_ledger=tmp_path / "bot.jsonl")

    assert ok is False


def test_push_blocker_returns_false_when_the_binary_cannot_be_launched(
    tmp_path: Path, monkeypatch
) -> None:
    # A resolved path that does not actually exist raises OSError/FileNotFoundError
    # from subprocess.run -- the same failure mode a stale PATH entry would hit.
    monkeypatch.setattr(escalate, "_bot_binary", lambda: tmp_path / "no-such-binary")

    blocker = escalate.Blocker(candidate="my-guard#3", detail="stuck", attempt=3)
    ok = escalate.push_blocker(blocker, bot_ledger=tmp_path / "bot.jsonl", timeout=1.0)

    assert ok is False


def test_bot_binary_prefers_the_one_beside_the_interpreter(
    tmp_path: Path, monkeypatch
) -> None:
    fake_interpreter_dir = tmp_path / "venv" / "bin"
    fake_interpreter_dir.mkdir(parents=True)
    (fake_interpreter_dir / "python3").touch()
    (fake_interpreter_dir / "mytelegrambot").touch()
    monkeypatch.setattr(escalate.sys, "executable", str(fake_interpreter_dir / "python3"))

    assert escalate._bot_binary() == fake_interpreter_dir / "mytelegrambot"


def test_bot_binary_falls_back_to_path(tmp_path: Path, monkeypatch) -> None:
    fake_interpreter_dir = tmp_path / "venv" / "bin"
    fake_interpreter_dir.mkdir(parents=True)
    (fake_interpreter_dir / "python3").touch()  # no mytelegrambot beside it
    monkeypatch.setattr(escalate.sys, "executable", str(fake_interpreter_dir / "python3"))
    monkeypatch.setattr(escalate.shutil, "which", lambda _name: "/usr/local/bin/mytelegrambot")

    assert escalate._bot_binary() == Path("/usr/local/bin/mytelegrambot")


def test_bot_binary_is_none_when_unresolvable(tmp_path: Path, monkeypatch) -> None:
    fake_interpreter_dir = tmp_path / "venv" / "bin"
    fake_interpreter_dir.mkdir(parents=True)
    (fake_interpreter_dir / "python3").touch()
    monkeypatch.setattr(escalate.sys, "executable", str(fake_interpreter_dir / "python3"))
    monkeypatch.setattr(escalate.shutil, "which", lambda _name: None)

    assert escalate._bot_binary() is None
