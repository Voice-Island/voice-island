"""Records the owner's voice to measure speaker verification (about 4 minutes).

  cd web/test/voice_id && uv run record.py

Three long readings serve as the voiceprint (enroll/), then short, medium and long sentences serve as tests
(test/), some of them from further away and in a low voice. Each take: Enter to start, Enter to stop.
Everything stays in data/owner/ (ignored by git: it is biometric data). Ctrl+C stops; running again resumes
where it left off.
"""

import json
import sys
import termios
import textwrap
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

RATE = 16000
OUT = Path(__file__).resolve().parent / "data" / "owner"

ENROLL = [
    ("fr", 14, "Bonjour, je m'appelle comme je m'appelle et voici ma voix. J'utilise cet assistant tous les jours "
               "pour ouvrir mes applications, écrire mes messages et chercher des informations sur le web."),
    ("en", 14, "Hello, this is my normal speaking voice. I use this assistant every day to open my apps, move my "
               "windows around, search the web and play some music while I work."),
    ("fr", 14, "Le matin, je commence par lire mes emails, puis je prépare mes présentations pour les clients. "
               "Ensuite je passe des appels et je prends des notes pendant les réunions."),
]

# (language, recording duration in seconds, instruction, text). Surrounding silences are removed at analysis.
TESTS = [
    ("fr", 3, "", "Pause."), ("fr", 3, "", "Suivant."), ("en", 3, "", "Yes."), ("en", 3, "", "Next song."),
    ("fr", 3, "", "Ferme Notes."), ("en", 3, "", "Open Chrome."), ("fr", 3, "", "Oui, vas-y."),
    ("en", 3, "", "Stop."),
    ("fr", 5, "", "Mets Chrome à gauche et Notes à droite."), ("en", 5, "", "What time is it right now?"),
    ("fr", 5, "", "Ouvre une nouvelle fenêtre à droite."), ("en", 5, "", "Search the web for the weather in Geneva."),
    ("fr", 5, "", "Est-ce que tu peux fermer WhatsApp ?"), ("en", 5, "", "Put the email on the left please."),
    ("de", 5, "", "Kannst du bitte die Musik anhalten?"), ("fr", 5, "", "Lance YouTube et cherche du jazz."),
    ("fr", 9, "", "Écris un message pour dire que je serai en retard d'une dizaine de minutes à la réunion."),
    ("en", 9, "", "Can you summarize the selected text and tell me the three most important points?"),
    ("fr", 9, "", "Ajoute un rendez-vous demain à quatorze heures avec l'équipe pour parler du projet."),
    ("en", 9, "", "Open a new terminal window on the right and start Claude Code in my project folder."),
    ("fr", 5, "Recule-toi à 1 ou 2 mètres du Mac.", "Mets la musique en pause."),
    ("en", 5, "Toujours à 1 ou 2 mètres.", "Close the current window."),
    ("fr", 5, "Reviens près du Mac, et parle à voix basse.", "Ouvre mes téléchargements."),
    ("en", 5, "Toujours à voix basse.", "What's on my calendar today?"),
    ("fr", 5, "Voix normale, un peu plus vite que d'habitude.", "Ferme Chrome et ouvre Safari tout de suite."),
    ("en", 5, "Voix normale.", "Turn the volume down to thirty percent."),
]


def ask(prompt: str) -> str:
    """Waits for Enter; presses made ahead of time are ignored (otherwise the next sentence started on its own)."""
    termios.tcflush(sys.stdin, termios.TCIFLUSH)
    return input(prompt).strip().lower()


def show(text: str) -> None:
    """The sentence to say, framed, clearly visible."""
    width = min(max(len(text) + 4, 30), 100)
    print("  ┌" + "─" * width + "┐")
    for line in textwrap.wrap(text, width - 4):
        print(f"  │  {line:<{width - 4}}  │")
    print("  └" + "─" * width + "┘")


def record(path: Path, text: str) -> float:
    """Records from Enter to Enter, with the sentence shown during the take. -> peak level."""
    show(text)
    ask("  Entrée pour commencer… ")
    chunks: list[np.ndarray] = []
    with sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=lambda d, *_: chunks.append(d.copy())):
        ask(f"  🎙  DIS MAINTENANT : « {text} »\n     puis Entrée pour arrêter ")
    audio = np.concatenate(chunks)[:, 0] if chunks else np.zeros(RATE, dtype=np.float32)
    sf.write(path, audio, RATE)
    peak = float(np.abs(audio).max())
    print(f"  enregistré ({len(audio) / RATE:.1f} s, niveau {peak:.2f})")
    return peak


def record_checked(path: Path, text: str) -> None:
    """Redoes the take as long as the sound is almost zero (mic taken by another app, too far away…)."""
    while record(path, text) < 0.02:
        if ask("  ⚠️  presque aucun son : Entrée pour refaire, « g » pour garder quand même ") == "g":
            return


def main() -> None:
    (OUT / "enroll").mkdir(parents=True, exist_ok=True)
    (OUT / "test").mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"enroll": {}, "test": {}}
    print(f"Micro : {sd.query_devices(kind='input')['name']}\n")
    print("Pour chaque texte : Entrée pour commencer, tu parles, Entrée pour arrêter. Ctrl+C pour tout arrêter.\n")

    for i, (lang, _, text) in enumerate(ENROLL):
        name = f"{i:02d}_{lang}.wav"
        if name in manifest["enroll"]:
            continue
        print(f"\n━━━ Empreinte {i + 1}/{len(ENROLL)} : lis ce texte en entier")
        record_checked(OUT / "enroll" / name, text)
        manifest["enroll"][name] = {"lang": lang, "text": text}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    print("\nPhrases de test.\n")
    for i, (lang, _, hint, text) in enumerate(TESTS):
        name = f"{i:02d}_{lang}.wav"
        if name in manifest["test"]:
            continue
        print(f"\n━━━ Phrase {i + 1}/{len(TESTS)}")
        if hint:
            print(f"  👉 {hint}")
        record_checked(OUT / "test" / name, text)
        manifest["test"][name] = {"lang": lang, "text": text, "hint": hint}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    print(f"\nTerminé : {len(manifest['enroll'])} lectures et {len(manifest['test'])} phrases dans {OUT}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nArrêté. Relance pour reprendre là où tu étais.")
