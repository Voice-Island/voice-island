"""Other voices "in the room": FLEURS sentences played through the Mac's speakers and re-recorded by its
mic (same mic, same room as the owner: the case of a video or a neighbor, harder than raw FLEURS).

  uv run room.py        # ~2 min, quiet room; then uv run bench.py (the takes go to data/impostors/room/)
"""

import subprocess
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "data" / "impostors" / "fleurs"
OUT = ROOT / "data" / "impostors" / "room"
RATE = 16000
PER_LANG = 10


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for lang in ("fr_fr", "en_us", "de_de"):
        for clip in sorted((SRC / lang).glob("*.wav"))[-PER_LANG:]:   # the last ones: not the same as raw FLEURS
            target = OUT / f"{lang}_{clip.name}"
            if target.exists():
                continue
            chunks: list[np.ndarray] = []
            with sd.InputStream(samplerate=RATE, channels=1, dtype="float32",
                                callback=lambda d, *_: chunks.append(d.copy())):
                subprocess.run(["afplay", str(clip)], check=True)
            audio = np.concatenate(chunks)[:, 0]
            sf.write(target, audio, RATE)
            print(f"{target.name} : niveau {np.abs(audio).max():.2f}")


if __name__ == "__main__":
    main()
