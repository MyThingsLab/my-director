from __future__ import annotations

from typing import Protocol, runtime_checkable

from mydirector.interview import ConsolePrompter, Prompter

SAMPLE_RATE = 16_000
# The laptop's DMIC already captures at 16 kHz, which is what both Whisper and
# Parakeet want, so nothing is resampled on the way in.

_YES = frozenset({"yes", "yeah", "yep", "sure", "ok", "okay", "confirm", "sì", "si", "certo"})
_NO = frozenset({"no", "nope", "nah", "cancel", "stop", "negative"})


def unavailable() -> str:
    # Checked once, up front. The speech imports are lazy so `import
    # mydirector.voice` always succeeds -- which means a missing dependency
    # would otherwise surface inside the first `listen()`, halfway through an
    # interview, as a traceback. Discovering it before the first question costs
    # nothing and degrades to typing instead.
    missing = []
    for module, package in (
        ("faster_whisper", "faster-whisper"),
        ("sounddevice", "sounddevice"),
        ("numpy", "numpy"),
    ):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        return f"missing {', '.join(missing)} — install with: pip install 'my-director[voice]'"
    return ""


@runtime_checkable
class Ear(Protocol):
    def listen(self) -> str: ...


@runtime_checkable
class Mouth(Protocol):
    def speak(self, text: str) -> None: ...


class SilentMouth:
    def speak(self, text: str) -> None:
        return None


def hears_yes(text: str) -> bool | None:
    # None means "that was not a yes or a no" -- the caller re-asks rather than
    # guessing, because a misheard confirmation is how a contract binds to
    # something the operator never agreed to.
    words = {w.strip(".,!?").lower() for w in text.split()}
    if words & _YES:
        return True
    if words & _NO:
        return False
    return None


class VoicePrompter:
    # A third Prompter beside ConsolePrompter and ScriptedPrompter, so nothing
    # in the interview loop knows whether a question was typed or spoken.
    #
    # Every transcript is shown and editable before it is used: an STT slip in
    # a done_when line would silently change what the mission is graded against,
    # which is far more expensive than the keystroke it costs to confirm.
    def __init__(
        self,
        *,
        ear: Ear,
        mouth: Mouth | None = None,
        typed: Prompter | None = None,
        echo=print,
    ) -> None:
        self._ear = ear
        self._mouth = mouth or SilentMouth()
        self._typed = typed or ConsolePrompter()
        self._echo = echo

    def ask(self, prompt: str, *, default: str = "") -> str:
        self._echo(f"\n{prompt}")
        self._mouth.speak(prompt)
        heard = self._ear.listen().strip()
        if not heard:
            self._echo("  (heard nothing — type it instead)")
            return self._typed.ask(prompt, default=default)

        self._echo(f'  heard: "{heard}"')
        correction = self._typed.ask("  enter to accept, or type a correction:", default=heard)
        return correction or default

    def confirm(self, prompt: str) -> bool:
        self._echo(f"\n{prompt}")
        self._mouth.speak(prompt)
        verdict = hears_yes(self._ear.listen())
        if verdict is None:
            self._echo("  (not a clear yes or no)")
            return self._typed.confirm(prompt)
        self._echo(f"  heard: {'yes' if verdict else 'no'}")
        return verdict


class PushToTalkEar:
    # Push-to-talk, never always-listening: no wake word, no hot mic, and the
    # operator decides when anything is captured at all. Enter starts, Enter
    # stops -- which needs no raw terminal mode and so survives an SSH session.
    def __init__(
        self,
        *,
        model: str = "large-v3-turbo",
        language: str | None = None,
        device: str = "auto",
        echo=print,
        reader=input,
    ) -> None:
        self._model_name = model
        self._language = language
        self._device = device
        self._echo = echo
        self._reader = reader
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            device = self._device
            if device == "auto":
                try:
                    import torch

                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"
            compute = "float16" if device == "cuda" else "int8"
            self._echo(f"  (loading {self._model_name} on {device}…)")
            self._model = WhisperModel(self._model_name, device=device, compute_type=compute)
        return self._model

    def _record(self):
        import numpy as np
        import sounddevice as sd

        frames: list = []
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=lambda indata, *_: frames.append(indata.copy()),
        ):
            self._reader()
        return np.concatenate(frames).flatten() if frames else np.zeros(0, dtype="float32")

    def listen(self) -> str:
        # Any failure here returns silence rather than raising, and VoicePrompter
        # reads silence as "type it instead" -- so a mic that disappears mid
        # interview costs a question, not the session.
        try:
            model = self._load()
            self._echo("  [enter] to start talking…")
            self._reader()
            self._echo("  recording — [enter] to stop")
            audio = self._record()
            if len(audio) == 0:
                return ""
            segments, _ = model.transcribe(audio, language=self._language, vad_filter=True)
            return " ".join(segment.text.strip() for segment in segments).strip()
        except (ImportError, OSError, RuntimeError) as exc:
            self._echo(f"  (microphone unavailable: {exc})")
            return ""


class KokoroMouth:
    # Kokoro-82M: Apache-2.0, 24 kHz, and small enough that a question is spoken
    # effectively instantly. Piper was archived in October 2025 and is not an
    # option. Speech failing must never block the interview -- it is an output
    # convenience, and the question is always on screen too.
    def __init__(self, *, voice: str = "af_heart", speed: float = 1.0, echo=print) -> None:
        self._voice = voice
        self._speed = speed
        self._echo = echo
        self._pipeline = None
        self._broken = False

    def _load(self):
        if self._pipeline is None:
            from kokoro import KPipeline

            self._pipeline = KPipeline(lang_code=self._voice[0])
        return self._pipeline

    def speak(self, text: str) -> None:
        if self._broken or not text.strip():
            return
        try:
            import sounddevice as sd

            pipeline = self._load()
            for _, _, audio in pipeline(text, voice=self._voice, speed=self._speed):
                sd.play(audio, 24_000)
                sd.wait()
        except Exception as exc:  # noqa: BLE001 -- see the class comment
            self._broken = True
            self._echo(f"  (speech unavailable: {exc}; questions stay on screen)")


def build_prompter(
    *, model: str = "large-v3-turbo", voice: str = "af_heart", language: str | None = None
) -> VoicePrompter:
    return VoicePrompter(
        ear=PushToTalkEar(model=model, language=language),
        mouth=KokoroMouth(voice=voice),
    )
