"""Local voice server: POST /synthesize -> 16-bit 24 kHz mono PCM audio, streamed;
POST /transcribe -> text (Mac app dictation, multilingual Parakeet v3).

Runs in its own process so that generation (Qwen3-TTS's Python loop) does not slow down
the agent (transcription, LiveKit loop). Models loaded once, shared by all sessions.

  uv run tts_server.py            # port 8090
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import unicodedata

from aiohttp import web

from models_mlx import MLX_THREAD, SAMPLE_RATE, _generate, _pcm16, preload
import voice_id
from settings import load_settings
from privacy import r

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger("voice.tts_server")


async def synthesize(request: web.Request) -> web.StreamResponse:
    body = await request.json()
    engine = body.get("engine") or load_settings()["tts"]["engine"]
    text = (body.get("text") or "").strip()
    instruct = body.get("instruct") or None
    voice = {**load_settings()["tts"], **{k: body[k] for k in ("kokoro_voice", "qwen_speaker", "speed") if k in body}}

    response = web.StreamResponse(headers={
        "Content-Type": "audio/pcm", "X-Sample-Rate": str(SAMPLE_RATE), "Cache-Control": "no-store"})
    await response.prepare(request)
    if not text:
        await response.write_eof()
        return response

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancel = threading.Event()

    def work() -> None:
        try:
            for audio in _generate(engine, text, instruct, voice, cancel):
                loop.call_soon_threadsafe(queue.put_nowait, _pcm16(audio))
        except Exception as e:
            logger.exception("génération « %s »", r(text))
            loop.call_soon_threadsafe(queue.put_nowait, e)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    logger.info("%s « %s » [%s]", engine, r(text), instruct or "neutre")
    MLX_THREAD.submit(work)
    try:
        while (item := await queue.get()) is not None:
            if isinstance(item, Exception):
                break
            await response.write(item)
        await response.write_eof()
    except (ConnectionResetError, asyncio.CancelledError):
        # Interruption: the agent hung up, we simply stop generating.
        logger.info("client parti : génération arrêtée")
    finally:
        cancel.set()
    return response


async def preload_handler(request: web.Request) -> web.Response:
    engine = (await request.json()).get("engine") or load_settings()["tts"]["engine"]
    await asyncio.get_running_loop().run_in_executor(None, preload, engine)
    return web.json_response({"ok": True, "engine": engine})


_stt: dict = {}


def _load_stt(name: str):
    """Dictation model, loaded on first request (on MLX_THREAD, like the voices) then kept.
    Weights converted to bfloat16: 1.3 GB instead of 2.6, same text (Parakeet v3, test of Oct 26)."""
    if name not in _stt:
        import mlx.core as mx
        import numpy as np
        from mlx.utils import tree_map
        from mlx_audio.stt.utils import load_model

        model = load_model(name)
        model.update(tree_map(lambda p: p.astype(mx.bfloat16) if p.dtype == mx.float32 else p, model.parameters()))
        mx.eval(model.parameters())
        model.generate(mx.array(np.zeros(16000, dtype=np.float32)))   # compiles the kernels (~3 s, once)
        _stt.clear()
        _stt[name] = model
        logger.info("dictée : %s chargé", name)
    return _stt[name]


_whisper: dict = {}
WHISPER = "mlx-community/whisper-large-v3-turbo-8bit"


def _load_whisper():
    """Dictation fallback: Whisper turbo, forced language (~3 s for 3 s of audio, versus ~0.5 s for Parakeet).
    The 8-bit repo has no tokenizer: the one from openai/whisper-large-v3-turbo (cached) fills the gap."""
    if not _whisper:
        from mlx_audio.stt.utils import load_model
        from transformers import WhisperProcessor

        model = load_model(WHISPER)
        model._processor = WhisperProcessor.from_pretrained("openai/whisper-large-v3-turbo")
        _whisper["model"] = model
        logger.info("dictée : %s chargé (repli)", WHISPER)
    return _whisper["model"]


def _wrong_script(text: str, language: str) -> bool:
    """Parakeet v3 guesses the language by itself and cannot be configured: on a short sound, French sometimes
    comes out in Cyrillic ("Эй оси.", Oct 26). Non-Latin letters for a Latin language -> wrong language."""
    if language in ("", "auto") or language.split("-")[0] in ("ru", "uk", "bg", "el"):
        return False
    return any(ch.isalpha() and "LATIN" not in unicodedata.name(ch, "") for ch in text)


async def transcribe(request: web.Request) -> web.Response:
    """POST /transcribe: PCM float32, 16 kHz, mono -> {"text", "ms"}. For the Mac app's dictation."""
    import time

    import numpy as np

    audio = np.frombuffer(await request.read(), dtype=np.float32)
    if audio.size < 1600:   # less than 0.1 s
        return web.json_response({"text": "", "ms": 0})
    dictation = load_settings().get("dictation", {})
    name = dictation.get("model", "mlx-community/parakeet-tdt-0.6b-v3")
    # ?language=latin (agent commands): any Latin language, Whisper guesses the language on fallback.
    language = request.query.get("language") or dictation.get("language", "auto")

    # ?partial=1: live text while the dictation key is held (the app sends the audio so far, ~every 0.6 s).
    # Parakeet only, no Whisper fallback (the final pass on release does that), not logged. With "Listen only to my
    # voice", another voice must never appear, even for a moment: nothing is shown until 1.5 s of the owner's voice.
    if request.query.get("partial") == "1":
        check, threshold = voice_id.settings_for("dictation")
        if check:
            if audio.size < 24000:
                return web.json_response({"text": "", "partial": True})
            similarity = await asyncio.to_thread(voice_id.score, audio)
            if similarity is None or similarity < threshold:
                return web.json_response({"text": "", "partial": True})

        def partial() -> str:
            import mlx.core as mx

            return _load_stt(name).generate(mx.array(audio)).text.strip()

        try:
            text = await asyncio.wrap_future(MLX_THREAD.submit(partial))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
        return web.json_response({"text": text, "partial": True})

    def work() -> str:
        import mlx.core as mx

        text = _load_stt(name).generate(mx.array(audio)).text.strip()
        if _wrong_script(text, "fr" if language == "latin" else language):
            logger.info("dictée : « %s » pas en %s, repli Whisper", r(text), language)
            code = None if language == "latin" else language.split("-")[0]
            text = _load_whisper().generate(mx.array(audio), language=code).text.strip()
        logger.info(mlx_memory())
        return text

    start = time.perf_counter()
    # Mac app dictation (without ?language=) with "Listen only to my voice": the voice first (~0.1 s); another voice
    # is not transcribed at all.
    check, threshold = (False, 0.0) if "language" in request.query else voice_id.settings_for("dictation")
    similarity = await asyncio.to_thread(voice_id.score, audio) if check else None
    if similarity is not None and similarity < threshold:
        logger.info("dictée %.1f s : autre voix (%.2f < %.2f), ignorée", audio.size / 16000, similarity, threshold)
        return web.json_response({"text": "", "rejected": True, "score": round(similarity, 3), "ms": 0})
    try:
        text = await asyncio.wrap_future(MLX_THREAD.submit(work))
    except Exception as e:
        logger.exception("dictée")
        return web.json_response({"error": str(e)}, status=500)
    ms = (time.perf_counter() - start) * 1000
    logger.info("dictée %.1f s -> %.0f ms%s : %s", audio.size / 16000, ms,
                f", voix {similarity:.2f}" if similarity is not None else "", r(text))
    return web.json_response({"text": text, "ms": round(ms)})


