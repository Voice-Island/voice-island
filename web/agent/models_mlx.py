"""Local voices via MLX (Kokoro, Qwen3-TTS, Chatterbox Turbo) for LiveKit, with emotions.

In automatic emotion mode, LiveKit teaches the LLM to write before each sentence
<expr type="expression" label="gently warm"/> ("inworld" format of the expressive pipeline).
Here we read these tags: the label becomes the Qwen3-TTS instruction, breaks
become silence, the rest is removed from the spoken text.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import aiohttp
from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.utils import http_context, shortuuid

from settings import load_settings

logger = logging.getLogger("voice.tts")

SAMPLE_RATE = 24000
TTS_URL = os.environ.get("TTS_BASE_URL", "http://127.0.0.1:8090")
# Audio lead before playing Qwen3-TTS: 0.8 s of 16-bit mono PCM.
PREBUFFER_BYTES = int(SAMPLE_RATE * 0.8) * 2

ENGINES = {
    "kokoro": "prince-canuma/Kokoro-82M",
    "qwen3-0.6b": "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit",
    "qwen3-1.7b": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
    "chatterbox-turbo": "mlx-community/chatterbox-turbo-8bit",
}

# MLX keeps per-thread state: all of the voice's MLX calls go through this single thread.
MLX_THREAD = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx-tts")
_models: dict[str, object] = {}

_EXPR_RE = re.compile(r"<expr\b([^>]*?)/?>|</expr\s*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_BREAK_MS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s)?", re.IGNORECASE)
# Small models often write the tone in brackets: [calm and gentle], [sigh].
BRACKET_RE = re.compile(r"\[([^\[\]\n]{2,80})\]")
SOUNDS = {"laugh", "laughs", "laughing", "sigh", "sighs", "breathe", "breath", "cough", "yawn",
          "clear throat", "chuckle", "chuckles"}


def supports_instruct(engine: str) -> bool:
    return engine.startswith("qwen3")


def _get_model(engine: str):
    """To be called from MLX_THREAD only."""
    if engine not in _models:
        from mlx_audio.tts.utils import load_model

        logger.info("chargement de %s", ENGINES[engine])
        _models[engine] = load_model(ENGINES[engine])
    return _models[engine]


def preload(engine: str) -> None:
    """Loads and warms up an engine (blocking, from any thread)."""

    def warm():
        for _ in _generate(engine, "Ready.", None, load_settings()["tts"], threading.Event()):
            pass

    MLX_THREAD.submit(warm).result()


def parse_markup(text: str) -> list[tuple[str, object]]:
    """Splits a tagged text into ("text", str) / ("expression", label) / ("sound", label) / ("break", ms)."""
    parts: list[tuple[str, object]] = []
    pos = 0
    for m in _EXPR_RE.finditer(text):
        if m.start() > pos:
            parts.append(("text", text[pos : m.start()]))
        pos = m.end()
        attrs = dict(_ATTR_RE.findall(m.group(1) or ""))
        kind, label = attrs.get("type", "").lower(), attrs.get("label", "").strip()
        if kind == "expression" and label:
            parts.append(("expression", label))
        elif kind == "sound" and label:
            parts.append(("sound", label))
        elif kind == "break":
            bm = _BREAK_MS_RE.search(label)
            if bm:
                value = float(bm.group(1))
                parts.append(("break", value * 1000 if (bm.group(2) or "ms").lower() == "s" else value))
    if pos < len(text):
        parts.append(("text", text[pos:]))
    return _split_brackets(parts)


def _split_brackets(parts: list[tuple[str, object]]) -> list[tuple[str, object]]:
    """In the text pieces, [label] becomes an expression (or a sound), never spoken."""
    out: list[tuple[str, object]] = []
    for kind, value in parts:
        if kind != "text":
            out.append((kind, value))
            continue
        text, pos = str(value), 0
        for m in BRACKET_RE.finditer(text):
            if m.start() > pos:
                out.append(("text", text[pos : m.start()]))
            label = m.group(1).strip()
            out.append(("sound" if label.lower() in SOUNDS else "expression", label))
            pos = m.end()
        if pos < len(text):
            out.append(("text", text[pos:]))
    return out


HEAD_WORDS = 6
_HEAD_PUNCT_RE = re.compile(r"[,;:.!?](\s|$)")


def split_head(text: str) -> list[str]:
    """Splits the start of a long sentence (up to the 1st punctuation or HEAD_WORDS words) from the rest."""
    words = text.split()
    if len(words) <= HEAD_WORDS + 2:
        return [text]
    m = _HEAD_PUNCT_RE.search(text)
    if m and len(text[: m.end()].split()) <= HEAD_WORDS + 2:
        head, tail = text[: m.end()].strip(), text[m.end() :].strip()
    else:
        head, tail = " ".join(words[:HEAD_WORDS]), " ".join(words[HEAD_WORDS:])
    return [head, tail] if tail else [head]


def _max_tokens_for(text: str) -> int:
    """Safeguard against Qwen running off (it can keep talking): ~12 tokens/s of audio."""
    words = max(1, len(text.split()))
    seconds = words / 2.0 + 2.0
    return int(seconds * 12 * 1.6)


def _generate(engine: str, text: str, instruct: str | None, t: dict, cancel: threading.Event):
    """Generates the audio (float32, 24 kHz) in chunks. In MLX_THREAD."""
    model = _get_model(engine)
    if engine == "kokoro":
        voice = t["kokoro_voice"]
        results = model.generate(
            text=text, voice=voice, speed=t["speed"], lang_code="b" if voice.startswith("b") else "a")
    elif supports_instruct(engine):
        results = model.generate_custom_voice(
            text=text, speaker=t["qwen_speaker"], language="English", instruct=instruct or None,
            max_tokens=_max_tokens_for(text), stream=True, streaming_interval=0.6)
    else:
        results = model.generate(text=text)
    for r in results:
        if cancel.is_set():
            break
        audio = np.array(r.audio, dtype=np.float32).reshape(-1)
        if r.sample_rate != SAMPLE_RATE:
            positions = np.arange(0, len(audio), r.sample_rate / SAMPLE_RATE)
            audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
        yield audio


def _pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


class LocalTTS(tts.TTS):
    """Local multi-engine TTS. Reads the settings at each sentence: voice changes apply live."""

    class Markup(tts.TTS.Markup):
        def _provider_key(self) -> str:
            # "inworld" format: free-form English labels, exactly what Qwen3-TTS expects.
            return "inworld" if self._tts.expressive_enabled else ""

    def __init__(self) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False), sample_rate=SAMPLE_RATE, num_channels=1)
        self.expressive_enabled = False
        # Last instruction seen: a sentence without a tag keeps the tone of the previous one.
        self.current_instruct: str | None = None

    @property
    def model(self) -> str:
        return load_settings()["tts"]["engine"]

    @property
    def provider(self) -> str:
        return "mlx-local"

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        return LocalChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class LocalChunkedStream(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        s = load_settings()
        t, emotion = s["tts"], s["emotion"]
        engine = t["engine"]
        local_tts: LocalTTS = self._tts  # type: ignore[assignment]

        # Synthesis plan: (instruction, text) and breaks, in order.
        plan: list[tuple[str, object, str | None]] = []
        instruct = local_tts.current_instruct
        if emotion["mode"] == "fixed":
            instruct = emotion["fixed_style"]
        elif emotion["mode"] == "off" or not supports_instruct(engine):
            instruct = None
        for kind, value in parse_markup(self._input_text):
            if kind == "expression" and emotion["mode"] == "auto":
                instruct = str(value)
            elif kind == "break":
                plan.append(("break", value, None))
            elif kind == "text" and str(value).strip():
                # Kokoro and Chatterbox only return audio once the whole text is synthesized:
                # start with a short sentence head so the voice starts sooner.
                pieces = [str(value).strip()] if supports_instruct(engine) else split_head(str(value).strip())
                plan.extend(("text", piece, instruct) for piece in pieces)
        if emotion["mode"] == "auto":
            local_tts.current_instruct = instruct

        output_emitter.initialize(
            request_id=shortuuid(), sample_rate=SAMPLE_RATE, num_channels=1, mime_type="audio/pcm")
        if not plan:
            output_emitter.flush()
            return

        # Generation happens in the voice server (separate process): the audio arrives as a stream.
        session = http_context.http_session()
        started = False
        for kind, value, instr in plan:
            if kind == "break":
                output_emitter.push(bytes(2 * int(SAMPLE_RATE * min(float(value), 3000) / 1000)))
                continue
            payload = {"engine": engine, "text": value, "instruct": instr, "kokoro_voice": t["kokoro_voice"],
                       "qwen_speaker": t["qwen_speaker"], "speed": t["speed"]}
            async with session.post(f"{TTS_URL}/synthesize", json=payload,
                                    timeout=aiohttp.ClientTimeout(total=120)) as resp:
                resp.raise_for_status()
                carry = b""
                # Qwen3-TTS arrives in chunks, sometimes slower than playback: keep a
                # lead (~0.8 s) before playing, so the voice does not stutter.
                held = bytearray() if supports_instruct(engine) and not started else None
                async for chunk in resp.content.iter_chunked(4800):
                    data = carry + chunk
                    # Always a whole number of 16-bit samples.
                    cut = len(data) - len(data) % 2
                    carry = data[cut:]
                    if held is not None:
                        held += data[:cut]
                        if len(held) >= PREBUFFER_BYTES:
                            output_emitter.push(bytes(held))
                            held = None
                    elif cut:
                        output_emitter.push(data[:cut])
                if held:
                    output_emitter.push(bytes(held))
                started = True
        output_emitter.flush()
