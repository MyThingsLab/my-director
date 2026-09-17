from __future__ import annotations

import argparse
import json
from pathlib import Path

from mythings.engine import build_engine_from_args
from mythings.ledger import Ledger
from mythings.session import Outcome

from mydirector import emit, escalate, mission, sources
from mydirector.converse import RipgrepSearcher, converse
from mydirector.emit import DefaultPolicy, is_stale, render_markdown, render_stale_notice
from mydirector.interview import ConsolePrompter, Prompter, conduct
from mydirector.plan import SessionPlan, synthesize

_ENGINE_NAMES = ("noop", "claude-cli")


def _open_counts(raw: list[str] | None) -> dict[str, int]:
    # --open-count repo=N, purely advisory context for the briefing.
    counts: dict[str, int] = {}
    for item in raw or []:
        repo, _, n = item.partition("=")
        if repo and n.isdigit():
            counts[repo] = int(n)
    return counts


def _run_session(args: argparse.Namespace, prompter: Prompter) -> int:
    ledger = Ledger(args.ledger)
    planner_ledger = Ledger(args.planner_ledger) if args.planner_ledger else None
    briefing = sources.assemble(
        ledger=ledger,
        planner_ledger=planner_ledger,
        open_counts=_open_counts(args.open_count),
    )
    answers = conduct(briefing, prompter)
    if not answers.objective.strip():
        print("no objective given — nothing to plan (aborted)")
        return 1

    engine = build_engine_from_args(args)
    plan = synthesize(answers, briefing.summary(), engine)

    print()
    print(render_markdown(plan))

    result = emit.emit(
        plan,
        ledger=ledger,
        artifact_dir=args.artifact_dir,
        execute=args.execute,
        policy=DefaultPolicy(),
        prompter=prompter,
        tracking_repo=args.tracking_repo,
        tracking_issue=args.tracking_issue,
    )
    print()
    print(f"artifact + ledger written under {args.artifact_dir}")
    if args.execute:
        for url in result.created_issues:
            print(f"  created issue: {url}")
        for s in result.skipped:
            print(f"  skipped: {s}")
        if result.tracking_updated:
            print(f"  tracking issue updated: {args.tracking_repo}#{args.tracking_issue}")
    else:
        print("  (dry run — pass --execute to create issues / update the tracking issue)")
    return 0


def _voice_prompter(args: argparse.Namespace, fallback: Prompter) -> Prompter:
    if not getattr(args, "voice", False):
        return fallback
    from mydirector.voice import build_prompter, unavailable

    if reason := unavailable():
        print(f"voice unavailable: {reason}")
        print("falling back to the typed interview")
        return fallback
    return build_prompter(
        model=args.voice_model, voice=args.voice_name, language=args.voice_language
    )


def _run_mission_open(args: argparse.Namespace, prompter: Prompter) -> int:
    prompter = _voice_prompter(args, prompter)
    ledger = Ledger(args.ledger)
    if args.objective:
        contract = mission.MissionContract(
            objective=args.objective,
            why=args.why or "",
            repos=tuple(args.repo or ()),
            done_when=tuple(args.done_when or ()),
        ).with_id()
    else:
        briefing = sources.assemble(ledger=ledger, open_counts={}).summary()
        contract = converse(
            briefing=briefing,
            prompter=prompter,
            engine=build_engine_from_args(args),
            searcher=RipgrepSearcher(args.root),
            max_turns=args.max_turns,
        )

    print()
    print(f"checking {len(contract.executable_criteria)} criteria before any work starts…")
    result = mission.open_mission(
        contract,
        cwd=args.root,
        ledger=ledger,
        allow_unverifiable=args.allow_unverifiable,
    )
    print()
    print(mission.render(result.contract, result.checks))
    print()
    if result.refused:
        print(f"MISSION REFUSED — {result.refused}")
        return 1
    print(f"mission open: {result.contract.mission_id}")
    return 0


def _run_mission_close(args: argparse.Namespace) -> int:
    result = mission.close_mission(
        cwd=args.root, ledger=Ledger(args.ledger), mission_id=args.mission_id or ""
    )
    if result is None:
        print("no open mission recorded")
        return 1
    print(mission.render_verdict(result))
    return 0 if result.verdict.outcome is Outcome.ACCEPTED else 1


def _run_show(args: argparse.Namespace) -> int:
    ledger = Ledger(args.ledger)
    plans = ledger.read(tool=emit.TOOL, kind=emit.LEDGER_KIND)
    if not plans:
        print("no session plan recorded yet")
        return 1
    plan = SessionPlan.from_dict(plans[-1].data)
    if args.json:
        print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_stale_notice(plan) if is_stale(plan) else render_markdown(plan))
    return 0


