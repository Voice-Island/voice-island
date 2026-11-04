"""Confirmation of risky actions (P0-6).

Open, place, search, type at the cursor: no confirmation. Anything that leaves the Mac or cannot be undone
(send, publish, delete, Claude Code allowed to modify the project, MCP tools not declared safe) is not
executed: the tool returns a "proposed" `Outcome`, the frame shows Yes / No, and the action only runs on a yes
from the user themselves: their sentence (intercepted before the LLM) or a click. Never on text from the LLM, a
tool result or a page: the LLM calling the tool again only proposes it again.
"""
from __future__ import annotations

import functools
import itertools
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum

from outcome import Outcome, Status, failed
from privacy import r

logger = logging.getLogger("policy")


class Risk(StrEnum):
    READ = "read"                # only reads
    REVERSIBLE = "reversible"    # changes the Mac's state, can be undone (open, place, type at the cursor)
    EXTERNAL = "external"        # leaves the Mac: send, publish
    DESTRUCTIVE = "destructive"  # cannot be undone: delete, overwrite


CONFIRM = {Risk.EXTERNAL, Risk.DESTRUCTIVE}
EXPIRES = 120.0   # seconds: a later "yes" no longer counts for this proposal


@dataclass
class Proposal:
    id: str
    tool: str
    summary: str                                  # what the user approves, in one line
    run: Callable[[], Awaitable[Outcome]] = field(repr=False)
    created: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.created > EXPIRES

    def message(self) -> str:
        """What the LLM reads instead of the result."""
        return (f"NOT DONE: this needs the user's confirmation first. Ask them in one short question ending with "
                f"'?': {self.summary}. Never say it is done or sent. Their yes or no is handled for you.")


_ids = itertools.count(1)
_pending: Proposal | None = None


def propose(tool: str, summary: str, run: Callable[[], Awaitable[Outcome]]) -> Outcome:
    """Puts the action on hold (it replaces any older proposal) -> "proposed" Outcome."""
    global _pending
    _pending = Proposal(id=f"p{next(_ids)}", tool=tool, summary=summary, run=run)
    logger.info("🛑 à confirmer [%s] %s : %s", _pending.id, tool, r(summary))
    return Outcome(_pending.message(), Status.PROPOSED, _pending.id)


def pending() -> Proposal | None:
    global _pending
    if _pending is not None and _pending.expired:
        logger.info("proposition %s expirée", _pending.id)
        _pending = None
    return _pending


def cancel(reason: str = "") -> Proposal | None:
    global _pending
    dropped, _pending = _pending, None
    if dropped is not None:
        logger.info("proposition %s annulée %s", dropped.id, reason)
    return dropped


async def approve(proposal_id: str | None = None) -> Outcome | None:
    """Runs the pending action (only once). None if there is none, or no longer one (expired, other id)."""
    global _pending
    current = pending()
    if current is None or (proposal_id is not None and proposal_id != current.id):
        return None
    _pending = None
    logger.info("✅ confirmé [%s] %s", current.id, current.tool)
    try:
        result = await current.run()
    except Exception as e:   # the user must know it did not work, even on an unexpected error
        logger.exception("action confirmée en échec")
        return failed(f"{current.summary}: failed ({e}).", current.tool)
    return result if isinstance(result, Outcome) else Outcome(str(result), Status.ATTEMPTED, current.tool)


def spoken(result: Outcome) -> str:
    """Short sentence after a confirmed action (some tools' text is addressed to the LLM, not to the user)."""
    if result.failed:
        return str(result)[:200]
    if result.status is Status.ATTEMPTED:
        return "Sent. I'll tell you when it's done."
    first = next((line.strip() for line in str(result).splitlines() if line.strip()), "")
    return f"Done. {first[:160]}".strip()


# MARK: - User's yes / no

YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed", "go", "go ahead", "do it",
       "send it", "please do", "yes please", "yes go ahead", "yes do it", "yes send it", "ok go", "okay go ahead",
       "oui", "ouais", "vas-y", "vas y", "allez", "allez-y", "allez y", "d'accord", "ok vas-y", "oui vas-y",
       "oui vas y", "c'est bon", "confirme", "je confirme", "oui je confirme", "envoie", "envoie-le", "fais-le",
       "oui fais-le", "oui envoie", "oui s'il te plaît", "oui merci", "go ahead please"}
NO = {"no", "nope", "cancel", "stop", "don't", "do not", "no thanks", "no thank you", "never mind", "cancel it",
      "don't do it", "don't send it", "non", "annule", "annuler", "non merci", "laisse tomber", "arrête", "non annule",
      "ne le fais pas", "n'envoie pas", "non n'envoie pas"}


def answer(text: str) -> str | None:
    """"yes" / "no" when the whole sentence is only a yes or a no, otherwise None (another request).
    Strict on purpose: "yes but in plan mode" is not a yes."""
    t = re.sub(r"[.!?,;:…\s]+", " ", text.lower().replace("’", "'")).strip()
    t = re.sub(r"\b(please|s'il te plaît|s'il vous plaît)$", "", t).strip() or t
    if t in YES:
        return "yes"
    if t in NO:
        return "no"
    return None


# MARK: - Local tools


def confirm_first(check: Callable[..., str | None]):
    """To be placed under `@tracked`: `check(**arguments)` returns the summary to get approved, or None to run
    directly (e.g. Claude Code in read-only mode)."""

    def decorate(fn):
        @functools.wraps(fn)
        async def wrapper(context, *args, **kwargs):
            summary = check(*args, **kwargs)
            if summary is None:
                return await fn(context, *args, **kwargs)
            return propose(fn.__name__, summary, lambda: fn(context, *args, **kwargs))

        return wrapper

    return decorate
