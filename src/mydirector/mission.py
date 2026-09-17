from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from mythings.contract import Criterion, already_green, grade, parse_criteria, run_criteria
from mythings.ledger import Ledger, LedgerEntry
from mythings.session import Check, Outcome, Verdict

from mydirector.interview import Prompter

TOOL = "my-director"
OPEN_KIND = "mission_open"
CLOSE_KIND = "mission_close"


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "mission"


@dataclass(frozen=True)
class MissionContract:
    objective: str
    why: str = ""
    repos: tuple[str, ...] = ()
    guardrails: tuple[str, ...] = ()
    done_when: tuple[str, ...] = ()
    mission_id: str = ""
    generated_ts: str = field(default_factory=_utc_now)

    def with_id(self) -> MissionContract:
        if self.mission_id:
            return self
        stamp = self.generated_ts.replace("-", "").replace(":", "").replace("Z", "")
        return MissionContract(**{**asdict(self), "mission_id": f"{_slug(self.objective)}-{stamp}"})

    @property
    def criteria(self) -> tuple[Criterion, ...]:
        return parse_criteria(self.done_when)

    @property
    def executable_criteria(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if c.executable)

    def to_dict(self) -> dict:
        return asdict(self) | {
            "repos": list(self.repos),
            "guardrails": list(self.guardrails),
            "done_when": list(self.done_when),
        }

    @classmethod
    def from_dict(cls, obj: dict) -> MissionContract:
        return cls(
            objective=obj.get("objective", ""),
            why=obj.get("why", ""),
            repos=tuple(obj.get("repos", ())),
            guardrails=tuple(obj.get("guardrails", ())),
            done_when=tuple(obj.get("done_when", ())),
            mission_id=obj.get("mission_id", ""),
            generated_ts=obj.get("generated_ts", _utc_now()),
        )


@dataclass(frozen=True)
class OpenResult:
    contract: MissionContract
    checks: tuple[Check, ...]
    refused: str = ""

    @property
    def opened(self) -> bool:
        return not self.refused


def _checks_payload(checks: tuple[Check, ...]) -> list[dict]:
    return [asdict(check) for check in checks]


def _checks_from_payload(raw: list[dict]) -> tuple[Check, ...]:
    return tuple(
        Check(name=c.get("name", ""), passed=c.get("passed"), detail=c.get("detail", ""))
        for c in raw
    )


def render(contract: MissionContract, checks: tuple[Check, ...]) -> str:
    lines = [f"# Mission: {contract.objective}", ""]
    if contract.why:
        lines += [contract.why, ""]
    if contract.repos:
        lines += [f"**Repos:** {', '.join(contract.repos)}", ""]
    if contract.guardrails:
        lines += ["**Guardrails:**", *[f"- {g}" for g in contract.guardrails], ""]
    lines += ["**Done when:**"]
    for criterion, check in zip(contract.criteria, checks, strict=False):
        mark = {True: "GREEN", False: "red", None: "unevaluable"}[check.passed]
        lines.append(f"- [{mark}] {criterion.raw}")
        if check.detail:
            lines.append(f"      {check.detail}")
    return "\n".join(lines)


def open_mission(
    contract: MissionContract,
    *,
    cwd: Path,
    ledger: Ledger,
    policy=None,
    runner=None,
    unattended: bool = False,
    allow_unverifiable: bool = False,
) -> OpenResult:
    contract = contract.with_id()
    checks = run_criteria(
        contract.criteria, cwd=cwd, policy=policy, runner=runner, unattended=unattended
    )

    refused = ""
    if not contract.objective.strip():
        refused = "no objective given"
    elif not contract.executable_criteria and not allow_unverifiable:
        # The point of the whole mechanism: a mission no machine can grade
        # closes on the same judgement that produced the diff.
        refused = (
            "no executable done_when criterion — nothing could prove this mission "
            "was achieved (pass --allow-unverifiable to open it anyway)"
        )
    elif green := already_green(checks):
        # A criterion that already passes cannot distinguish 'the work landed'
        # from 'the work never happened'.
        refused = f"already green before any work started: {'; '.join(green)}"

    ledger.append(
        LedgerEntry(
            tool=TOOL,
            kind=OPEN_KIND,
            outcome="refused" if refused else "open",
            detail=refused or contract.objective,
            data={
                "mission": contract.to_dict(),
                "opening_checks": _checks_payload(checks),
            },
        )
    )
    return OpenResult(contract=contract, checks=checks, refused=refused)


