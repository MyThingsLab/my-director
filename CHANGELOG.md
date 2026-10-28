# Changelog

## [Unreleased]
### Added/Changed
- Built the human-in-the-loop director: guided end-of-day interview + one Engine synthesis call into a SessionPlan (objective + task-issues); ledger/artifact always, issue-create + tracking-issue-edit ASK-gated. sources/interview/plan/emit/cli, 23 tests green (92% cov).
- Mechanical migration to mythings.testing: the stateful local FakeGh became fake_gh wiring over the shared FakeGh (create counter + edit->view body kept on gh.body for assertions).

All notable changes to `my-director` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is
[semver](https://semver.org/), per the rules in `RELEASE.md`.

## [1.0.0] - 2026-07-20

First stable release. Baseline of the end-of-day session director as it
already existed: the guided interview, plan decomposition into task-issues,
and the `needs_human` Telegram escalation path (#1/#2/#3). No behavior
changes in this release. Adopts the v1 release contract (`RELEASE.md`) and
pins its own `my-things-core` dependency to `@v1.0.0` instead of floating on
`@main`.
