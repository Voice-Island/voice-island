"""Extracts from the agent log: questions, spoken sentences (with their tone) and measurements of each turn."""
import re
import sys

raw = open(sys.argv[1] if len(sys.argv) > 1 else "logs/agent.log", "rb").read().decode("utf-8", "replace")
raw = re.sub(r"\x1b\[[0-9;]*m", "", raw)
for m in re.finditer(r'"lk.pii.user_transcript": "(.*?)"', raw):
    print("USER:", m.group(1))
for m in re.finditer(r"voice\.tts - tts (\S+) « (.*?) » \[(.*?)\]", raw, re.S):
    print(f"  TTS [{m.group(3)}] {m.group(2)}")
for m in re.finditer(r"voice\.agent - 🤖 ([^\n]*)", raw):
    print("  RÉPONSE:", m.group(1).strip()[:200])
for m in re.finditer(r"voice\.agent - nouvelle conversation \(([^)]*)\)", raw):
    print("MODE:", m.group(1))
for m in re.finditer(r"tour : (\{.*?\})", raw):
    print("TOUR:", m.group(1))
for m in re.finditer(r"(Traceback|ERROR)[^\n]{0,200}", raw):
    print("ERR:", m.group(0)[:200])
