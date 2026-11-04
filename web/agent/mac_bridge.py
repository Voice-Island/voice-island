"""Bridge to the Voice Island Mac app (LiveKit RPC): the app is what drives Chrome.

macOS grants the right to drive Chrome to a whole application. Going through Voice Island avoids
granting it to Terminal (and so to everything running in it). Without the app (web page, tests), the calls
return None and the tools simply open a new tab.
"""

from __future__ import annotations

import json
import logging

from livekit import rtc
from livekit.agents import get_job_context

logger = logging.getLogger("voice.mac")

CHROME_METHOD = "mac.chrome"


def _client(room: rtc.Room) -> rtc.RemoteParticipant | None:
    return next((p for p in room.remote_participants.values()
                 if p.kind != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT), None)


async def _call(method: str, payload: dict, timeout: float = 8) -> dict | None:
    try:
        room = get_job_context().room
    except RuntimeError:
        return None
    client = _client(room)
    if client is None:
        return None
    try:
        reply = await room.local_participant.perform_rpc(
            destination_identity=client.identity, method=method, payload=json.dumps(payload),
            response_timeout=timeout)
        return json.loads(reply)
    except Exception as e:  # web client (unknown method), timeout…
        logger.debug("pont %s indisponible (%s)", method, e)
        return None


async def chrome(action: str, **fields) -> dict | None:
    """"active_tab" -> {running, front, url, title}; "set_url" (url=...) -> {ok}; "close_tab" -> {ok, title};
    "count" -> {windows, front_window_tabs}. None without Voice Island."""
    return await _call(CHROME_METHOD, {"action": action, **fields})


async def windows() -> dict[str, int] | None:
    """Number of open windows per application (display name -> count), or None without Voice Island."""
    reply = await _call("mac.windows", {}, timeout=4)
    return reply.get("windows") if reply else None


async def keys(action: str, bundle_id: str = "") -> dict | None:
    """Keys: play_pause, next_track, previous_track, youtube_fullscreen, new_window (bundle_id)."""
    return await _call("mac.keys", {"action": action, "bundle_id": bundle_id}, timeout=10)


async def active_tab() -> dict | None:
    """Chrome's active tab ({url, title, front}), or None if Chrome is closed / unreachable."""
    tab = await chrome("active_tab")
    if not tab or not tab.get("url"):
        return None
    return tab


async def place(bundle_id: str, where: str, screen: str = "current") -> dict | None:
    """Places an app's main window: where = left | right | full | center; screen = current | laptop | monitor.
    -> {ok, error}, or None without Voice Island."""
    return await _call("mac.place", {"bundle_id": bundle_id, "place": where, "screen": screen}, timeout=8)


async def text(action: str, value: str = "") -> dict | None:
    """"selection" -> {ok, app, text} (text selected in the foreground app); "insert" (value) -> {ok, app}.
    None without Voice Island."""
    return await _call("mac.text", {"action": action, "text": value}, timeout=6)


async def screen() -> dict | None:
    """Text of the foreground window -> {ok, app, source (accessibility | ocr), text}, or None without Voice Island."""
    return await _call("mac.screen", {}, timeout=10)


async def look(question: str, **fields) -> dict | None:
    """Captures the foreground window and has the local vision server look at it (the Mac app sends it
    itself: the image never goes through the agent) -> {ok, app, answer}, or None without Voice Island."""
    return await _call("mac.look", {"question": question, **fields}, timeout=60)


async def calendar(action: str, **fields) -> dict | None:
    """list (start, days) | add (title, start, minutes) | move (title, new_start) | delete (title) | add_reminder
    (title, start). Dates "yyyy-MM-dd HH:mm", local time. None without Voice Island."""
    return await _call("mac.calendar", {"action": action, **fields}, timeout=60)   # the confirmation waits for a click
