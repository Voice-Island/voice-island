"""Compares two speaker verification models on the owner's voice and on other voices.

  cd web/test/voice_id && uv run record.py   # first: the owner's voice (data/owner/)
  uv run bench.py                            # downloads models + FLEURS voices the 1st time, writes results/report.md

Models: SpeechBrain ECAPA-TDNN (Apache-2.0) and WeSpeaker ResNet34-LM in ONNX (CC-BY-4.0).
Other voices: FLEURS (Google, CC-BY-4.0), dev fr / en / de, cut to the same durations as the owner's
sentences (1, 2 and 4 s), and macOS synthetic voices (say). Score = cosine with the owner's voiceprint
(mean of the 3 s windows of their three readings).
"""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OWNER = Path(os.environ.get("VOICE_ID_OWNER", DATA / "owner"))   # other folder: pipeline test
RESULTS = ROOT / "results" / OWNER.name
RATE = 16000
FLEURS = {"fr_fr": 100, "en_us": 100, "de_de": 100}   # sentences kept per language
BUCKETS = [("courte (< 1,5 s)", 0.0, 1.5, 1.0), ("moyenne (1,5-3 s)", 1.5, 3.0, 2.0), ("longue (≥ 3 s)", 3.0, 99, 4.0)]
SAY_VOICES = ["Thomas", "Amélie", "Samantha", "Daniel", "Anna", "Reed", "Flo"]
SAY_TEXTS = ["Mets Chrome à gauche et Notes à droite.", "What time is it right now?", "Pause.",
             "Ouvre une nouvelle fenêtre à droite.", "Search the web for the weather in Geneva."]


# ---------- audio ----------

def load(path: Path) -> np.ndarray:
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if rate != RATE:
        audio = torchaudio.functional.resample(torch.from_numpy(audio), rate, RATE).numpy()
    return audio


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
        return audio
    start = max(0, voiced[0] * frame - int(0.1 * RATE))
    end = min(len(audio), (voiced[-1] + 1) * frame + int(0.1 * RATE))
    return audio[start:end]


# ---------- models ----------

class Ecapa:
    name = "SpeechBrain ECAPA-TDNN (Apache-2.0)"

    def __init__(self) -> None:
        from speechbrain.inference.speaker import EncoderClassifier

        self.model = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb", savedir=str(DATA / "models" / "ecapa"),
            run_opts={"device": "cpu"})

    def embed(self, audio: np.ndarray) -> np.ndarray:
        with torch.no_grad():
            return self.model.encode_batch(torch.from_numpy(audio)[None]).squeeze().numpy()


class WeSpeaker:
    name = "WeSpeaker ResNet34-LM, ONNX (CC-BY-4.0)"

    def __init__(self) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download

        path = hf_hub_download("Wespeaker/wespeaker-voxceleb-resnet34-LM", "voxceleb_resnet34_LM.onnx",
                               cache_dir=str(DATA / "models"))
        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def embed(self, audio: np.ndarray) -> np.ndarray:
        wave = torch.from_numpy(audio)[None] * (1 << 15)   # like WeSpeaker: int16 amplitude, Kaldi fbank
        feats = torchaudio.compliance.kaldi.fbank(wave, num_mel_bins=80, frame_length=25, frame_shift=10,
                                                  dither=0.0, sample_frequency=RATE)
        feats = feats - feats.mean(dim=0, keepdim=True)
        return self.session.run(None, {self.input: feats[None].numpy()})[0].squeeze()


def unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-9)


def template(model, enroll: list[np.ndarray]) -> np.ndarray:
    """Voiceprint: mean of the 3 s windows (1.5 s step) of the owner's readings."""
    vecs = []
    for audio in enroll:
        for start in range(0, max(1, len(audio) - 3 * RATE + 1), int(1.5 * RATE)):
            vecs.append(unit(model.embed(audio[start:start + 3 * RATE])))
    return unit(np.mean(vecs, axis=0))


# ---------- other voices ----------

