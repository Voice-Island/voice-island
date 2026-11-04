"""Rereads logs/agent.log: one line per event of each conversation (question, tool, result, answer).

  python3 -I test/conversations.py [number of conversations]   (default: the last 6, test rooms excluded)
"""

import json
import re
import sys
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "logs" / "agent.log"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ - (\w+) ([\w.]+) - (.*?) (\{.*\})?$")


def main() -> None:
    wanted = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    rooms: dict[str, list[str]] = {}
    for raw in LOG.read_text(errors="replace").splitlines():
        m = LINE.match(ANSI.sub("", raw))
        if not m:
            continue
        when, level, logger, msg, extra = m.groups()
        try:
            info = json.loads(extra) if extra else {}
        except json.JSONDecodeError:
            info = {}
        room = info.get("room", "")
        if not room or "check" in room or room.startswith("test-"):
            continue
        t = when[11:]
        if msg == "conversation_item_added" and info.get("role") == "user":
            event = f"{t}  TOI      {info.get('lk.pii.text', '')}"
        elif msg == "executing tool":
            event = f"{t}  OUTIL    {info.get('function')}({info.get('lk.pii.arguments', '')})"
        elif logger == "voice.tools":
            event = f"{t}    -> {msg}"
        elif msg.startswith("🤖"):
            event = f"{t}  ELLE     {msg[2:].strip()}"
        elif msg.startswith(("nouvelle conversation", "réponses ", "claude code")):
            event = f"{t}  ·        {msg}"
        elif level in ("ERROR",) or "timed out" in msg:
            event = f"{t}  !        {msg[:160]}"
        else:
            continue
        rooms.setdefault(f"{when[:10]} {room}", []).append(event)
    for name in list(rooms)[-wanted:]:
        print(f"\n=== {name}")
        print("\n".join(rooms[name]))


if __name__ == "__main__":
    main()
