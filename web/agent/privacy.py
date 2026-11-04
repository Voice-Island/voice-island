"""What the logs keep of what you say (P0-7).

The service logs (web/logs/*.log) are for understanding a failure. By default, they no longer contain what
the user says, dictates or searches, nor the replies: `privacy.log_transcripts` (off by default) replaces
them with their length, "‹42 car.›". To debug: Dashboard › Privacy, or
VOICE_LOG_TRANSCRIPTS=1 ./dev.sh (takes priority over the setting).

Two sources of personal text in the logs:
- our own lines (agent, tools, voice server): pass the text through `r()`;
- LiveKit Agents, which marks conversation and tool text with `lk.pii.*` fields ("pii" = personal
  data): `install()` masks them in every log record, whatever the module.

The history (web/history/, `privacy.keep_history`) is a visible feature, set separately: see history.py.
"""
from __future__ import annotations

import logging
import os
import time

_cache = {"at": -1e9, "on": False}
MASK = "‹masqué›"


def log_transcripts() -> bool:
    env = os.environ.get("VOICE_LOG_TRANSCRIPTS")
    if env in ("0", "1"):
        return env == "1"
    now = time.monotonic()
    if now - _cache["at"] > 5:   # reread every 5 s at most: a dashboard change applies quickly
        try:
            from settings import load_settings

            _cache["on"] = bool(load_settings().get("privacy", {}).get("log_transcripts", False))
        except Exception:   # unreadable settings: stay discreet
            _cache["on"] = False
        _cache["at"] = now
    return _cache["on"]


def r(text: object) -> object:
    """The text as is if transcripts are allowed in the logs, otherwise its length."""
    if log_transcripts():
        return text
    s = "" if text is None else str(text)
    return f"‹{len(s)} car.›" if s else s


def _is_pii(key: str) -> bool:
    return "pii" in key.split(".")


def scrub(record: logging.LogRecord) -> logging.LogRecord:
    if not log_transcripts():
        for key in [k for k in vars(record) if _is_pii(k)]:
            setattr(record, key, MASK)
    return record


_installed = False


def install() -> None:
    """Masks the lk.pii.* fields of every log record created in this process.

    Extras (`extra={...}`) are added by Logger.makeRecord after the record factory: so it is
    makeRecord that we wrap. LiveKit's conversation processes also import agent.py, so they apply it too.
    """
    global _installed
    if _installed:
        return
    make = logging.Logger.makeRecord

    def make_record(self, *args, **kwargs):
        return scrub(make(self, *args, **kwargs))

    logging.Logger.makeRecord = make_record
    _installed = True
