"""Parakeet v3 transcription (NVIDIA, 25 languages including French and English) for LiveKit, via the voice server.

The dictation model (POST /transcribe on the voice server, already loaded, ~0.2-0.5 s per sentence): no second
copy in memory. With "Listen only to my voice", the voice is checked first (voice_id.Gate): another
voice is not transcribed at all. No streaming: LiveKit's VAD cuts each sentence (StreamAdapter), then
the whole sentence is transcribed in one block. No live partial text, so no early execution (fast_path.Early).
"""

from __future__ import annotations

import asyncio

import aiohttp
import numpy as np
from livekit import rtc
from livekit.agents import APIConnectionError, stt
from livekit.agents.language import LanguageCode
from livekit.agents.types import NOT_GIVEN, APIConnectOptions, NotGivenOr
from livekit.agents.utils import AudioBuffer

import voice_id
from models_mlx import TTS_URL

# French hints to label the sentence (the end-of-turn detector and the logs use it).
FRENCH_HINTS = {"le", "la", "les", "une", "des", "est", "et", "à", "ouvre", "mets", "ferme", "fenêtre", "je", "tu",
                "moi", "sur", "dans", "pour", "pas", "c'est", "qu'est-ce", "quelle", "quel"}


def guess_language(text: str) -> LanguageCode:
    words = set(text.lower().replace("’", "'").replace(",", " ").replace(".", " ").split())
    return LanguageCode("fr" if len(words & FRENCH_HINTS) >= 1 or any(c in text for c in "éèêàçù") else "en")


class ParakeetSTT(stt.STT):
    def __init__(self, language: str = "latin", gate: voice_id.Gate | None = None) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        # latin: Parakeet guesses the language; a Cyrillic output (short sound) is sent on to Whisper.
        self.language = language
        self.gate = gate
        self.config = ("parakeet", language)

    @property
    def model(self) -> str:
        return "parakeet-v3"

    @property
    def provider(self) -> str:
        return "parakeet-local"

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        frame = rtc.combine_audio_frames(buffer)
        pcm = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        if frame.num_channels > 1:
            pcm = pcm.reshape(-1, frame.num_channels).mean(axis=1)
        if frame.sample_rate != 16000:
            positions = np.arange(0, len(pcm), frame.sample_rate / 16000)
            pcm = np.interp(positions, np.arange(len(pcm)), pcm).astype(np.float32)
        if self.gate and not await asyncio.to_thread(self.gate.accepts, pcm):
            return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                                   alternatives=[stt.SpeechData(language=LanguageCode("en"), text="")])
        try:
            async with aiohttp.ClientSession() as session, session.post(
                    f"{TTS_URL}/transcribe", params={"language": self.language}, data=pcm.astype(np.float32).tobytes(),
                    timeout=aiohttp.ClientTimeout(total=conn_options.timeout + 20)) as resp:
                body = await resp.json()
        except Exception as e:
            raise APIConnectionError(f"serveur de voix injoignable ({TTS_URL}) : {e}") from e
        text = (body.get("text") or "").strip()
        return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                               alternatives=[stt.SpeechData(language=guess_language(text), text=text)])
