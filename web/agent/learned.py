"""Learned shortcuts: a request that succeeded once is replayed afterwards without the LLM (~0.1 s).

Idea taken from Atticus (MIT). Two ways to learn:
- "When I say focus mode, close Slack and open Notes on the left": the shortcut is named by the user;
- a request the LLM solved with replayable tools (open, place, quit, site, folder…), all
  successful: the same request, said later with the same meaningful words (in any order), is replayed.
"Forget focus mode" erases it. Stored next to the settings (web/learned.json, outside git).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import settings
from outcome import Outcome, combine, failed

# Tools whose result does not depend on the moment: replaying them does exactly the same thing again.
REPLAYABLE = {"open_app", "quit_app", "place_window", "open_website", "open_folder", "set_volume", "search_web",
              "open_terminal"}
FILLER = {"please", "can", "could", "would", "will", "you", "the", "a", "an", "my", "me", "for", "to", "on", "in",
          "and", "then", "now", "just", "hey", "ok", "okay", "so", "um", "uh", "i", "want", "like", "go", "ahead",
          "of", "up", "thanks", "thank"}
# "it", "this", "the first one": depends on the current screen, never replayed.
DEICTIC = re.compile(r"\b(it|this|that|these|those|here|there|first|second|last|next|previous|again)\b")
NAMED = re.compile(r"^(?:when(?:ever)?|if) i say (.+?)(?:,|:|\s+(?=open|close|quit|put|move|play|search|start|"
                   r"launch|show|set|go))\s*(?:you should |please |just )?(.+)$")
FORGET = re.compile(r"^(?:forget|delete|remove)(?: the)?(?: shortcut)?\s+(.+?)(?: shortcut)?$")


def path() -> Path:
    return Path(settings.SETTINGS_PATH).with_name("learned.json")


def load() -> dict:
    try:
        return json.loads(path().read_text())
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    path().write_text(json.dumps(data, indent=2, ensure_ascii=False))


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", text.lower().replace("’", "'"))).strip()


def gist(text: str) -> str:
    """The words that matter, unordered: "can you put Chrome on the left" == "Chrome left"."""
    return " ".join(sorted({w for w in plain(text).split() if w not in FILLER}))


def find(text: str) -> dict | None:
    key = gist(text)
    return load().get(key) if key else None


def learn(phrase: str, calls: list[dict], named: bool = False) -> bool:
    """calls: [{"name": tool, "args": {...}}]. Refused if a tool is not replayable or if the phrase depends
    on the screen ("it", "the first one")."""
    key = gist(phrase)
    if not key or not calls or any(c["name"] not in REPLAYABLE for c in calls):
        return False
    if not named and DEICTIC.search(plain(phrase)):
        return False
    data = load()
    old = data.get(key, {})
    data[key] = {"phrase": phrase.strip(" .!?"), "calls": calls, "named": named or old.get("named", False),
                 "uses": old.get("uses", 0), "created": old.get("created", time.strftime("%Y-%m-%d %H:%M"))}
    save(data)
    return True


def forget(name: str) -> str | None:
    data = load()
    key = gist(name)
    if key not in data:
        return None
    phrase = data.pop(key)["phrase"]
    save(data)
    return phrase


def named(text: str) -> tuple[str, str] | None:
    """"When I say focus mode, close Slack and open Notes" -> ("focus mode", "close Slack and open Notes")."""
    m = NAMED.match(plain(text))
    if not m:
        return None
    return m[1].strip(), m[2].strip()


def forget_request(text: str) -> str | None:
    m = FORGET.match(plain(text))
    return m[1].strip() if m else None


def calls_from_actions(actions: list[dict]) -> list[dict]:
    """fast_path actions -> replayable tool calls (None if one is not, e.g. pause, which toggles)."""
    calls = []
    for a in actions:
        app = a.get("app")
        if a["kind"] == "open" and a.get("place"):
            calls.append({"name": "place_window", "args": {"name": app.stem, "where": a["place"], "screen": a["screen"]}})
        elif a["kind"] == "open":
            calls.append({"name": "open_app", "args": {"name": app.stem}})
        elif a["kind"] == "place":
            calls.append({"name": "place_window", "args": {"name": app.stem, "where": a["place"], "screen": a["screen"]}})
        elif a["kind"] == "close":
            calls.append({"name": "quit_app", "args": {"name": app.stem}})
        elif a["kind"] == "search":
            calls.append({"name": "search_web", "args": {"query": a["query"]}})
        else:
            return []
    return calls


async def replay(entry: dict) -> Outcome:
    """Replays the saved calls with the usual tools. -> a short reply, with the status of the least certain one."""
    import tools

    replies: list[Outcome] = []
    for call in entry["calls"]:
        tool = tools.ALL_TOOLS.get(call["name"])
        if tool is None:
            return failed(f"The tool {call['name']} no longer exists.")
        replies.append(await tool(None, **call["args"]))
    data = load()
    key = gist(entry["phrase"])
    if key in data:
        data[key]["uses"] = data[key].get("uses", 0) + 1
        save(data)
    return combine(replies)