async def voice_status(_: web.Request) -> web.Response:
    """GET /voice_id: voiceprint saved or not (dashboard "My voice" page)."""
    return web.json_response({"enrolled": await asyncio.to_thread(voice_id.enrolled)})


async def voice_enroll(request: web.Request) -> web.Response:
    """POST /voice_id/enroll: PCM float32 16 kHz of the readings, end to end -> voiceprint in the keychain."""
    import numpy as np

    audio = np.frombuffer(await request.read(), dtype=np.float32)
    try:
        vec = await asyncio.to_thread(voice_id.template, [audio])
        await asyncio.to_thread(voice_id.save_print, vec)
    except Exception as e:
        logger.warning("empreinte vocale : %s", e)
        return web.json_response({"ok": False, "error": str(e)}, status=400)
    logger.info("empreinte vocale enregistrée (%.0f s de lecture)", audio.size / 16000)
    return web.json_response({"ok": True, "seconds": round(audio.size / 16000, 1)})


async def voice_score(request: web.Request) -> web.Response:
    """POST /voice_id/score: PCM float32 16 kHz -> similarity to the voiceprint ("Test" button)."""
    import numpy as np

    audio = np.frombuffer(await request.read(), dtype=np.float32)
    similarity = await asyncio.to_thread(voice_id.score, audio)
    threshold = float(load_settings().get("voice_id", {}).get("threshold", voice_id.DEFAULT_THRESHOLD))
    return web.json_response({"score": None if similarity is None else round(similarity, 3), "threshold": threshold,
                              "accepted": similarity is not None and similarity >= threshold})