def fleurs() -> dict[str, list[np.ndarray]]:
    from huggingface_hub import hf_hub_download

    voices: dict[str, list[np.ndarray]] = {}
    for lang, keep in FLEURS.items():
        folder = DATA / "impostors" / "fleurs" / lang
        if len(list(folder.glob("*.wav"))) < keep:
            folder.mkdir(parents=True, exist_ok=True)
            tar_path = hf_hub_download("google/fleurs", f"data/{lang}/audio/dev.tar.gz", repo_type="dataset",
                                       cache_dir=str(DATA / "hf"))
            with tarfile.open(tar_path, "r|gz") as tar:
                saved = 0
                for member in tar:
                    if member.isfile() and member.name.endswith(".wav"):
                        (folder / Path(member.name).name).write_bytes(tar.extractfile(member).read())
                        saved += 1
                        if saved >= keep:
                            break
        voices[lang] = [trim(load(p)) for p in sorted(folder.glob("*.wav"))[:keep]]
    return voices


def synthetic() -> list[np.ndarray]:
    folder = DATA / "impostors" / "say"
    folder.mkdir(parents=True, exist_ok=True)
    available = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    out = []
    for voice in SAY_VOICES:
        if not any(line.startswith(voice + " ") or line.startswith(voice + "  ") for line in available.splitlines()):
            continue
        for i, text in enumerate(SAY_TEXTS):
            path = folder / f"{voice}_{i}.aiff"
            if not path.exists():
                subprocess.run(["say", "-v", voice, "-o", str(path), text], check=True)
            out.append(trim(load(path)))
    return out


def crop(audio: np.ndarray, seconds: float) -> np.ndarray | None:
    n = int(seconds * RATE)
    return audio[:n] if len(audio) >= n else None


# ---------- measurements ----------

def eer(genuine: np.ndarray, impostor: np.ndarray) -> tuple[float, float]:
    """Equal error rate (false rejection = false acceptance) and its threshold."""
    best_gap, best = 2.0, (1.0, 1.0)
    for t in np.unique(np.concatenate([genuine, impostor])):
        frr, far = float((genuine < t).mean()), float((impostor >= t).mean())
        if abs(frr - far) < best_gap:
            best_gap, best = abs(frr - far), ((frr + far) / 2, float(t))
    return best


def frr_at_far(genuine: np.ndarray, impostor: np.ndarray, far: float) -> tuple[float, float]:
    """False rejection when only a share `far` of the other voices gets through; and the matching threshold."""
    t = float(np.quantile(impostor, 1 - far)) if len(impostor) else 1.0
    return float((genuine < t).mean()), t


