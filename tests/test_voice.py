from __future__ import annotations

# stdlib `array`, deliberately not numpy: the speech deps are the optional
# [voice] extra and CI installs only [dev], so a test that imports numpy is
# testing the developer's machine. listen() only takes len() of the buffer,
# so anything sized stands in for a recording.
from array import array

import pytest

from mydirector.interview import ScriptedPrompter
from mydirector.mission import scripted_contract
from mydirector.voice import (
    KokoroMouth,
    PushToTalkEar,
    SilentMouth,
    VoicePrompter,
    hears_yes,
)


class _Ear:
    # The system boundary: everything above this is ours, everything below is
    # the STT library and a microphone.
    def __init__(self, *heard: str) -> None:
        self._heard = list(heard)

    def listen(self) -> str:
        return self._heard.pop(0) if self._heard else ""


class _Mouth:
    def __init__(self) -> None:
        self.said: list[str] = []

    def speak(self, text: str) -> None:
        self.said.append(text)


def _prompter(*heard: str, typed: list[str] | None = None, confirms=None) -> VoicePrompter:
    return VoicePrompter(
        ear=_Ear(*heard),
        mouth=_Mouth(),
        typed=ScriptedPrompter(typed or [], confirms=confirms or []),
        echo=lambda *a: None,
    )


# ---- yes / no parsing -----------------------------------------------------


@pytest.mark.parametrize("said", ["yes", "Yeah.", "ok", "sure", "sì", "certo"])
def test_affirmatives_are_heard_as_yes(said: str) -> None:
    assert hears_yes(said) is True


@pytest.mark.parametrize("said", ["no", "Nope!", "cancel", "stop"])
def test_negatives_are_heard_as_no(said: str) -> None:
    assert hears_yes(said) is False


@pytest.mark.parametrize("said", ["maybe later", "", "I think the merge gate is broken"])
def test_anything_ambiguous_is_neither(said: str) -> None:
    # Never guess: a misheard confirmation binds a contract the operator did
    # not agree to.
    assert hears_yes(said) is None


# ---- the prompter ---------------------------------------------------------


def test_spoken_answer_is_used_when_accepted() -> None:
    # Empty typed reply == pressing enter, which accepts the transcript.
    prompter = _prompter("fix the heartbeat", typed=[""])
    assert prompter.ask("What is the objective?") == "fix the heartbeat"


def test_operator_can_correct_a_misheard_transcript() -> None:
    prompter = _prompter("fix the art beat", typed=["fix the heartbeat"])
    assert prompter.ask("What is the objective?") == "fix the heartbeat"


def test_silence_falls_back_to_typing() -> None:
    prompter = _prompter("", typed=["typed instead"])
    assert prompter.ask("What is the objective?") == "typed instead"


def test_question_is_spoken_aloud() -> None:
    mouth = _Mouth()
    VoicePrompter(
        ear=_Ear("something"), mouth=mouth, typed=ScriptedPrompter([""]), echo=lambda *a: None
    ).ask("What is the objective?")
    assert mouth.said == ["What is the objective?"]


def test_confirm_reads_a_spoken_yes() -> None:
    assert _prompter("yes").confirm("Accept this?") is True


def test_confirm_reads_a_spoken_no() -> None:
    assert _prompter("no").confirm("Accept this?") is False


def test_ambiguous_confirmation_falls_back_to_typing() -> None:
    prompter = _prompter("I'm not sure", confirms=[True])
    assert prompter.confirm("Accept this?") is True


def test_default_survives_an_empty_answer() -> None:
    prompter = _prompter("", typed=[""])
    assert prompter.ask("Horizon?", default="next") == "next"


# ---- it is a real Prompter ------------------------------------------------


def test_voice_prompter_drives_the_scripted_contract_unchanged() -> None:
    # The seam's whole point: the interview cannot tell speech from typing.
    prompter = _prompter(
        "make the heartbeat alert",
        "it is silently dead",
        "MyThingsLab/my-fleet",
        "no force push",
        "$ pytest tests/test_heartbeat.py",
        "",
        typed=[""] * 6,
    )
    contract = scripted_contract(prompter)
    assert contract.objective == "make the heartbeat alert"
    assert contract.repos == ("MyThingsLab/my-fleet",)
    assert contract.done_when == ("$ pytest tests/test_heartbeat.py",)


# ---- degradation ----------------------------------------------------------


def test_silent_mouth_is_a_no_op() -> None:
    assert SilentMouth().speak("anything") is None


def test_broken_tts_never_blocks_the_interview() -> None:
    # Speech is an output convenience; the question is always on screen too.
    said: list[str] = []
    mouth = KokoroMouth(echo=said.append)
    mouth.speak("hello")  # kokoro is not installed -> handled, not raised
    assert mouth._broken
    assert "speech unavailable" in said[0]

    said.clear()
    mouth.speak("again")
    assert said == []  # already known broken, stays quiet


def test_tts_ignores_empty_text() -> None:
    said: list[str] = []
    KokoroMouth(echo=said.append).speak("   ")
    assert said == []


def test_empty_recording_is_never_sent_to_the_model() -> None:
    # A stray enter-enter must come back as silence, not as whatever the model
    # hallucinates from an empty buffer.
    transcribed: list[object] = []

    class _Model:
        def transcribe(self, audio, **kw):
            transcribed.append(audio)
            return [], None

    ear = PushToTalkEar(echo=lambda *a: None, reader=lambda *a: "")
    ear._model = _Model()
    ear._record = lambda: array("f", [])

    assert ear.listen() == ""
    assert transcribed == []


def test_missing_dependencies_are_reported_before_the_first_question() -> None:
    # Regression: the speech imports are lazy, so `import mydirector.voice`
    # succeeds even with nothing installed. Without this preflight the failure
    # surfaced inside the first listen() as a traceback, mid-interview.
    from mydirector.voice import unavailable

    reason = unavailable()
    assert reason == "" or "pip install 'my-director[voice]'" in reason


def test_a_microphone_that_fails_costs_a_question_not_the_session() -> None:
    said: list[str] = []
    ear = PushToTalkEar(echo=said.append, reader=lambda *a: "")

    def _boom():
        raise OSError("no default input device")

    ear._load = _boom
    assert ear.listen() == ""
    assert "microphone unavailable" in said[0]

    # And silence is what makes VoicePrompter hand over to typing.
    prompter = VoicePrompter(
        ear=ear, typed=ScriptedPrompter(["typed instead"]), echo=lambda *a: None
    )
    assert prompter.ask("Objective?") == "typed instead"


def test_recorded_audio_is_transcribed_and_joined() -> None:
    class _Segment:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Model:
        def transcribe(self, audio, **kw):
            return [_Segment(" fix the "), _Segment(" heartbeat ")], None

    ear = PushToTalkEar(echo=lambda *a: None, reader=lambda *a: "")
    ear._model = _Model()
    ear._record = lambda: array("f", [1.0]) * 16_000

    assert ear.listen() == "fix the heartbeat"
