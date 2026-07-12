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
- **The single Engine call:** required — synthesize the deterministically
  assembled briefing plus the operator's captured answers into the plan's
  task decomposition (`plan.synthesize`, strict-JSON `{"tasks": [...]}`). The
  *human* fixes the objective, guardrails, and target repos; the model only
  decomposes that objective into fleet-ready task-issues. Against `NoopEngine`
  or on an empty/unparsable reply it degrades to a single placeholder task
  echoing the objective — never fabricates a different objective.
- **Invariants / rules:**
  - Interactive / operator-run — **never** unattended-dispatched by the fleet.
    The interview is a pure function over an injectable `Prompter`, so the loop
    is TTY-free testable.
  - Reads other tools' state only via the on-disk `Ledger` + `gh` (MyPlanner's
    latest plan through the read-only ledger seam MyTodo uses) — **no package
    dependency on any other tool**; runtime dep is `my-things-core` only.
  - Every mutation goes through `Policy`: writing the local artifact + one
    `kind=session_plan` ledger entry is `ALLOW`; creating issues and editing the
    org tracking issue are `ASK`-gated (answered in the terminal; an unattended
    run degrades `ASK`→`DENY`, so nothing public is created without a human).
    **Never merges, never opens a PR.**
  - Writes exactly one `session_plan` ledger entry + one artifact per session.
- **Backlog label:** `my-director`.
