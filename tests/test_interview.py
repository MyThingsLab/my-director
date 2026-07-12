from __future__ import annotations

from mydirector.interview import ScriptedPrompter, conduct
from mydirector.sources import Briefing


def _briefing() -> Briefing:
    return Briefing(recent=[], latest_plan=[], open_counts={})


def test_conduct_captures_a_full_director_decision() -> None:
    prompter = ScriptedPrompter(
        answers=[
            "shipped my-bibliography",  # shipped
            "my-guide repo not created",  # blocked
            "Land the mythings.mastery seam so study tools can share progress",  # objective
            "It unblocks four study consumers",  # why
            "next",  # horizon
            "my-things-core, my-professor",  # repos
            "seam-first, keep it local JSONL",  # guardrails
            "extract cycle_driver first",  # task hints
        ]
    )
    ans = conduct(_briefing(), prompter)
    assert ans.objective.startswith("Land the mythings.mastery seam")
    assert ans.horizon == "next"
    assert ans.repos == ["my-things-core", "my-professor"]
    assert ans.guardrails == ["seam-first", "keep it local JSONL"]
    assert ans.task_hints == "extract cycle_driver first"


def test_empty_replies_fall_back_to_briefing_defaults() -> None:
    # A blank shipped/blocked answer takes the briefing headline default.
    from mythings.ledger import LedgerEntry

    briefing = Briefing(
        recent=[LedgerEntry(tool="my-x", kind="ship", outcome="success", detail="shipped foo")]
    )
    prompter = ScriptedPrompter(answers=["", "", "an objective", "a why", "", "repo-a", "", ""])
    ans = conduct(briefing, prompter)
    assert "shipped foo" in ans.shipped  # took the default
    assert ans.horizon == "next"  # default when blank
    assert ans.guardrails == []
