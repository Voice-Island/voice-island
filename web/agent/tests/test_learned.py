"""Learned shortcuts: what is kept, what is not, and replay without the LLM."""

import asyncio
from pathlib import Path

import pytest

import learned
from outcome import succeeded

CALLS = [{"name": "quit_app", "args": {"name": "Slack"}},
         {"name": "place_window", "args": {"name": "Notes", "where": "left", "screen": "current"}}]


def test_same_words_in_any_order_find_the_shortcut():
    assert learned.learn("Put Notes on the left and quit Slack", CALLS)
    assert learned.find("quit slack and put notes left please")["calls"] == CALLS
    assert learned.find("put notes on the right") is None


def test_named_shortcut_and_forget():
    assert learned.named("When I say focus mode, close Slack and open Notes on the left.") == (
        "focus mode", "close slack and open notes on the left")
    assert learned.named("When I say study open Notes") == ("study", "open notes")
    assert learned.learn("focus mode", CALLS, named=True)
    assert learned.find("Focus mode.")["named"]
    assert learned.forget_request("Forget focus mode.") == "focus mode"
    assert learned.forget("focus mode") == "focus mode"
    assert learned.find("focus mode") is None


@pytest.mark.parametrize("phrase, calls", [
    ("Put it on the left", CALLS),                                   # depends on the screen
    ("Play the first video", [{"name": "play_youtube_video", "args": {"position": 1}}]),
    ("Pause", [{"name": "media_control", "args": {"action": "pause"}}]),   # toggles: never replayed
    ("Draft an email to Paul", [{"name": "draft_email", "args": {}}]),
])
def test_not_learned(phrase, calls):
    assert not learned.learn(phrase, calls)


def test_calls_from_fast_actions():
    app = Path("/Applications/Notes.app")
    assert learned.calls_from_actions([{"kind": "open", "app": app, "place": "left", "screen": "current"}]) == [
        {"name": "place_window", "args": {"name": "Notes", "where": "left", "screen": "current"}}]
    assert learned.calls_from_actions([{"kind": "media", "action": "pause"}]) == []


def test_replay_uses_the_tools_and_counts_uses(monkeypatch):
    import tools

    done = []

    async def fake(context, **args):
        done.append(args)
        return succeeded(f"done {args['name']}.", args["name"])

    monkeypatch.setitem(tools.ALL_TOOLS, "quit_app", fake)
    monkeypatch.setitem(tools.ALL_TOOLS, "place_window", fake)
    learned.learn("focus mode", CALLS, named=True)
    reply = asyncio.run(learned.replay(learned.find("focus mode")))
    assert reply.done and reply == "done Slack. done Notes." and len(done) == 2
    assert learned.find("focus mode")["uses"] == 1


def test_llm_node_learns_a_named_shortcut_then_replays_it_without_the_model(monkeypatch):
    from livekit.agents.llm import ChatContext

    import agent
    import fast_path
    import tools
    from tests.test_fast_path import resolve

    def no_llm(cfg):
        raise AssertionError("le LLM ne doit pas être appelé")

    async def fake_run(actions):
        return succeeded("Quit Slack. Notes is on the left.")

    async def fake_tool(context, **args):
        return succeeded(f"ok {args['name']}.", args["name"])

    async def no_publish(*a, **k):
        return None

    monkeypatch.setattr(agent, "llm_for", no_llm)
    monkeypatch.setattr(fast_path, "resolve_strict", resolve)
    monkeypatch.setattr(fast_path, "run", fake_run)
    monkeypatch.setattr(agent, "publish_action", no_publish)
    monkeypatch.setitem(tools.ALL_TOOLS, "quit_app", fake_tool)
    monkeypatch.setitem(tools.ALL_TOOLS, "place_window", fake_tool)
    a = agent.Assistant.__new__(agent.Assistant)
    a.command_mode, a.silent_prompt, a.typed, a._turn = False, agent.COMMAND_PROMPT, False, {}
    instructions = agent.compose_instructions(agent.load_settings())
    monkeypatch.setattr(agent.Assistant, "instructions", property(lambda self: instructions), raising=False)

    def say(text):
        ctx = ChatContext()
        ctx.add_message(role="system", content=instructions)
        ctx.add_message(role="user", content=text)

        async def collect():
            return "".join([c async for c in a.llm_node(ctx, [], None)])

        return asyncio.run(collect())

    first = say("When I say focus mode, close Slack and open Notes on the left.")
    assert "Learned" in first
    assert say("Focus mode.") == "ok Slack. ok Notes."
