# my-director — agent instructions

You are developing **my-director**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** the fleet's human-in-the-loop director. An end-of-day interactive
  session that assembles the day's state, walks the operator through a fixed
  director interview (what shipped / what's blocked / the ONE critical objective
  and why / target repos / guardrails), and turns that decision into a structured
  `SessionPlan` — one objective plus ordered task-issues the fleet then executes.
  It keeps a human as general director while many agents run pre-decided,
  structured work.

  The `mission` subcommand is the start-of-work counterpart: a conversational
  interview that ends in a `MissionContract` whose `done_when` criteria a
  machine re-runs. `mission open` proves every criterion is **failing** before
  work starts and refuses the mission otherwise; `mission close` re-runs them
  and grades the red→green transition. A criterion already green at open can
  never yield `ACCEPTED` — it cannot distinguish work that landed from work
  that never happened.
- **The single Engine call:** required — synthesize the deterministically
  assembled briefing plus the operator's captured answers into the plan's
  task decomposition (`plan.synthesize`, strict-JSON `{"tasks": [...]}`). The
  *human* fixes the objective, guardrails, and target repos; the model only
  decomposes that objective into fleet-ready task-issues. Against `NoopEngine`
  or on an empty/unparsable reply it degrades to a single placeholder task
  echoing the objective — never fabricates a different objective.

  **`mission` is the one exception to the single-call rule** (the second in the
  fleet, after my-coder). Its interview runs one call per turn — searching the
  code, asking, and being redirected by the operator is the whole point, and a
  single call cannot do it. `session` is unchanged and still holds to one call.
  The exception is bounded *by construction*, not by the model's restraint:
  `--max-turns` (default 14) and a separate search budget cap it, a move past
  the search budget is ignored rather than obeyed, and the loop is read-only —
  it may inspect the codebase, never change it. Out of budget, or given a reply
  nothing can parse, it falls back to the scripted interview rather than
  leaving the operator in a dead conversation.
- **Invariants / rules:**
  - Interactive / operator-run — **never** unattended-dispatched by the fleet.
    The interview is a pure function over an injectable `Prompter`, so the loop
    is TTY-free testable.
  - The model proposes criteria; the **operator confirms or rewrites them**
    before the contract binds. A mission with no executable criterion is
    refused unless `--allow-unverifiable` is passed explicitly.
  - Reads other tools' state only via the on-disk `Ledger` + `gh` (MyPlanner's
    latest plan through the read-only ledger seam MyTodo uses) — **no package
    dependency on any other tool**; runtime dep is `my-things-core` only.
  - Every mutation goes through `Policy`: writing the local artifact + one
    `kind=session_plan` ledger entry is `ALLOW`; creating issues and editing the
    org tracking issue are `ASK`-gated (answered in the terminal; an unattended
    run degrades `ASK`→`DENY`, so nothing public is created without a human).
    **Never merges, never opens a PR.**
  - Writes exactly one `session_plan` ledger entry + one artifact per session;
    `mission` writes one `mission_open` (including a refusal — provenance
    matters more than a tidy ledger) and one `mission_close` carrying the real
    exit codes from both runs.
- **Backlog label:** `my-director`.
