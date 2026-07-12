from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger, LedgerEntry

from mydirector import sources


def test_assemble_reads_recent_entries_newest_first(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.append(LedgerEntry(tool="a", kind="ship", outcome="success", detail="old"))
    ledger.append(LedgerEntry(tool="b", kind="run", outcome="failure", detail="new"))
    briefing = sources.assemble(ledger=ledger)
    assert briefing.recent[0].detail == "new"  # newest first
    assert "new" in briefing.blocked_headline()
    assert "old" in briefing.recent_headline()


def test_missing_ledger_degrades_to_empty(tmp_path: Path) -> None:
    briefing = sources.assemble(ledger=Ledger(tmp_path / "nope.jsonl"))
    assert briefing.recent == []
    assert "none" in briefing.summary()


def test_latest_plan_is_surfaced(tmp_path: Path) -> None:
    planner = Ledger(tmp_path / "planner.jsonl")
    planner.append(
        LedgerEntry(
            tool="my-planner",
            kind="plan",
            outcome="success",
            detail="",
            data={"plan": [{"item": "build my-mastery", "horizon": "next"}]},
        )
    )
    briefing = sources.assemble(ledger=Ledger(tmp_path / "l.jsonl"), planner_ledger=planner)
    assert briefing.latest_plan == ["[next] build my-mastery"]
    assert "build my-mastery" in briefing.summary()