def _run_escalate(args: argparse.Namespace) -> int:
    ledger = Ledger(args.ledger)
    dispatch_ledger = Ledger(args.dispatch_ledger)
    blockers = escalate.unescalated_blockers(dispatch_ledger=dispatch_ledger, ledger=ledger)
    if not blockers:
        print("no new needs_human blocker(s)")
        return 0

    pushed = 0
    for blocker in blockers:
        if escalate.push_blocker(blocker, bot_ledger=args.bot_ledger):
            escalate.record_escalated(ledger, blocker)
            pushed += 1
    print(f"{len(blockers)} new blocker(s), {pushed} pushed successfully")
    return 0 if pushed == len(blockers) else 1


def main(argv: list[str] | None = None, *, prompter: Prompter | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mydirector",
        description="End-of-day director session: decide the next session's one critical "
        "objective with a human, decompose it into fleet task-issues.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    session = sub.add_parser("session", help="run the interactive director interview")
    session.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    session.add_argument(
        "--planner-ledger", type=Path, help="MyPlanner's ledger, to surface its latest plan"
    )
    session.add_argument(
        "--artifact-dir", type=Path, default=Path(".mythings"), help="where to write session_plan.*"
    )
    session.add_argument(
        "--open-count", action="append", help="advisory context, repeatable: repo=N"
    )
    session.add_argument("--engine", choices=sorted(_ENGINE_NAMES), default="noop")
    session.add_argument("--engine-model", help="model for --engine claude-cli")
    session.add_argument(
        "--tracking-repo", help='tracking issue repo, e.g. "MyThingsLab/my-things-core"'
    )
    session.add_argument("--tracking-issue", type=int, help="tracking issue number to update")
    session.add_argument(
        "--execute",
        action="store_true",
        help="actually create issues / update the tracking issue (each ASK-gated)",
    )

    mopen = sub.add_parser(
        "mission", help="open or close a mission: a contract with criteria a machine re-runs"
    )
    msub = mopen.add_subparsers(dest="mission_cmd", required=True)

    opener = msub.add_parser("open", help="interview, then prove every criterion is red")
    opener.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    opener.add_argument("--root", type=Path, default=Path("."), help="where criteria are run")
    opener.add_argument("--objective", help="skip the interview and state the objective directly")
    opener.add_argument("--why")
    opener.add_argument("--repo", action="append", help="repeatable")
    opener.add_argument(
        "--done-when", action="append", help="repeatable acceptance criterion; see README"
    )
    opener.add_argument("--engine", choices=sorted(_ENGINE_NAMES), default="noop")
    opener.add_argument("--engine-model", help="model for --engine claude-cli")
    opener.add_argument("--max-turns", type=int, default=14, help="interview turn budget")
    opener.add_argument(
        "--allow-unverifiable",
        action="store_true",
        help="open a mission no machine can grade (every criterion is prose)",
    )
    opener.add_argument(
        "--voice",
        action="store_true",
        help="conduct the interview aloud (push-to-talk; needs the [voice] extra)",
    )
    opener.add_argument("--voice-model", default="large-v3-turbo", help="faster-whisper model")
    opener.add_argument("--voice-name", default="af_heart", help="Kokoro voice, e.g. im_nicola")
    opener.add_argument("--voice-language", help="force a language code, e.g. it")

    closer = msub.add_parser("close", help="re-run the criteria and grade the mission")
    closer.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    closer.add_argument("--root", type=Path, default=Path("."))
    closer.add_argument("--mission-id", help="defaults to the latest open mission")

    show = sub.add_parser("show", help="print the latest recorded session plan")
    show.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    show.add_argument("--json", action="store_true")

    esc = sub.add_parser(
        "escalate",
        help="push any new needs_human blocker (fleet-dispatch#44) to Telegram",
    )
    esc.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    esc.add_argument(
        "--dispatch-ledger", type=Path, required=True, help="fleet_dispatch's own dispatch ledger"
    )
    esc.add_argument(
        "--bot-ledger",
        type=Path,
        required=True,
        help="mytelegrambot's ledger (the push rendezvous)",
    )

    args = parser.parse_args(argv)
    if args.cmd == "show":
        return _run_show(args)
    if args.cmd == "escalate":
        return _run_escalate(args)
    if args.cmd == "mission":
        if args.mission_cmd == "close":
            return _run_mission_close(args)
        return _run_mission_open(args, prompter or ConsolePrompter())
    return _run_session(args, prompter or ConsolePrompter())


if __name__ == "__main__":
    raise SystemExit(main())
