# "Listen only to my voice": measurements (Oct 31, 2026)

Scripts: `web/test/voice_id/` (`record.py` owner's voice, `room.py` voices replayed in the room,
`bench.py` comparison). The recordings and voiceprints stay in `web/test/voice_id/data/` (ignored by
git: biometric data); this document only contains the numbers.

## Data

- Owner: 3 readings of ~14 s (FR, EN, FR) for the voiceprint; 26 test sentences FR / EN / DE, including 11
  shorter than 1.5 s ("Pause", "Yes", "Stop"…), 2 at 1-2 m from the Mac, 2 in a low voice, 2 faster. MacBook Air
  mic, a single session, quiet room.
- Other voices: FLEURS (Google, CC-BY-4.0), 100 dev sentences per language (fr, en, de), cut to 1, 2 and 4 s (like
  the owner's sentences); 30 of these sentences **replayed through the speakers and re-recorded by the Mac's
  mic** (same mic, same room: the case of a video playing nearby); 35 sentences from macOS synthetic voices.
- Voiceprint: mean of the 3 s windows of the readings; score: cosine.

## Results

| | SpeechBrain ECAPA-TDNN | WeSpeaker ResNet34-LM (ONNX) |
| --- | --- | --- |
| License | Apache-2.0 | CC-BY-4.0 (attribution) |
| Compute for 2 s (M3 CPU) | **26 ms** | 67-87 ms |
| Owner's score (min / median) | 0.415 / 0.663 | 0.422 / 0.682 |
| Other FLEURS voices (max) | 0.433 (1 clip above the owner's worst score) | **0.364** |
| Voices replayed in the room (max) | 0.336 | **0.264** |
| Synthetic voices (max) | 0.158 | 0.293 |
| EER, all durations | 0.1 % | **0.0 %** |
| Owner's weakest sentences | "Yes" 0.4 s: 0.415; "Pause" 0.5 s: 0.463 | "Stop" 0.3 s: 0.422; "Next song" 1.0 s: 0.489 |

Low voice and 1-2 m distance: accepted by both models (not among the weakest scores, except one
low-voice sentence at 0.503 with ECAPA).

## Proposed choice

**WeSpeaker ResNet34-LM in ONNX**: full separation on this data (0.058 margin between the owner's worst score
and the best other voice), no extra PyTorch in the agent (onnxruntime is already there), 26 MB.
Starting threshold **0.39** (adjustable). The computation (~70-90 ms) can run at the same time as transcription:
no added delay. ECAPA remains the alternative (3 times faster, Apache-2.0, one FLEURS voice above the threshold).

## Limits of this measurement

- 26 sentences, one session, one mic: to redo with AirPods, on another day, in a noisy room.
- No **real person** speaking nearby on the same mic yet (the replayed voices come close to it).
- Isolated words shorter than half a second are the closest to the threshold: plan a rule (accept a short
  word if the previous sentence came from the owner, within the preceding seconds).
- This is not authentication: a recording or a clone of the voice (Qwen3-TTS can do it) gets through.
  Confirmations before risky actions (P0-6) are still required.
