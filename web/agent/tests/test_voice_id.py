""""Listen only to my voice": another voice is dropped silently, before transcription (Parakeet) or before
its text is shown (live transcription). Short word right after the owner accepted. No model and no
keychain: the score is simulated."""

import asyncio
import time

import numpy as np
import pytest
from livekit import rtc
from livekit.agents import stt

import agent
import stt_parakeet
import voice_id

RATE = voice_id.RATE


def tone(seconds: float) -> np.ndarray:
    return (np.sin(np.arange(int(seconds * RATE)) * 2 * np.pi * 220 / RATE) * 0.4).astype(np.float32)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setattr(voice_id, "settings_for", lambda product: (True, 0.39))
    monkeypatch.setattr("settings.load_settings", lambda: {"voice_id": {"short_grace": 20}})


def test_settings_follow_product_and_enrollment(monkeypatch):
    monkeypatch.setattr("settings.load_settings", lambda: {"voice_id": {"command": True, "threshold": 0.4}})
    monkeypatch.setattr(voice_id, "load_print", lambda: np.ones(256, dtype=np.float32))
    assert voice_id.settings_for("command") == (True, 0.4)
    assert voice_id.settings_for("assistant") == (False, 0.4)
    monkeypatch.setattr(voice_id, "load_print", lambda: None)   # no voiceprint: never any verification
    assert voice_id.settings_for("command")[0] is False


def test_gate(monkeypatch, on):
    gate = voice_id.Gate("command")
    monkeypatch.setattr(voice_id, "score", lambda pcm: 0.12)
    assert gate.accepts(tone(2.0)) is False                 # other voice
    monkeypatch.setattr(voice_id, "score", lambda pcm: 0.61)
    assert gate.accepts(tone(2.0)) is True and gate.owner_at > 0
    monkeypatch.setattr(voice_id, "score", lambda pcm: 0.30)
    assert gate.accepts(tone(0.5)) is True                  # "Stop" right after a sentence from the owner
    gate.owner_at = time.monotonic() - 60
    assert gate.accepts(tone(0.5)) is False                 # too long after
    monkeypatch.setattr(voice_id, "settings_for", lambda product: (False, 0.39))
    monkeypatch.setattr(voice_id, "score", lambda pcm: pytest.fail("ne doit pas calculer"))
    assert gate.accepts(tone(2.0)) is True                  # disabled: nothing is judged


def test_parakeet_does_not_transcribe_another_voice(monkeypatch, on):
    monkeypatch.setattr(voice_id, "score", lambda pcm: 0.1)
    model = stt_parakeet.ParakeetSTT(gate=voice_id.Gate("command"))
    pcm = (tone(1.5) * 32767).astype(np.int16)
    frame = rtc.AudioFrame(pcm.tobytes(), RATE, 1, len(pcm))

    class NoServer:   # the slightest request to the voice server would fail the test
        def __init__(self, *a, **k):
            pytest.fail("autre voix envoyée à la transcription")

    monkeypatch.setattr(stt_parakeet.aiohttp, "ClientSession", NoServer)
    event = asyncio.run(model._recognize_impl([frame], conn_options=stt_parakeet.APIConnectOptions()))
    assert event.alternatives[0].text == ""


def test_live_text_held_then_dropped_for_another_voice(monkeypatch, on):
    events = [stt.SpeechEvent(type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                              alternatives=[stt.SpeechData(language="en", text="open")]),
              stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                              alternatives=[stt.SpeechData(language="en", text="open chrome")])]

    async def fake_default(agent_self, audio, settings):
        async for _ in audio:
            pass
        for e in events:
            yield e

    monkeypatch.setattr(agent.Agent.default, "stt_node", fake_default)
    pcm = (tone(1.0) * 32767).astype(np.int16)

    async def audio():
        yield rtc.AudioFrame(pcm.tobytes(), RATE, 1, len(pcm))

    def run(similarity: float) -> list:
        monkeypatch.setattr(voice_id, "score", lambda p: similarity)
        a = agent.Assistant.__new__(agent.Assistant)   # without a LiveKit session
        a.gate, a.gate_stream = voice_id.Gate("assistant"), True
        a._heard, a._heard_samples = agent.deque(), 0

        async def collect():
            return [e async for e in a.stt_node(audio(), None)]

        return asyncio.run(collect())

    assert run(0.1) == []                                   # other voice: no live text, no sentence
    kept = run(0.7)
    assert [e.type for e in kept] == [stt.SpeechEventType.FINAL_TRANSCRIPT]   # owner: the sentence, at the end


def test_trim_drops_silence():
    audio = np.concatenate([np.zeros(RATE), np.random.default_rng(0).normal(0, 0.3, RATE), np.zeros(RATE)])
    assert 0.9 * RATE < len(voice_id.trim(audio.astype(np.float32))) < 1.4 * RATE
    assert len(voice_id.trim(np.zeros(RATE, dtype=np.float32))) == 0
