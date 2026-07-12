from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from mythings.github import GitHub, Runner, _gh
from mythings.isolation import in_github_actions
from mythings.ledger import Ledger, LedgerEntry
from mythings.policy import ALLOW, Action, Decision, Policy, PolicyResult

from mydirector.interview import Prompter
from mydirector.plan import SessionPlan, Task

TOOL = "my-director"
LEDGER_KIND = "session_plan"
SECTION_HEADER = "## Next session — critical objective"


class DefaultPolicy:
    # Creating public issues and editing the org tracking issue are both
    # public-content mutations: ASK by default (same classification MyPlanner /
    # MyProjector give that action kind). Writing the local artifact/ledger is
    # not a public mutation and never reaches Policy.
    def evaluate(self, action: Action) -> PolicyResult:
        if action.kind in ("issue-create", "tracking-issue-edit"):
            return PolicyResult(Decision.ASK, reason="edits public content", rule="public-content")
        return ALLOW


# -- rendering -----------------------------------------------------------


def render_markdown(plan: SessionPlan) -> str:
    o = plan.objective
    lines = [
        SECTION_HEADER,
        "",
        f"**Objective ({o.horizon}):** {o.statement}",
        "",
        f"_Why now:_ {o.why}" if o.why else "",
        "",
        "### Tasks",
    ]
    for i, t in enumerate(plan.tasks, 1):
        target = f"{t.repo}" + (f" `{t.label}`" if t.label else "")
        lines.append(f"{i}. **{t.title}** — {target} (agent: {t.agent_type})")
        if t.acceptance:
            lines.append(f"   - done when: {t.acceptance}")
        if t.rationale:
            lines.append(f"   - why: {t.rationale}")
    if plan.guardrails:
        lines.append("")
        lines.append("### Guardrails")
        for g in plan.guardrails:
            lines.append(f"- {g}")
    via = "engine" if plan.engine_used else "degraded (no usable Engine reply)"
    lines += ["", f"_synthesis: {via} · generated {plan.generated_ts}_"]
    return "\n".join(line for line in lines if line is not None)


def _issue_body(plan: SessionPlan, task: Task) -> str:
    o = plan.objective
    parts = [
        f"Part of the next-session objective: **{o.statement}**",
        "",
        f"**Acceptance:** {task.acceptance}" if task.acceptance else "",
        f"**Why:** {task.rationale}" if task.rationale else "",
        f"**Suggested agent:** {task.agent_type}",
    ]
    if plan.guardrails:
        parts += ["", "**Guardrails:**", *[f"- {g}" for g in plan.guardrails]]
    parts += ["", "_Filed by my-director from an end-of-day director session._"]
    return "\n".join(p for p in parts if p)


# -- side effects --------------------------------------------------------


def write_artifact(plan: SessionPlan, directory: str | Path) -> Path:
    # ALLOW: a local file the next fleet session reads. Not a public mutation.
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    md = out / "session_plan.md"
    md.write_text(render_markdown(plan) + "\n", encoding="utf-8")
    (out / "session_plan.json").write_text(
        json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return md


def record_ledger(plan: SessionPlan, ledger: Ledger) -> LedgerEntry:
    n = len(plan.tasks)
    detail = f"objective: {plan.objective.statement} ({n} task{'s' if n != 1 else ''})"
    return ledger.append(
        LedgerEntry(
            tool=TOOL,
            kind=LEDGER_KIND,
            outcome="success",
            detail=detail,
            data=plan.to_dict(),
        )
    )


@dataclass(frozen=True)
class EmitResult:
    created_issues: list[str]
    tracking_updated: bool
    skipped: list[str]


def _gate(
    policy: Policy, prompter: Prompter, action: Action, prompt: str, *, unattended: bool
) -> bool:
    # ALLOW proceeds; ASK asks the operator (in an unattended run ASK→DENY, so
    # nothing is created without a human); DENY refuses.
    decision = policy.evaluate(action).under(unattended=unattended)
    if decision is Decision.ALLOW:
        return True
    if decision is Decision.ASK:
        return prompter.confirm(prompt)
    return False


def create_issues(
    plan: SessionPlan,
    *,
    policy: Policy,
    prompter: Prompter,
    runner: Runner = _gh,
    unattended: bool | None = None,
) -> tuple[list[str], list[str]]:
    unattended = in_github_actions() if unattended is None else unattended
    created: list[str] = []
    skipped: list[str] = []
    for task in plan.tasks:
        if not task.repo:
            skipped.append(f"{task.title} (no target repo)")
            continue
        action = Action(kind="issue-create", payload={"repo": task.repo, "title": task.title})
        prompt = f"Create issue in {task.repo}: {task.title!r}?"
        if not _gate(policy, prompter, action, prompt, unattended=unattended):
            skipped.append(f"{task.title} (not confirmed)")
            continue
        gh = GitHub(task.repo, runner=runner)
        issue = gh.create_issue(title=task.title, body=_issue_body(plan, task))
        if task.label:
            gh.add_labels(issue.number, [task.label])
        created.append(issue.url)
    return created, skipped


def update_tracking(
    plan: SessionPlan,
    *,
    repo: str,
    issue: int,
    policy: Policy,
    prompter: Prompter,
    runner: Runner = _gh,
    unattended: bool | None = None,
) -> bool:
    unattended = in_github_actions() if unattended is None else unattended
    action = Action(kind="tracking-issue-edit", payload={"repo": repo, "issue": issue})
    prompt = f"Update tracking issue {repo}#{issue}?"
    if not _gate(policy, prompter, action, prompt, unattended=unattended):
        return False
    body = runner(["issue", "view", str(issue), "--repo", repo, "--json", "body", "-q", ".body"])
    new_body = upsert_section(body, render_markdown(plan))
    if new_body == body:
        return False
    argv = ["issue", "edit", str(issue), "--repo", repo, "--body", new_body]
    runner(argv)
    return True


_SECTION_RE = re.compile(
    rf"^{re.escape(SECTION_HEADER)}\s*$.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL
)


def upsert_section(body: str, section: str) -> str:
    block = section.rstrip() + "\n"
    if _SECTION_RE.search(body):
        return _SECTION_RE.sub(block, body, count=1).rstrip() + "\n"
    sep = "" if not body else ("\n" if body.endswith("\n") else "\n\n")
    return body + sep + block


def emit(
    plan: SessionPlan,
    *,
    ledger: Ledger,
    artifact_dir: str | Path,
    execute: bool,
    policy: Policy,
    prompter: Prompter,
    tracking_repo: str | None = None,
    tracking_issue: int | None = None,
    runner: Runner = _gh,
    unattended: bool | None = None,
) -> EmitResult:
    # Always-on, ALLOW side effects first.
    write_artifact(plan, artifact_dir)
    record_ledger(plan, ledger)
    if not execute:
        return EmitResult(created_issues=[], tracking_updated=False, skipped=[])
    created, skipped = create_issues(
        plan, policy=policy, prompter=prompter, runner=runner, unattended=unattended
    )
    tracking_updated = False
    if tracking_repo and tracking_issue is not None:
        tracking_updated = update_tracking(
            plan,
            repo=tracking_repo,
            issue=tracking_issue,
            policy=policy,
            prompter=prompter,
            runner=runner,
            unattended=unattended,
        )
    return EmitResult(created_issues=created, tracking_updated=tracking_updated, skipped=skipped)
