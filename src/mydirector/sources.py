from __future__ import annotations

from dataclasses import dataclass, field

from mythings.ledger import Ledger, LedgerEntry

# Outcomes that read as "something moved" vs. "something is stuck", used to seed
# the two retrospective interview prompts without a model call.
_SHIPPED = {"success", "ship", "shipped", "merged"}
_STUCK = {"denied", "failure", "blocked", "needs_human", "deferred"}


@dataclass
class Briefing:
    recent: list[LedgerEntry] = field(default_factory=list)
    latest_plan: list[str] = field(default_factory=list)
    open_counts: dict[str, int] = field(default_factory=dict)

    def _by_outcome(self, outcomes: set[str]) -> list[LedgerEntry]:
        return [e for e in self.recent if e.outcome in outcomes]

    def recent_headline(self) -> str:
        shipped = self._by_outcome(_SHIPPED)
        return "; ".join(f"{e.tool}: {e.detail}" for e in shipped[:3])

    def blocked_headline(self) -> str:
        stuck = self._by_outcome(_STUCK)
        return "; ".join(f"{e.tool}: {e.detail}" for e in stuck[:3])

    def summary(self) -> str:
        lines = ["=== today's state (assembled, no model call) ==="]
        if self.recent:
            lines.append(f"recent activity ({len(self.recent)} entries):")
            for e in self.recent[:8]:
                lines.append(f"  [{e.outcome}] {e.tool}/{e.kind}: {e.detail}")
        else:
            lines.append("recent activity: (none in the given ledger window)")
        if self.latest_plan:
            lines.append("myplanner's latest recommended sequence:")
            for item in self.latest_plan[:6]:
                lines.append(f"  - {item}")
        if self.open_counts:
            counts = ", ".join(f"{repo}: {n}" for repo, n in sorted(self.open_counts.items()))
            lines.append(f"open issues by repo: {counts}")
        return "\n".join(lines)


def _recent_entries(ledger: Ledger, limit: int) -> list[LedgerEntry]:
    try:
        entries = list(ledger)
    except (OSError, ValueError):
        return []
    return entries[-limit:][::-1]


def _latest_plan(planner_ledger: Ledger) -> list[str]:
    # Read MyPlanner's latest kind=plan entry straight off disk — the same
    # read-only seam MyTodo uses, so there is no package dependency on MyPlanner.
    plans = [e for e in _recent_entries(planner_ledger, limit=200) if e.kind == "plan"]
    if not plans:
        return []
    items = plans[0].data.get("plan") or plans[0].data.get("items") or []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            out.append(f"[{item.get('horizon', '?')}] {item.get('item', '')}")
        else:
            out.append(str(item))
    return out


def assemble(
    *,
    ledger: Ledger,
    planner_ledger: Ledger | None = None,
    open_counts: dict[str, int] | None = None,
    recent_limit: int = 30,
) -> Briefing:
    return Briefing(
        recent=_recent_entries(ledger, recent_limit),
        latest_plan=_latest_plan(planner_ledger) if planner_ledger is not None else [],
        open_counts=dict(open_counts or {}),
    )
