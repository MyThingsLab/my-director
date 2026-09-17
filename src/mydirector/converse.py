from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from mythings.engine import Engine, EngineRequest, parse_json_object

from mydirector.interview import Prompter
from mydirector.mission import MissionContract, scripted_contract

MAX_TURNS = 14
MAX_SEARCHES = 6

# Unlike every other tool's single Engine call, the mission interview runs one
# call per turn -- that is the point of it, and my-director's AGENTS.md records
# the exception. The budget below is what keeps "bounded" true by construction
# rather than by the model's good manners.
_SYSTEM = (
    "You are interviewing a senior engineer to pin down ONE mission for a fleet of "
    "autonomous coding workers, and the machine-checkable criteria that will prove it "
    "was achieved. You may inspect the codebase before asking, and the operator may "
    "redirect you toward a suspected problem — follow that lead and verify it.\n\n"
    "Reply with ONLY a JSON object, one of:\n"
    '  {"move": "search", "query": "<ripgrep pattern>", "why": "<one sentence>"}\n'
    '  {"move": "question", "question": "<one short question>"}\n'
    '  {"move": "contract", "objective": "...", "why": "...", "repos": ["..."], '
    '"guardrails": ["..."], "done_when": ["..."]}\n\n'
    "Ask only what you cannot determine yourself; search first when you can. Keep each "
    "question to one sentence.\n\n"
    "Every done_when entry must be executable where possible:\n"
    "  $ pytest tests/test_x.py::test_y   (runs argv-only, NO pipes or shell operators)\n"
    "  ci_green Org/repo\n"
    "  pr_merged Org/repo#12\n"
    "  issue_closed Org/repo#34\n"
    "Prose is allowed but can never prove a mission, so prefer a command. A criterion "
    "MUST be failing right now — one that already passes proves nothing about the work "
    "that follows and will be rejected. Propose at least one executable criterion."
)


@runtime_checkable
class Searcher(Protocol):
    def search(self, query: str) -> str: ...


class RipgrepSearcher:
    # Read-only and argv-only: the interview may look at the code, never change
    # it, and a model-authored query never reaches a shell.
    def __init__(self, root: Path, *, max_lines: int = 40, timeout: int = 30) -> None:
        self._root = root
        self._max_lines = max_lines
        self._timeout = timeout

    def search(self, query: str) -> str:
        if not query.strip():
            return "(empty query)"
        argv = ["rg", "--no-heading", "--line-number", "--max-count", "3", "-e", query]
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self._root),
                capture_output=True,
                text=True,
                timeout=self._timeout,
                shell=False,
            )
        except FileNotFoundError:
            return "(ripgrep not installed)"
        except subprocess.TimeoutExpired:
            return "(search timed out)"
        lines = (proc.stdout or "").splitlines()
        if not lines:
            return "(no matches)"
        head = lines[: self._max_lines]
        suffix = "" if len(lines) <= self._max_lines else f"\n… {len(lines) - self._max_lines} more"
        return "\n".join(head) + suffix


class NullSearcher:
    def search(self, query: str) -> str:
        return "(search unavailable)"


@dataclass
class Transcript:
    briefing: str = ""
    turns: list[str] = field(default_factory=list)
    searches: int = 0

    def add_question(self, question: str, answer: str) -> None:
        self.turns.append(f"Q: {question}\nOPERATOR: {answer}")

    def add_search(self, query: str, result: str) -> None:
        self.searches += 1
        self.turns.append(f"SEARCHED {query!r}:\n{result}")

    def as_prompt(self) -> str:
        parts = [f"TODAY'S STATE:\n{self.briefing}"] if self.briefing else []
        parts += self.turns
        if not self.turns:
            parts.append("The interview has not started. Ask your first question, or search first.")
        return "\n\n".join(parts)


def _move(engine: Engine, transcript: Transcript) -> dict | None:
    result = engine.run(EngineRequest(prompt=transcript.as_prompt(), system=_SYSTEM))
    obj = parse_json_object(result.text or "")
    return obj if isinstance(obj, dict) and obj.get("move") else None


def _contract_from(obj: dict) -> MissionContract:
    def _strs(key: str) -> tuple[str, ...]:
        raw = obj.get(key) or []
        if isinstance(raw, str):
            raw = [raw]
        return tuple(str(x).strip() for x in raw if str(x).strip())

    return MissionContract(
        objective=str(obj.get("objective", "")).strip(),
        why=str(obj.get("why", "")).strip(),
        repos=_strs("repos"),
        guardrails=_strs("guardrails"),
        done_when=_strs("done_when"),
    ).with_id()


def converse(
    *,
    briefing: str,
    prompter: Prompter,
    engine: Engine,
    searcher: Searcher | None = None,
    max_turns: int = MAX_TURNS,
    max_searches: int = MAX_SEARCHES,
) -> MissionContract:
    transcript = Transcript(briefing=briefing)
    searcher = searcher or NullSearcher()
    if briefing:
        print(briefing)
        print()

    for _ in range(max_turns):
        obj = _move(engine, transcript)
        if obj is None:
            # NoopEngine, or a reply nothing could parse. Fall back rather than
            # leave the operator in a dead interview.
            print("(no usable model reply — falling back to the scripted interview)")
            return scripted_contract(prompter)

        move = str(obj.get("move", "")).lower()
        if move == "contract":
            contract = _contract_from(obj)
            if contract.objective:
                return _confirm(contract, prompter)
            continue

        if move == "search" and transcript.searches < max_searches:
            query = str(obj.get("query", ""))
            why = str(obj.get("why", "")).strip()
            print(f"  … looking: {why or query}")
            transcript.add_search(query, searcher.search(query))
            continue

        question = str(obj.get("question", "")).strip()
        if not question:
            continue
        transcript.add_question(question, prompter.ask(question))

    # Out of budget with no contract: keep the turns, let the human finish it.
    print(f"(interview hit its {max_turns}-turn budget — finishing it by hand)")
    return scripted_contract(prompter)


def _confirm(contract: MissionContract, prompter: Prompter) -> MissionContract:
    # The contract is the thing the mission is graded against, so the operator
    # sees it in full and edits the criteria before it is binding.
    print()
    print(f"OBJECTIVE: {contract.objective}")
    if contract.why:
        print(f"WHY: {contract.why}")
    if contract.repos:
        print(f"REPOS: {', '.join(contract.repos)}")
    if contract.guardrails:
        print(f"GUARDRAILS: {'; '.join(contract.guardrails)}")
    print("DONE WHEN:")
    for line in contract.done_when or ("(none proposed)",):
        print(f"  - {line}")
    print()

    if prompter.confirm("Accept this mission contract as written?"):
        return contract

    print("Re-enter the criteria — one per line, blank to stop (objective is kept).")
    done_when: list[str] = []
    while True:
        line = prompter.ask(f"done_when [{len(done_when) + 1}]:")
        if not line.strip():
            break
        done_when.append(line.strip())
    objective = prompter.ask("Objective:", default=contract.objective)
    return MissionContract(
        objective=objective,
        why=contract.why,
        repos=contract.repos,
        guardrails=contract.guardrails,
        done_when=tuple(done_when) or contract.done_when,
    ).with_id()
