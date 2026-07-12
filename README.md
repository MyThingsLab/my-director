# my-director

[![CI](https://github.com/MyThingsLab/my-director/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-director/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-director/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-director) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

The fleet's **human-in-the-loop director**. Before the operator goes offline, an
end-of-day session decides *the one most critical thing* the fleet should work on
next — and turns that decision into structured task-issues the autonomous workers
pick up. It is the general-director seat: a human sets direction, then many agents
execute a pre-decided, structured session plan.

Every other fleet tool runs unattended (`myplanner` recommends a sequence,
`myorchestrator` ranks, `fleet_dispatch` spawns workers). `my-director` is the
deliberately interactive one that sits in front of them.

## What a session does

1. **Assembles the day's state** — recent ledger activity, MyPlanner's latest
   recommended sequence (read straight off its ledger, no package dependency),
   and any open-issue counts you pass in. No model call.
2. **Runs a fixed director interview** — what shipped, what's blocked, the ONE
   critical objective and *why*, which repos it touches, and the guardrails the
   workers must respect. Each prompt is seeded from the assembled briefing so you
   edit recalled state rather than reconstruct it.
3. **One Engine call** synthesizes your answers into a `SessionPlan`: the
   objective (yours, verbatim) plus an ordered list of small, independently
   verifiable **task-issues** decomposed by the model. Against `NoopEngine`, or on
   an unusable reply, it degrades to a single placeholder task echoing your
   objective — plumbing only, never a fabricated plan.
4. **Emits the plan.** Always: a `kind=session_plan` ledger entry + a local
   `session_plan.md`/`.json` artifact the next fleet session reads. With
   `--execute` (and your confirmation): one labeled GitHub issue per task, and a
   `## Next session — critical objective` section on the org tracking issue. Both
   public mutations are `ASK`-gated through `Policy`; an unattended run degrades
   `ASK`→`DENY`, so nothing public is ever created without a human. **It never
   opens a PR and never merges.**

## Usage

```bash
# Interactive dry run — decide the plan, write the artifact + ledger, touch no GitHub.
mydirector session --engine noop

# Real synthesis, and actually file the issues + update the tracking issue (each ASK-gated).
mydirector session \
  --engine claude-cli \
  --planner-ledger ../my-planner/.mythings/ledger.jsonl \
  --tracking-repo MyThingsLab/my-things-core --tracking-issue 1 \
  --execute

# Re-read the last decided plan (e.g. at the start of the next session).
mydirector show
```

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../my-things-core -e ".[dev]"
pytest
```

See [`CLAUDE.md`](CLAUDE.md) for the tool's contract and [`HARNESS.md`](HARNESS.md)
for the shared build rules.

## License

MIT — see [`LICENSE`](LICENSE).
