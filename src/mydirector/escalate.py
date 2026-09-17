from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from mythings.ledger import Ledger

# fleet-dispatch#44: "a stuck worker should ask a human, not just give up."
#
# fleet_dispatch marks a candidate `needs_human` after MAX_ATTEMPTS unresolved
# tries -- a state nobody is watching, so the work just stops until the
# operator happens to look. This reads that ledger straight off disk (the same
# read-only seam sources.py already uses for MyPlanner's sequence -- no package
# dependency on fleet_dispatch) and pushes each new one to Telegram as a CLI
# hand-off, the fleet's normal cross-tool relationship.

_SELF_TOOL = "mydirector"
_ESCALATED_KIND = "escalated"


@dataclass(frozen=True)
class Blocker:
    candidate: str
    detail: str
    attempt: int


def _needs_human_blockers(dispatch_ledger: Ledger) -> list[Blocker]:
    try:
        entries = dispatch_ledger.read(kind="dispatch")
    except (OSError, ValueError):
        return []
    out = []
    for e in entries:
        if e.outcome != "needs_human":
            continue
        candidate = e.data.get("candidate")
        if not candidate:
            continue
        attempt = int(e.data.get("attempt", 0))
        out.append(Blocker(candidate=candidate, detail=e.detail, attempt=attempt))
    return out


def _already_escalated(ledger: Ledger) -> set[str]:
    return {
        e.data.get("candidate")
        for e in ledger.read(tool=_SELF_TOOL, kind=_ESCALATED_KIND)
        if e.data.get("candidate")
    }


def unescalated_blockers(*, dispatch_ledger: Ledger, ledger: Ledger) -> list[Blocker]:
    # Escalate each candidate at most once: a needs_human entry stays in
    # fleet_dispatch's ledger forever once written, so without this a rerun
    # would re-push the same blocker every time.
    seen = _already_escalated(ledger)
    return [b for b in _needs_human_blockers(dispatch_ledger) if b.candidate not in seen]


def record_escalated(ledger: Ledger, blocker: Blocker) -> None:
    # Only called after a successful push. A failed push is deliberately not
    # recorded at all, so unescalated_blockers() offers the same blocker again
    # next run instead of a broken channel silently losing it -- the same
    # "don't advance the cursor on failure" posture notify()/send_spend_alert()
    # already take.
    ledger.record(
        tool=_SELF_TOOL,
        kind=_ESCALATED_KIND,
        outcome="success",
        detail=f"escalated {blocker.candidate}",
        candidate=blocker.candidate,
    )


def _bot_binary() -> Path | None:
    # Same resolution fleet_ask.py uses: a subprocess does not necessarily
    # inherit a PATH containing the venv's bin, so the interpreter running us
    # is the ground truth -- the console script sits beside it.
    beside_interpreter = Path(sys.executable).parent / "mytelegrambot"
    if beside_interpreter.exists():
        return beside_interpreter
    found = shutil.which("mytelegrambot")
    return Path(found) if found else None


def push_blocker(blocker: Blocker, *, bot_ledger: Path, timeout: float = 30.0) -> bool:
    binary = _bot_binary() or Path("mytelegrambot")
    argv = [
        str(binary),
        "escalate-blocker",
        "--candidate", blocker.candidate,
        "--detail", blocker.detail,
        "--attempt", str(blocker.attempt),
        "--ledger", str(bot_ledger),
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"mydirector: escalation push failed, will retry next run: {type(exc).__name__}")
        return False
    if proc.returncode != 0:
        print(
            f"mydirector: mytelegrambot escalate-blocker exited {proc.returncode}: "
            f"{(proc.stdout or proc.stderr).strip()}"
        )
        return False
    return True
