from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from mydirector.sources import Briefing


@runtime_checkable
class Prompter(Protocol):
    # The one seam that makes an interactive tool testable: production reads a
    # TTY, tests inject scripted answers. `ask` returns free text (falling back
    # to `default` on an empty reply); `confirm` gates a side effect y/N.
    def ask(self, prompt: str, *, default: str = "") -> str: ...

    def confirm(self, prompt: str) -> bool: ...


class ConsolePrompter:
    def ask(self, prompt: str, *, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        reply = input(f"{prompt}{suffix}\n> ").strip()
        return reply or default

    def confirm(self, prompt: str) -> bool:
        return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


class ScriptedPrompter:
    # Test double: replays a fixed list of `ask` answers and `confirm` booleans.
    def __init__(self, answers: list[str], confirms: list[bool] | None = None) -> None:
        self._answers = list(answers)
        self._confirms = list(confirms or [])

    def ask(self, prompt: str, *, default: str = "") -> str:
        reply = self._answers.pop(0) if self._answers else ""
        return reply or default

    def confirm(self, prompt: str) -> bool:
        return self._confirms.pop(0) if self._confirms else False


@dataclass(frozen=True)
class DirectorAnswers:
    shipped: str
    blocked: str
    objective: str
    why: str
    horizon: str
    repos: list[str]
    guardrails: list[str]
    task_hints: str = ""


def _split(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def conduct(briefing: Briefing, prompter: Prompter) -> DirectorAnswers:
    # A fixed director interview: every prompt is seeded from the assembled
    # briefing so the operator edits recalled state rather than reconstructing
    # it. No model call here — this only captures the human's decision.
    print(briefing.summary())
    print()
    shipped = prompter.ask(
        "What shipped or moved forward today?", default=briefing.recent_headline()
    )
    blocked = prompter.ask(
        "What is blocked or needs a human before it can proceed?",
        default=briefing.blocked_headline(),
    )
    objective = prompter.ask(
        "The ONE most critical objective for the next session (the highest-leverage "
        "thing to improve the whole ecosystem):"
    )
    why = prompter.ask("Why is that the highest-leverage thing to do now?")
    horizon = prompter.ask("Horizon — next / soon / later?", default="next").lower()
    repos = _split(prompter.ask("Which repo(s) does it touch? (comma-separated)"))
    guardrails = _split(
        prompter.ask("Guardrails the workers must respect? (comma-separated, optional)")
    )
    task_hints = prompter.ask(
        "Any specific tasks you already have in mind? (optional; the synthesis will "
        "decompose the objective either way)"
    )
    return DirectorAnswers(
        shipped=shipped,
        blocked=blocked,
        objective=objective,
        why=why,
        horizon=horizon,
        repos=repos,
        guardrails=guardrails,
        task_hints=task_hints,
    )
