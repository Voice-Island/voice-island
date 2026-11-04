"""User history (P0-7): what the user said or typed, the replies, what was actually done, and their
dictations. Kept by default (`privacy.keep_history`), for at most `privacy.history_days` days (30), erasable with one
button (Dashboard › Privacy). Unlike the service logs, which dev.sh rotates, it is not lost on restart.

One file per day, one JSON line per event: web/history/2026-10-31.jsonl
  {"t": "2026-10-31T17:18:13", "product": "command", "session": "command_room_745", "role": "user", "text": "…"}
role: user | assistant | action (with "status") | dictation. The Mac app writes dictations in the same format.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("history")

HISTORY = Path(__file__).resolve().parent.parent / "history"


def product_of(room: str) -> str:
    if room.startswith("chat_room_"):
        return "chat"
    if room.startswith("command_room_"):
        return "command"
    return "assistant"


def is_test(room: str) -> bool:
    """Rooms of the test scripts (web/test/*_check.py): never in the history."""
    return "check" in room or room.startswith("test-")


def _privacy() -> dict:
    from settings import load_settings

    return load_settings().get("privacy", {})


def keep() -> bool:
    return bool(_privacy().get("keep_history", True))


_pruned: dict[Path, str] = {}


def prune(folder: Path = HISTORY, days: float | None = None, today: datetime | None = None) -> list[Path]:
    """Deletes days older than privacy.history_days (30 by default). Once per day and per folder."""
    days = float(_privacy().get("history_days", 30) if days is None else days)
    today = today or datetime.now()
    removed = []
    for f in folder.glob("*.jsonl"):
        try:
            age = (today - datetime.strptime(f.stem, "%Y-%m-%d")).days
        except ValueError:
            continue
        if days > 0 and age >= days:
            f.unlink(missing_ok=True)
            removed.append(f)
    return removed


def record(session: str, role: str, text: str, status: str | None = None, folder: Path | None = None) -> None:
    text = (text or "").strip()
    if not text or is_test(session):
        return
    try:
        if not keep():
            return
        now = datetime.now()
        entry = {"t": now.isoformat(timespec="seconds"), "product": product_of(session), "session": session,
                 "role": role, "text": text}
        if status:
            entry["status"] = status
        folder = folder or HISTORY
        folder.mkdir(parents=True, exist_ok=True)
        if _pruned.get(folder) != f"{now:%Y-%m-%d}":
            prune(folder, today=now)
            _pruned[folder] = f"{now:%Y-%m-%d}"
        with (folder / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:   # the history must never break a conversation
        logger.warning("historique non écrit : %s", e)
