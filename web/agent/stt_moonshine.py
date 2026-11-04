"""Moonshine transcription (English) for LiveKit.

In streaming mode (default), Moonshine transcribes while the user speaks: live partial text,
and the final sentence arrives right after the end of speech detected by its built-in VAD.
Without streaming, the utterance cut by LiveKit's VAD is transcribed in one block.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from livekit import rtc
from livekit.agents import stt
from livekit.agents.language import LanguageCode
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions, NotGivenOr
from livekit.agents.utils import AudioBuffer
from moonshine_voice import ModelArch, Transcriber
from moonshine_voice.download import get_model_for_language
from moonshine_voice.transcriber import LineCompleted, LineTextChanged

ARCHS = {
    "tiny": ModelArch.TINY_STREAMING,
    "small": ModelArch.SMALL_STREAMING,
    "medium": ModelArch.MEDIUM_STREAMING,
}
# Audio pushed to Moonshine in 100 ms packets rather than at every 10-20 ms frame.
PUSH_SAMPLES = 1600
EN = LanguageCode("en")


def _speech(kind: stt.SpeechEventType, text: str) -> stt.SpeechEvent:
    return stt.SpeechEvent(type=kind, alternatives=[stt.SpeechData(language=EN, text=text)])


class MoonshineSTT(stt.STT):
    def __init__(self, size: str = "small", streaming: bool = True, vad_window: float = 0.3,
                 update_interval: float = 0.3, keyterms: tuple[str, ...] = ()) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=streaming, interim_results=streaming))
        self.size = size
        self.config = (size, streaming, vad_window, update_interval, tuple(keyterms))
        self._update_interval = update_interval
        path, arch = get_model_for_language("en", ARCHS[size])
        self._transcriber = Transcriber(path, arch, options={"vad_window_duration": str(vad_window)})
        if keyterms:
            self._transcriber.set_keyterms(list(keyterms))
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return f"moonshine-{self.size}"

    @property
    def provider(self) -> str:
        return "moonshine-local"

    def _transcribe(self, pcm16k: np.ndarray) -> str:
        with self._lock:
            transcript = self._transcriber.transcribe_without_streaming(pcm16k.tolist(), 16000)
        return " ".join(line.text.strip() for line in transcript.lines).strip()

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
        text = await asyncio.to_thread(self._transcribe, pcm)
        return _speech(stt.SpeechEventType.FINAL_TRANSCRIPT, text)

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.RecognizeStream:
        return MoonshineStream(self, conn_options)


class MoonshineStream(stt.RecognizeStream):
    def __init__(self, moonshine: MoonshineSTT, conn_options: APIConnectOptions) -> None:
        super().__init__(stt=moonshine, conn_options=conn_options, sample_rate=16000)
        self._moonshine = moonshine
        # One thread per stream: audio reaches Moonshine in order, without blocking the asyncio loop.
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="moonshine")

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        stream = self._moonshine._transcriber.create_stream(update_interval=self._moonshine._update_interval)

        def on_event(event) -> None:
            # Called by Moonshine in the stream's thread.
            if isinstance(event, LineCompleted):
                text = event.line.text.strip()
                if text:
                    loop.call_soon_threadsafe(
                        self._event_ch.send_nowait, _speech(stt.SpeechEventType.FINAL_TRANSCRIPT, text))
            elif isinstance(event, LineTextChanged):
                text = event.line.text.strip()
                if text:
                    loop.call_soon_threadsafe(
                        self._event_ch.send_nowait, _speech(stt.SpeechEventType.INTERIM_TRANSCRIPT, text))

        stream.add_listener(on_event)
        await loop.run_in_executor(self._worker, stream.start)
        pending: list[np.ndarray] = []
        pending_len = 0
        try:
            async for item in self._input_ch:
                if isinstance(item, self._FlushSentinel):
                    if pending:
                        chunk = np.concatenate(pending).tolist()
                        pending, pending_len = [], 0
                        await loop.run_in_executor(self._worker, stream.add_audio, chunk, 16000)
                    continue
                pcm = np.frombuffer(item.data, dtype=np.int16).astype(np.float32) / 32768.0
                if item.num_channels > 1:
                    pcm = pcm.reshape(-1, item.num_channels).mean(axis=1)
                pending.append(pcm)
                pending_len += len(pcm)
                if pending_len >= PUSH_SAMPLES:
                    chunk = np.concatenate(pending).tolist()
                    pending, pending_len = [], 0
                    await loop.run_in_executor(self._worker, stream.add_audio, chunk, 16000)
        finally:
            await loop.run_in_executor(self._worker, stream.stop)
            self._worker.shutdown(wait=False)
