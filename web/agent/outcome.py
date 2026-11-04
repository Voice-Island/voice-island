"""Typed result of an action (P0-5).

A tool returns an `Outcome`: it is a text (what the LLM reads, unchanged) that also carries a status and the
targeted resource. The status replaces reading the start of the text ("Could not…", "No application…"), which
let failures through in green ("Brought Notes to the front but could not open a new window").

LiveKit converts a tool's value to plain text before the `function_tools_executed` event: so the `tracked`
decorator keeps the result by call id, and `take()` hands it back to the agent.
"""
from __future__ import annotations

import functools
import logging
from collections import OrderedDict
from enum import StrEnum

logger = logging.getLogger("outcome")


class Status(StrEnum):
    PROPOSED = "proposed"     # waiting for the user's confirmation (P0-6)
    APPROVED = "approved"     # confirmed, not started yet (P0-6)
    ATTEMPTED = "attempted"   # started, with no way to know if it succeeded (background task, unknown tool)
    SUCCEEDED = "succeeded"   # the command succeeded
    VERIFIED = "verified"     # succeeded, and the result was observed (e.g. the new window appeared)
    FAILED = "failed"         # nothing was done, or not what was asked


class Outcome(str):
    """Result text + status + resource ("Notes", a URL…). Any text operation (f-string,
    `replace`, `join`) returns a plain `str`: build a new `Outcome` to keep the status."""

    status: Status
    resource: str

    def __new__(cls, message: str, status: Status, resource: str = "") -> Outcome:
        self = super().__new__(cls, message)
        self.status = Status(status)
        self.resource = resource
        return self

    @property
    def failed(self) -> bool:
        return self.status is Status.FAILED

    @property
    def ok(self) -> bool:
        """Not a failure: done, verified, started or pending. What shows in orange is `failed`."""
        return not self.failed

    @property
    def done(self) -> bool:
        """Done (or verified): only what is certain can become a learned shortcut."""
        return self.status in (Status.SUCCEEDED, Status.VERIFIED)

    def with_message(self, message: str) -> Outcome:
        return Outcome(message, self.status, self.resource)

    def __repr__(self) -> str:
        return f"Outcome({str(self)!r}, {self.status.value}{', ' + repr(self.resource) if self.resource else ''})"


def succeeded(message: str, resource: str = "") -> Outcome:
    return Outcome(message, Status.SUCCEEDED, resource)


def verified(message: str, resource: str = "") -> Outcome:
    return Outcome(message, Status.VERIFIED, resource)


def attempted(message: str, resource: str = "") -> Outcome:
    return Outcome(message, Status.ATTEMPTED, resource)


def failed(message: str, resource: str = "") -> Outcome:
    return Outcome(message, Status.FAILED, resource)


# From least to most certain: a set of actions is worth the least certain of them.
_RANK = [Status.FAILED, Status.PROPOSED, Status.APPROVED, Status.ATTEMPTED, Status.SUCCEEDED, Status.VERIFIED]


def combine(outcomes: list[Outcome], sep: str = " ") -> Outcome:
    """Several actions from one sentence ("Chrome à gauche et Notes à droite") -> a single result."""
    if not outcomes:
        return succeeded("")
    worst = min((o.status for o in outcomes), key=_RANK.index)
    return Outcome(sep.join(o for o in outcomes if o), worst, ", ".join(o.resource for o in outcomes if o.resource))


def coerce(value) -> Outcome:
    """Value of a tool that has no status (yet): started, nothing more."""
    return value if isinstance(value, Outcome) else attempted(str(value))


# MARK: - Results per tool call

_by_call: OrderedDict[str, Outcome] = OrderedDict()
_KEEP = 256


def tracked(fn):
    """To be placed under `@function_tool`: keeps the call's result (LiveKit id) for `take()`.
    Called without a context (fast_path, learned shortcuts), the tool simply returns its `Outcome`."""

    @functools.wraps(fn)
    async def wrapper(context, *args, **kwargs):
        result = await fn(context, *args, **kwargs)
        if not isinstance(result, Outcome):
            logger.warning("%s a rendu un texte sans statut : %r", fn.__name__, str(result)[:80])
            result = coerce(result)
        call_id = _call_id(context)
        if call_id:
            _by_call[call_id] = result
            while len(_by_call) > _KEEP:
                _by_call.popitem(last=False)
        return result

    return wrapper


def _call_id(context) -> str:
    try:
        return context.function_call.call_id if context is not None else ""
    except (AttributeError, RuntimeError):
        return ""


def take(call_id: str) -> Outcome | None:
    """Typed result of a call (only once), or None for a tool that does not return one (MCP servers)."""
    return _by_call.pop(call_id, None)


def for_call(call_id: str, text: str, is_error: bool) -> Outcome:
    """Result of a call as seen by the agent: the tool's status, otherwise (MCP) error -> failed, else -> attempted."""
    found = take(call_id)
    if found is not None:
        return found
    return failed(text) if is_error else attempted(text)
