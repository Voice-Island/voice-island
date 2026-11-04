"""Instant commands: what runs without the LLM, and above all what must keep going to the LLM."""

from pathlib import Path

import pytest

from fast_path import parse
from outcome import succeeded

APPS = {"google chrome": "Google Chrome", "chrome": "Google Chrome", "terminal": "Terminal", "notes": "Notes",
        "slack": "Slack", "spotify": "Spotify", "visual studio code": "Visual Studio Code",
        "vs code": "Visual Studio Code", "finder": "Finder"}


def resolve(name: str) -> Path | None:
    hit = APPS.get(name.strip().lower())
    return Path(f"/Applications/{hit}.app") if hit else None


def kinds(text: str, front: str = ""):
    actions = parse(text, resolve, lambda: front)
    if actions is None:
        return None
    return [(a["kind"], a.get("app").stem if a.get("app") else a.get("action") or a.get("query"),
             a.get("place"), a.get("screen")) for a in actions]


@pytest.mark.parametrize("text, expected", [
    ("Open Chrome.", [("open", "Google Chrome", None, "current")]),
    ("Can you open Spotify please?", [("open", "Spotify", None, "current")]),
    ("Open Chrome on the left.", [("open", "Google Chrome", "left", "current")]),
    ("Chrome on the left.", [("place", "Google Chrome", "left", "current")]),
    ("Put Terminal on the right side.", [("place", "Terminal", "right", "current")]),
    ("Move Notes to my monitor.", [("place", "Notes", "full", "monitor")]),
    ("Full screen Chrome on the big screen.", [("place", "Google Chrome", "full", "monitor")]),
    ("Maximize Slack.", [("place", "Slack", "full", "current")]),
    ("Chrome left, Terminal right.", [("place", "Google Chrome", "left", "current"),
                                      ("place", "Terminal", "right", "current")]),
    ("Open Chrome and Notes.", [("open", "Google Chrome", None, "current"), ("open", "Notes", None, "current")]),
    ("Close Slack.", [("close", "Slack", None, None)]),
    ("Quit Spotify.", [("close", "Spotify", None, None)]),
    ("Pause the music.", [("media", "pause", None, None)]),
    ("Skip this song.", [("media", "next", None, None)]),
    ("Search for best ramen in Lausanne.", [("search", "best ramen in lausanne", None, None)]),
    # Real sentences from the logs (route_report.py, Oct 26)
    ("Play Booba on YouTube.", [("youtube_play", "booba", None, None)]),
    ("Can you play a Michael Jackson song on YouTube?", [("youtube_play", "a michael jackson song", None, None)]),
    ("Search YouTube for lofi beats.", [("youtube_search", "lofi beats", None, None)]),
    ("Now pause the video", [("media", "pause", None, None)]),
    # New windows and French (Oct 29-31)
    ("Open a new terminal window.", [("new_window", "Terminal", None, "current")]),
    ("Open a new Chrome window on the right.", [("new_window", "Google Chrome", "right", "current")]),
    ("Mets Chrome à gauche.", [("place", "Google Chrome", "left", "current")]),
    ("Ouvre une nouvelle fenêtre Chrome à droite.", [("new_window", "Google Chrome", "right", "current")]),
    ("Mets Chrome à droite et Notes à gauche.", [("place", "Google Chrome", "right", "current"),
                                                ("place", "Notes", "left", "current")]),
    ("Ferme Slack.", [("close", "Slack", None, None)]),
    ("Put the VS Code Lumen on the left.", [("place", "Visual Studio Code", "left", "current")]),
])
def test_instant(text, expected):
    assert kinds(text) == expected


def test_it_means_the_front_app():
    assert kinds("Put it on the left.", front="Notes") == [("place", "Notes", "left", "current")]
    assert kinds("Put it on the left.", front="") is None
    assert kinds("Open a new window on the left.", front="Notes") == [("new_window", "Notes", "left", "current")]


@pytest.mark.parametrize("text", [
    "Open a new window of opportunity.",     # not an app
    "Open my downloads folder.",
    "Open youtube.com.",
    "Close this tab.",
    "Open the door.",                        # no app: above all not an approximate match
    "What's on my left screen?",
    "Play Billie Jean.",
    "Tell me a joke.",
    "Search for",                            # unfinished sentence
    "Google Chrome 1. Up.",                  # failed transcription: not a Google search
    "Play any or any song you like.",
    "Can you search on Youtube for a?",
    "Chrome on the left and tell Claude Code to run the tests.",
])
def test_goes_to_the_llm(text):
    assert kinds(text) is None


def test_llm_node_answers_instantly_without_the_model(monkeypatch):
    """Recognized command: run by fast_path, immediate reply, the LLM is never called."""
    import asyncio

    from livekit.agents.llm import ChatContext

    import agent

    def no_llm(cfg):
        raise AssertionError("le LLM ne doit pas être appelé")

    async def fake_run(actions):
        return succeeded("Quit Slack.", "Slack")

    async def no_publish(*a, **k):
        return None

    monkeypatch.setattr(agent, "llm_for", no_llm)
    monkeypatch.setattr(agent.fast_path, "parse", lambda *a, **k: [{"kind": "close", "app": Path("/Applications/Slack.app")}])
    monkeypatch.setattr(agent.fast_path, "run", fake_run)
    monkeypatch.setattr(agent, "publish_action", no_publish)
    a = agent.Assistant.__new__(agent.Assistant)
    a.command_mode, a.silent_prompt, a.typed, a._turn = False, agent.COMMAND_PROMPT, False, {}
    instructions = agent.compose_instructions(agent.load_settings())
    monkeypatch.setattr(agent.Assistant, "instructions", property(lambda self: instructions), raising=False)
    ctx = ChatContext()
    ctx.add_message(role="system", content=instructions)
    ctx.add_message(role="user", content="Close Slack.")

    async def collect():
        return "".join([c async for c in a.llm_node(ctx, [], None)])

    assert asyncio.run(collect()) == "Quit Slack."


def _early(monkeypatch):
    import asyncio

    import fast_path

    ran = []

    async def fake_run(actions):
        ran.append([fast_path.signature(a) for a in actions])
        return succeeded("Google Chrome is on the left.", "Google Chrome")

    async def no_front():
        return ""

    monkeypatch.setattr(fast_path, "run", fake_run)
    return fast_path.Early(resolve=resolve, front=no_front), ran, asyncio


def test_early_runs_a_stable_safe_command_once(monkeypatch):
    early, ran, asyncio = _early(monkeypatch)

    async def scenario():
        early.heard("Chrome on")
        await asyncio.sleep(0.05)
        early.heard("Chrome on the left")
        await asyncio.sleep(0.4)
        return early.already(parse("Chrome on the left.", resolve))

    todo, replies = asyncio.run(scenario())
    assert len(ran) == 1 and todo == [] and replies == ["Google Chrome is on the left."]


@pytest.mark.parametrize("partial", ["Close Slack", "Pause the music", "Chrome on the", "Search for ramen"])
def test_early_never_runs_unsafe_or_unfinished(monkeypatch, partial):
    early, ran, asyncio = _early(monkeypatch)

    async def scenario():
        early.heard(partial)
        await asyncio.sleep(0.4)

    asyncio.run(scenario())
    assert ran == []