@dataclass(frozen=True)
class CloseResult:
    contract: MissionContract
    verdict: Verdict
    closing_checks: tuple[Check, ...]


def latest_open(ledger: Ledger, *, mission_id: str = "") -> LedgerEntry | None:
    entries = [e for e in ledger.read(tool=TOOL, kind=OPEN_KIND) if e.outcome == "open"]
    if mission_id:
        entries = [e for e in entries if e.data.get("mission", {}).get("mission_id") == mission_id]
    return entries[-1] if entries else None


def close_mission(
    *,
    cwd: Path,
    ledger: Ledger,
    mission_id: str = "",
    policy=None,
    runner=None,
    unattended: bool = False,
) -> CloseResult | None:
    entry = latest_open(ledger, mission_id=mission_id)
    if entry is None:
        return None

    contract = MissionContract.from_dict(entry.data.get("mission", {}))
    opening = _checks_from_payload(entry.data.get("opening_checks", []))
    closing = run_criteria(
        contract.criteria, cwd=cwd, policy=policy, runner=runner, unattended=unattended
    )
    verdict = grade(opening, closing)

    ledger.append(
        LedgerEntry(
            tool=TOOL,
            kind=CLOSE_KIND,
            outcome=verdict.outcome.value,
            detail=verdict.reason,
            data={
                "mission_id": contract.mission_id,
                "objective": contract.objective,
                "opening_checks": _checks_payload(opening),
                "closing_checks": _checks_payload(closing),
                "verdict": {
                    "outcome": verdict.outcome.value,
                    "reason": verdict.reason,
                    "checks": _checks_payload(verdict.checks),
                },
            },
        )
    )
    return CloseResult(contract=contract, verdict=verdict, closing_checks=closing)


def render_verdict(result: CloseResult) -> str:
    banner = {
        Outcome.ACCEPTED: "ACHIEVED",
        Outcome.REJECTED: "NOT ACHIEVED",
        Outcome.NEEDS_HUMAN: "NEEDS A HUMAN",
    }[result.verdict.outcome]
    lines = [f"# {banner}: {result.contract.objective}", "", result.verdict.reason, ""]
    for check in result.verdict.checks:
        mark = {True: "pass", False: "FAIL", None: "?"}[check.passed]
        lines.append(f"- [{mark}] {check.name}")
        if check.detail:
            lines.append(f"      {check.detail}")
    return "\n".join(lines)


_SPLIT = re.compile(r"\s*,\s*")


def scripted_contract(prompter: Prompter, *, briefing: str = "") -> MissionContract:
    # The deterministic path: no Engine, so `--engine noop`, a scripted test,
    # and a model that returned nothing all still reach a real contract.
    if briefing:
        print(briefing)
        print()
    objective = prompter.ask("The ONE objective for this mission:")
    why = prompter.ask("Why is that the highest-leverage thing now?")
    repos = tuple(p for p in _SPLIT.split(prompter.ask("Repo(s) it touches?")) if p)
    guardrails = tuple(
        p for p in _SPLIT.split(prompter.ask("Guardrails the workers must respect?")) if p
    )
    print()
    print("Now the part that makes this verifiable. One criterion per line, blank to stop.")
    print("  $ pytest tests/test_x.py::test_y     ci_green Org/repo")
    print("  pr_merged Org/repo#12                issue_closed Org/repo#34")
    done_when: list[str] = []
    while True:
        line = prompter.ask(f"done_when [{len(done_when) + 1}]:")
        if not line.strip():
            break
        done_when.append(line.strip())
    return MissionContract(
        objective=objective,
        why=why,
        repos=repos,
        guardrails=guardrails,
        done_when=tuple(done_when),
    ).with_id()