async def voice_erase(_: web.Request) -> web.Response:
    """DELETE /voice_id: erases the voiceprint from the keychain."""
    await asyncio.to_thread(voice_id.erase_print)
    logger.info("empreinte vocale effacée")
    return web.json_response({"ok": True})


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# MLX cache capped: without a limit, MLX keeps every freed buffer for reuse, and the server grew to 9.2 GB
# for ~2.5 GB of models (Parakeet, Whisper, Kokoro; measured on Nov 4). Too much for a 16 GB Mac.
MLX_CACHE_LIMIT = int(os.environ.get("VOICE_MLX_CACHE_MB", "1024")) * 1024 * 1024


def _limit_mlx_cache() -> None:
    import mlx.core as mx

    mx.set_cache_limit(MLX_CACHE_LIMIT)


def mlx_memory() -> str:
    """"models 2.4 GB, cache 0.6 GB": to be called on MLX_THREAD."""
    import mlx.core as mx

    return f"mémoire MLX {mx.get_active_memory() / 1e9:.1f} Go, cache {mx.get_cache_memory() / 1e9:.1f} Go"


def main() -> None:
    MLX_THREAD.submit(_limit_mlx_cache).result()
    engine = load_settings()["tts"]["engine"]
    logger.info("préchargement de %s", engine)
    preload(engine)
    dictation = load_settings().get("dictation", {})
    if dictation.get("engine", "parakeet") == "parakeet":   # first dictation without waiting for loading (~7 s)
        MLX_THREAD.submit(_load_stt, dictation.get("model", "mlx-community/parakeet-tdt-0.6b-v3"))
        if dictation.get("language", "auto") != "auto":   # fallback ready: otherwise the 1st language error takes ~12 s
            MLX_THREAD.submit(_load_whisper)
    # Dictation: 64 KB per second of audio. aiohttp's default limit (1 MB) refused anything beyond ~16 s (413).
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/synthesize", synthesize)
    app.router.add_post("/preload", preload_handler)
    app.router.add_post("/transcribe", transcribe)
    app.router.add_get("/health", health)
    app.router.add_get("/voice_id", voice_status)
    app.router.add_post("/voice_id/enroll", voice_enroll)
    app.router.add_post("/voice_id/score", voice_score)
    app.router.add_delete("/voice_id", voice_erase)
    web.run_app(app, host="127.0.0.1", port=int(os.environ.get("TTS_PORT", "8090")), print=None)


if __name__ == "__main__":
    main()
