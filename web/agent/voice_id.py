""""Listen only to my voice": speaker verification, 100% local.

WeSpeaker ResNet34-LM in ONNX (CC-BY-4.0, 26 MB), chosen by the measurements in docs/VOICE-ID-BENCHMARK.md: on the
owner's voice and 300 other voices, complete separation (owner's worst score 0.42, best other voice 0.36),
~70-90 ms for 2 s of audio. The voiceprint (256 numbers, mean of the 3 s windows of the enrollment readings) is
kept in the macOS keychain, never in a file in the repo; "erase" deletes it.

Used by the voice server (dictation, dashboard "My voice" page) and by the agent (commands, assistant).

  uv run python voice_id.py enroll lecture1.wav lecture2.wav …   # creates the voiceprint
  uv run python voice_id.py score phrase.wav                       # score of a sentence
  uv run python voice_id.py status | erase
"""

from __future__ import annotations

import base64
import subprocess
import sys
import threading
import time

import numpy as np

RATE = 16000
REPO = "Wespeaker/wespeaker-voxceleb-resnet34-LM"
MODEL_FILE = "voxceleb_resnet34_LM.onnx"
KEYCHAIN_SERVICE = "VoiceIsland voiceprint"
KEYCHAIN_ACCOUNT = "voice-island"
DEFAULT_THRESHOLD = 0.39
MIN_SECONDS = 0.25   # below this, nothing to compare: the sentence is not judged

_session = None
_lock = threading.Lock()


# ---------- audio ----------

def to_16k(pcm: np.ndarray, rate: int) -> np.ndarray:
    pcm = pcm.astype(np.float32)
    if rate == RATE:
        return pcm
    positions = np.arange(0, len(pcm), rate / RATE)
    return np.interp(positions, np.arange(len(pcm)), pcm).astype(np.float32)


def trim(audio: np.ndarray) -> np.ndarray:
    """Removes silence at the start and end (energy per 30 ms frame, 0.1 s margin)."""
    frame = int(0.03 * RATE)
    n = len(audio) // frame
    if n == 0:
        return audio
    rms = np.sqrt((audio[: n * frame].reshape(n, frame) ** 2).mean(axis=1) + 1e-12)
    floor = np.percentile(rms, 10)
    voiced = np.where(rms > max(floor * 4, rms.max() * 0.08))[0]
    if len(voiced) == 0:
        return audio[:0]
    start = max(0, voiced[0] * frame - int(0.1 * RATE))
    end = min(len(audio), (voiced[-1] + 1) * frame + int(0.1 * RATE))
    return audio[start:end]


# ---------- model ----------

def _model():
    global _session
    if _session is None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(REPO, MODEL_FILE)
        _session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    return _session


def _fbank(audio: np.ndarray) -> np.ndarray:
    """WeSpeaker features: Kaldi fbank with 80 bins, 25 / 10 ms, int16 amplitude, mean removed."""
    import kaldi_native_fbank as knf

    opts = knf.FbankOptions()
    opts.frame_opts.dither = 0.0
    opts.frame_opts.samp_freq = RATE
    opts.frame_opts.frame_length_ms = 25
    opts.frame_opts.frame_shift_ms = 10
    opts.mel_opts.num_bins = 80
    fbank = knf.OnlineFbank(opts)
    fbank.accept_waveform(RATE, (audio * 32768).tolist())
    fbank.input_finished()
    feats = np.array([fbank.get_frame(i) for i in range(fbank.num_frames_ready)], dtype=np.float32)
    return feats - feats.mean(axis=0, keepdims=True)


def embed(audio: np.ndarray) -> np.ndarray:
    """Normalized embedding of a 16 kHz mono clip."""
    session = _model()
    with _lock:
        vec = session.run(None, {session.get_inputs()[0].name: _fbank(audio)[None]})[0].squeeze()
    return vec / (np.linalg.norm(vec) + 1e-9)


def template(clips: list[np.ndarray]) -> np.ndarray:
    """Owner's voiceprint: mean of the 3 s windows (1.5 s step) of their readings, silences removed."""
    vecs = []
    for clip in clips:
        clip = trim(clip)
        for start in range(0, max(1, len(clip) - 3 * RATE + 1), int(1.5 * RATE)):
            window = clip[start:start + 3 * RATE]
            if len(window) >= RATE:
                vecs.append(embed(window))
    if not vecs:
        raise ValueError("not enough voice in the recordings")
    mean = np.mean(vecs, axis=0)
    return mean / (np.linalg.norm(mean) + 1e-9)


