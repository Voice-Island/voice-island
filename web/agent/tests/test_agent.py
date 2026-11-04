"""Safeguard against "action announced without a tool" and background-noise filter, with a fake LLM."""

import asyncio
import contextlib

import pytest
from livekit.agents import llm
from livekit.agents.llm import ChatContext

import agent


@pytest.mark.parametrize("text, last, ignored", [
    ("Thank you.", "", True),
    ("Yeah, yeah.", "Playing the first video.", True),
    ("Yes.", "Would you like me to play it?", False),  # answers a question from the assistant
    ("Open Chrome.", "", False),
    ("", "", False),
    ("thank you thank you thank you thank you", "", True),
])
def test_background_filler(text, last, ignored):
    assert agent.is_background_filler(text, last) is ignored


class FakeLLM:
    """Replies, in order, with text (str) or a tool call ({"tool": name})."""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    @contextlib.asynccontextmanager
    async def _stream(self, reply):
        async def gen():
            if isinstance(reply, str):
                for word in reply.split(" "):
                    yield llm.ChatChunk(id="x", delta=llm.ChoiceDelta(role="assistant", content=word + " "))
            else:
                yield llm.ChatChunk(id="x", delta=llm.ChoiceDelta(role="assistant", tool_calls=[
                    llm.FunctionToolCall(name=reply["tool"], arguments="{}", call_id="c1")]))
        yield gen()

    def chat(self, **_):
        self.calls += 1
        return self._stream(self.replies.pop(0))


def run(monkeypatch, user, replies, screen=""):
    fake = FakeLLM(replies)
    monkeypatch.setattr(agent, "llm_for", lambda cfg: fake)
    monkeypatch.setattr(agent, "screen_context_line", lambda: asyncio.sleep(0, screen))
    # These tests are about the LLM and its safeguard: no instant command (it would really open the app).
    monkeypatch.setattr(agent.fast_path, "parse", lambda *a, **k: None)

    async def no_publish(*a, **k):
        return None

    monkeypatch.setattr(agent, "publish_action", no_publish)
    a = agent.Assistant.__new__(agent.Assistant)
    a.command_mode, a.silent_prompt, a.typed, a._turn = False, agent.COMMAND_PROMPT, False, {}
    instructions = agent.compose_instructions(agent.load_settings())
    monkeypatch.setattr(agent.Assistant, "instructions", property(lambda self: instructions), raising=False)
    ctx = ChatContext()
    ctx.add_message(role="system", content=instructions)
    ctx.add_message(role="user", content=user)

    async def collect():
        text, tool = "", False
        async for chunk in a.llm_node(ctx, [], None):
            if isinstance(chunk, str):
                text += chunk
            elif chunk.delta:
                text += chunk.delta.content or ""
                tool |= bool(chunk.delta.tool_calls)
        return text.strip(), tool

    text, tool = asyncio.run(collect())
    return text, fake.calls, tool


SCREENS = ["", "Right now on the user's screen: front app Google Chrome."]


@pytest.mark.parametrize("screen", SCREENS)
def test_claimed_action_without_tool_gets_a_second_try(monkeypatch, screen):
    text, calls, tool = run(monkeypatch, "Open a new VS Code window.", ["Opening VS Code now.", {"tool": "open_app"}],
                            screen)
    assert calls == 2 and tool and text == ""


@pytest.mark.parametrize("screen", SCREENS)
def test_twice_claimed_action_becomes_an_honest_answer(monkeypatch, screen):
    text, calls, tool = run(monkeypatch, "Close the open website.", ["Closing the open website.", "Closing it now."],
                            screen)
    assert calls == 2 and not tool and text == agent.HONEST_FALLBACK


@pytest.mark.parametrize("user, replies", [
    ("Open Chrome.", [{"tool": "open_app"}]),
    ("What's the capital of France?", ["Paris is the capital."]),
    ("Search YouTube for cats.", ["Which account should I use?"]),
])
def test_guard_leaves_honest_answers_alone(monkeypatch, user, replies):
    _, calls, _ = run(monkeypatch, user, replies, SCREENS[1])
    assert calls == 1


def test_stdio_mcp_server_keeps_only_allowed_tools(monkeypatch):
    class Info:
        def __init__(self, name):
            self.name = name

    class Tool:
        def __init__(self, name):
            self.info = Info(name)

    async def all_tools(self, *, tool_options=None):
        return [Tool("search"), Tool("get_observations"), Tool("build_corpus"), Tool("smart_search")]

    monkeypatch.setattr(agent.mcp.MCPServerStdio, "list_tools", all_tools)
    server = agent.FilteredStdioServer(command="x", args=[], allowed_tools=["search", "get_observations"])
    names = [t.info.name for t in asyncio.run(server.list_tools())]
    assert names == ["search", "get_observations"]
    unfiltered = agent.FilteredStdioServer(command="x", args=[])
    assert len(asyncio.run(unfiltered.list_tools())) == 4


def test_claimed_action_without_tool_is_caught_on_plain_questions(monkeypatch):
    # "What did we decide…" contains no action verb, but the reply claims a search.
    text, calls, tool = run(monkeypatch, "What did we decide about the Acme proposal?",
                            ["Searching your work memory for the Acme proposal...", {"tool": "search"}])
    assert calls == 2 and tool


def test_plain_answers_to_questions_are_streamed_untouched(monkeypatch):
    text, calls, _ = run(monkeypatch, "Tell me a fun fact.", ["Octopuses have three hearts. Isn't that wild?"])
    assert calls == 1 and text == "Octopuses have three hearts. Isn't that wild?"


def test_fun_fact_with_action_words_is_not_a_fake_action(monkeypatch):
    reply = ('Did you know that a group of octopuses can communicate using light patterns, creating intricate '
             '"light shows" to signal danger? Pretty clever.')
    text, calls, _ = run(monkeypatch, "Tell me a fun fact.", [reply])
    assert calls == 1 and text == reply
