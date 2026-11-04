""""Remember…": rules and facts the user asks to keep, recalled to the LLM at every question.

Idea taken from Atticus (MIT). Saved without the LLM ("Remember that my work account is Acme"), recalled in the
system note of every turn (not in the system prompt: it would stay cached even after a forget). "Forget that
…" removes the closest rule, "What do you remember?" lists them. Stored next to the settings
(web/memory.json, outside git), editable from the dashboard.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import settings

# "note" alone is not enough: Parakeet heard "Note à droite" for "Mets Notes à droite" (Oct 29).
REMEMBER = re.compile(r"^(?:please )?(?:remember|note that|take note(?: that)?|keep in mind|don't forget|"
                      r"souviens-toi|rappelle-toi|retiens|n'oublie pas)(?: that| que| qu')?[,:]?\s*(.{4,})$")
FORGET = re.compile(r"^(?:please )?forget (?:that|about)\s+(.+)$")
LIST = re.compile(r"^what (?:do you|did i ask you to) remember(?: about me)?$")
LIMIT = 20


def path() -> Path:
    return Path(settings.SETTINGS_PATH).with_name("memory.json")


def rules() -> list[dict]:
    try:
        return json.loads(path().read_text())
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    path().write_text(json.dumps(items, indent=2, ensure_ascii=False))


def plain(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(".!?")).strip()


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 2}


def handle(text: str) -> str | None:
    """Reply if the sentence is about memory (remember, forget, list), None otherwise."""
    t = plain(text)
    low = t.lower()
    if LIST.match(low):
        items = rules()
        if not items:
            return "Nothing yet. Say “remember that…” and I will keep it."
        return "I remember: " + "; ".join(r["text"] for r in items) + "."
    m = FORGET.match(low)
    if m:
        wanted = _words(m[1])
        items = rules()
        best = max(items, key=lambda r: len(wanted & _words(r["text"])), default=None)
        if not best or not wanted & _words(best["text"]):
            return "I had nothing like that in memory."
        items.remove(best)
        _save(items)
        return f"Forgot: {best['text']}."
    m = REMEMBER.match(low)
    if m:
        fact = t[len(t) - len(m[1]):]   # keeps the user's capitalization
        fact = fact[0].upper() + fact[1:]
        items = [r for r in rules() if r["text"].lower() != fact.lower()]
        items.append({"text": fact, "created": time.strftime("%Y-%m-%d %H:%M")})
        _save(items[-LIMIT:])
        return f"Okay, I will remember: {fact}."
    return None


def note() -> str:
    """Line added to the system note of every turn."""
    items = rules()
    if not items:
        return ""
    return "Things the user asked you to remember (follow them): " + "; ".join(r["text"] for r in items) + "."