# ---------- voiceprint in the keychain ----------

def _security(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["security", *args], capture_output=True, text=True, timeout=10)


_cache: tuple[float, np.ndarray | None] = (0.0, None)


def load_print() -> np.ndarray | None:
    """Saved voiceprint (reread every 10 s at most: an erase from the dashboard takes effect quickly)."""
    global _cache
    if time.monotonic() - _cache[0] < 10:
        return _cache[1]
    out = _security("find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w")
    vec = None
    if out.returncode == 0 and out.stdout.strip():
        vec = np.frombuffer(base64.b64decode(out.stdout.strip()), dtype=np.float32).copy()
    _cache = (time.monotonic(), vec)
    return vec


def save_print(vec: np.ndarray) -> None:
    global _cache
    data = base64.b64encode(vec.astype(np.float32).tobytes()).decode()
    out = _security("add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT,
                    "-l", "Voice Island : empreinte vocale", "-w", data)
    if out.returncode != 0:
        raise RuntimeError(f"trousseau : {out.stderr.strip()}")
    _cache = (time.monotonic(), vec.astype(np.float32))


def erase_print() -> None:
    global _cache
    _security("delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT)
    _cache = (time.monotonic(), None)


def enrolled() -> bool:
    return load_print() is not None


def score(audio: np.ndarray) -> float | None:
    """Similarity (cosine) of a 16 kHz sentence to the voiceprint; None without a voiceprint or without voice."""
    ref = load_print()
    audio = trim(audio)
    if ref is None or len(audio) < MIN_SECONDS * RATE:
        return None
    return float(embed(audio) @ ref)


def settings_for(product: str) -> tuple[bool, float]:
    """(verification active for this product — dictation | command | assistant —, threshold)."""
    from settings import load_settings

    cfg = load_settings().get("voice_id", {})
    return bool(cfg.get(product)) and enrolled(), float(cfg.get("threshold", DEFAULT_THRESHOLD))


class Gate:
    """Gatekeeper of a conversation: does the sentence pass "Listen only to my voice" for this product?

    Called before transcription (Parakeet) or before keeping its text (Moonshine): another voice is dropped
    silently, neither transcribed nor shown. A very short word (< 1 s, "Stop") passes if it closely follows a
    sentence from the owner (`short_grace` seconds): that is where the score is least reliable.
    """

    def __init__(self, product: str) -> None:
        self.product = product
        self.owner_at = 0.0

    def active(self) -> bool:
        return settings_for(self.product)[0]

    def accepts(self, pcm16k: np.ndarray) -> bool:
        """Blocking (ONNX model): call it in a thread, not in the asyncio loop."""
        check, threshold = settings_for(self.product)
        if not check:
            return True
        similarity = score(pcm16k)
        if similarity is None:   # no voice: nothing to judge, the transcription will not find anything either
            return True
        if similarity >= threshold:
            self.owner_at = time.monotonic()
            return True
        from settings import load_settings

        speech = len(trim(pcm16k)) / RATE
        grace = float(load_settings().get("voice_id", {}).get("short_grace", 20))
        if speech < 1.0 and time.monotonic() - self.owner_at < grace:
            return True
        import logging

        logging.getLogger("voice.voice_id").info("autre voix (%.2f < %.2f, %.1f s) : ignorée", similarity, threshold,
                                                 speech)
        return False


def _read(path: str) -> np.ndarray:
    """16-bit WAV (what web/test/voice_id/record.py writes), with no extra dependency."""
    import wave

    with wave.open(path) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        pcm = pcm.reshape(-1, w.getnchannels()).mean(axis=1)
        return to_16k(pcm, w.getframerate())


if __name__ == "__main__":
    command, files = (sys.argv[1] if len(sys.argv) > 1 else "status"), sys.argv[2:]
    if command == "enroll":
        save_print(template([_read(f) for f in files]))
        print(f"Voiceprint saved ({len(files)} recordings).")
    elif command == "score":
        for f in files:
            print(f, score(_read(f)))
    elif command == "erase":
        erase_print()
        print("Voiceprint erased.")
    else:
        print("Empreinte :", "oui" if enrolled() else "non")