def main() -> None:
    manifest = json.loads((OWNER / "manifest.json").read_text())
    enroll = [trim(load(OWNER / "enroll" / name)) for name in manifest["enroll"]]
    tests = [(name, meta, trim(load(OWNER / "test" / name))) for name, meta in manifest["test"].items()]
    print(f"Propriétaire : {len(enroll)} lectures, {len(tests)} phrases de test")
    others = fleurs()
    fake = synthetic()
    # FLEURS voices replayed through the speakers and re-recorded by the Mac's mic (room.py): same mic, same room.
    room = [trim(load(p)) for p in sorted((DATA / "impostors" / "room").glob("*.wav"))]
    print("Autres voix : " + ", ".join(f"{k} {len(v)}" for k, v in others.items()) + f", synthèse {len(fake)}")

    RESULTS.mkdir(parents=True, exist_ok=True)
    lines = ["# Vérification du locuteur : mesures", "",
             f"Propriétaire : {len(enroll)} lectures (empreinte), {len(tests)} phrases de test. Autres voix : FLEURS "
             + ", ".join(f"{k} ({len(v)})" for k, v in others.items()) + f", voix de synthèse macOS ({len(fake)}).",
             "Score = cosinus avec l'empreinte. EER : taux où refus à tort = acceptation à tort. "
             "« Refus à 1 % » : part des phrases du propriétaire refusées quand 1 % seulement des autres voix passent.",
             ""]
    raw: dict = {}
    for cls in (Ecapa, WeSpeaker):
        model = cls()
        ref = template(model, enroll)
        genuine = []
        for name, meta, audio in tests:
            genuine.append({"name": name, "dur": len(audio) / RATE, "hint": meta.get("hint", ""),
                            "score": float(unit(model.embed(audio)) @ ref)})
        impostor = {}
        for label, lo, hi, seconds in BUCKETS:
            scores = []
            for lang, clips in others.items():
                for clip in clips:
                    c = crop(clip, seconds)
                    if c is not None:
                        scores.append(float(unit(model.embed(c)) @ ref))
            impostor[label] = np.array(scores)
        fake_scores = np.array([float(unit(model.embed(a)) @ ref) for a in fake])
        room_scores = np.array([float(unit(model.embed(c)) @ ref)
                                for clip in room for _, _, _, sec in BUCKETS if (c := crop(clip, sec)) is not None])
        clip2 = np.zeros(2 * RATE, dtype=np.float32) + np.random.default_rng(0).normal(0, 0.01, 2 * RATE).astype(np.float32)
        model.embed(clip2)
        start = time.perf_counter()
        for _ in range(10):
            model.embed(clip2)
        ms = (time.perf_counter() - start) * 100

        all_gen = np.array([g["score"] for g in genuine])
        all_imp = np.concatenate(list(impostor.values()))
        e, t_eer = eer(all_gen, all_imp)
        f1, t1 = frr_at_far(all_gen, all_imp, 0.01)
        lines += [f"## {model.name}", "",
                  f"- Calcul : {ms:.0f} ms pour 2 s de son (CPU).",
                  f"- Toutes durées : EER {e:.1%} (seuil {t_eer:.3f}) ; refus à 1 % : {f1:.0%} (seuil {t1:.3f}).",
                  f"- Voix de synthèse : score max {fake_scores.max():.3f}, acceptées au seuil 1 % : "
                  f"{(fake_scores >= t1).mean():.0%}.",
                  (f"- Voix rejouées dans la pièce (même micro, {len(room)} phrases, {len(room_scores)} extraits) : "
                   f"score max {room_scores.max():.3f}, acceptées au seuil 1 % : {(room_scores >= t1).mean():.0%}, "
                   f"au seuil EER : {(room_scores >= t_eer).mean():.0%} ; EER propriétaire / pièce "
                   f"{eer(all_gen, room_scores)[0]:.1%}." if len(room_scores) else "- Pas de voix rejouées (room.py)."),
                  f"- Score du propriétaire : min {all_gen.min():.3f}, médiane {np.median(all_gen):.3f} ; autres voix "
                  f"FLEURS : max {all_imp.max():.3f}.", "",
                  "| Durée | Phrases du propriétaire | Autres voix | EER | Refus à 1 % |", "| --- | --- | --- | --- | --- |"]
        for label, lo, hi, _ in BUCKETS:
            g = np.array([x["score"] for x in genuine if lo <= x["dur"] < hi])
            imp = impostor[label]
            if len(g) and len(imp):
                be, _ = eer(g, imp)
                bf, _ = frr_at_far(g, imp, 0.01)
                lines.append(f"| {label} | {len(g)} | {len(imp)} | {be:.1%} | {bf:.0%} |")
        lines += ["", "Phrases du propriétaire refusées au seuil 1 % :", ""]
        for g in sorted(genuine, key=lambda x: x["score"]):
            if g["score"] < t1:
                lines.append(f"- {g['name']} ({g['dur']:.1f} s{', ' + g['hint'] if g['hint'] else ''}) : {g['score']:.3f}")
        lines.append("")
        raw[model.name] = {"genuine": genuine, "impostor": {k: v.tolist() for k, v in impostor.items()},
                           "synthetic": fake_scores.tolist(), "room": room_scores.tolist(), "ms_2s": ms}
        print(f"{model.name} : EER {e:.1%}, refus à 1 % {f1:.0%}, {ms:.0f} ms")
    (RESULTS / "report.md").write_text("\n".join(lines))
    (RESULTS / "scores.json").write_text(json.dumps(raw, indent=1))
    print(f"\nRapport : {RESULTS / 'report.md'}")


if __name__ == "__main__":
    main()
