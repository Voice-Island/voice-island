"""Replays the sentences actually spoken (agent logs) through the routing, without executing anything.

For each sentence: instant (fast_path), learned shortcut, memory ("remember…") or LLM. Sentences that
look like commands but still go to the LLM are listed: they are the next patterns to add.
  cd web/agent && uv run python ../test/route_report.py
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import fast_path  # noqa: E402
import learned  # noqa: E402
import memory_rules  # noqa: E402

LOGS = Path(__file__).resolve().parent.parent / "logs"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
USER = re.compile(r'"role": "user", "lk\.pii\.text": "((?:[^"\\]|\\.)*)"')
COMMAND = re.compile(r"^(?:can you |please )?(open|close|quit|put|move|snap|play|pause|skip|search|show|launch|"
                     r"start|maximize|full screen|remind|add|set)\b", re.I)


def utterances() -> list[str]:
    seen, out = set(), []
    for path in sorted(LOGS.glob("agent*.log*")):
        for raw in path.read_text(errors="replace").splitlines():
            line = ANSI.sub("", raw)
            if "check" in line or "test-" in line:
                continue
            m = USER.search(line)
            if m:
                text = json.loads(f'"{m[1]}"').strip()
                if text and text not in seen:
                    seen.add(text)
                    out.append(text)
    return out


def route(text: str) -> str:
    if memory_rules.REMEMBER.match(memory_rules.plain(text).lower()) or memory_rules.LIST.match(text.lower()):
        return "mémoire"
    if learned.named(text) or learned.forget_request(text) or learned.find(text):
        return "raccourci"
    if fast_path.parse(text, fast_path.resolve_strict, lambda: "Google Chrome"):
        return "instantané"
    return "LLM"


def main() -> None:
    phrases = utterances()
    routes = {p: route(p) for p in phrases}
    counts = Counter(routes.values())
    print(f"{len(phrases)} phrases distinctes : " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
    print("\nInstantanées :")
    for p, r in routes.items():
        if r == "instantané":
            print(f"  ⚡ {p}")
    print("\nCommandes encore envoyées au LLM (motifs à ajouter ?) :")
    for p, r in routes.items():
        if r == "LLM" and COMMAND.match(p):
            print(f"  · {p}")


if __name__ == "__main__":
    main()
