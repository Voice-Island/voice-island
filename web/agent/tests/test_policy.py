"""P0-6: a risky action waits for a yes from the user; nothing else (LLM, tool result) starts it."""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import outcome
import policy
import settings
import tools
from outcome import Status, succeeded


@pytest.fixture(autouse=True)
def no_pending():
    policy.cancel()
    yield
    policy.cancel()


def edits_allowed(path):
    path.write_text(json.dumps({"tools": {"claude_code": {"permission_mode": "acceptEdits"}}}))


@pytest.fixture
def claude(monkeypatch):
    runs = []

    async def fake_run(session, request, cfg, resume=None, entry=None):
        runs.append(request)

    monkeypatch.setattr(tools, "_run_claude_code", fake_run)
    monkeypatch.setattr(tools, "claude_tasks", [])
    return runs


def ask(request="Fix the tests.", call_id="c1"):
    ctx = SimpleNamespace(session=None, function_call=SimpleNamespace(call_id=call_id))

    async def go():
        result = await tools.ask_claude_code(ctx, request=request)
        await asyncio.sleep(0)   # lets the background task start
        return result

    return asyncio.run(go())


# MARK: - Yes / no


@pytest.mark.parametrize("text", ["Yes.", "yes please", "Oui", "Oui, vas-y !", "go ahead", "Vas-y.", "D'accord",
                                  "Je confirme.", "Send it."])
def test_yes(text):
    assert policy.answer(text) == "yes"


@pytest.mark.parametrize("text", ["No.", "Non", "Cancel", "Annule.", "Laisse tomber", "Don't send it."])
def test_no(text):
    assert policy.answer(text) == "no"


@pytest.mark.parametrize("text", ["Yes but in plan mode", "Open Chrome", "oui et ouvre Notes", "", "yes no"])
def test_anything_else_is_not_an_answer(text):
    assert policy.answer(text) is None


# MARK: - Local tools


def test_every_risky_tool_asks_first():
    """Every EXTERNAL / DESTRUCTIVE tool carries @confirm_first; every tool has a risk level."""
    assert set(tools.TOOL_RISK) == set(tools.ALL_TOOLS)
    source = Path(tools.__file__).read_text()
    guarded = {fn.name for fn in ast.parse(source).body if isinstance(fn, ast.AsyncFunctionDef)
               and any(ast.unparse(d).startswith("confirm_first(") for d in fn.decorator_list)}
    risky = {name for name, risk in tools.TOOL_RISK.items() if risk in policy.CONFIRM}
    assert risky == guarded


def test_read_only_claude_code_runs_without_asking(claude):
    assert ask().status is Status.ATTEMPTED and claude == ["Fix the tests."]
    assert policy.pending() is None


def test_claude_code_allowed_to_edit_waits_for_a_yes(claude, isolated_settings):
    edits_allowed(isolated_settings)
    proposed = ask()
    assert proposed.status is Status.PROPOSED and "NOT DONE" in proposed and claude == []
    assert outcome.take("c1") is proposed   # the agent sees "proposed", not a success
    assert asyncio.run(policy.approve()).status is Status.ATTEMPTED
    assert claude == ["Fix the tests."]
    assert asyncio.run(policy.approve()) is None   # only once


def test_the_model_calling_again_only_proposes_again(claude, isolated_settings):
    edits_allowed(isolated_settings)
    first = ask(call_id="c1")
    second = ask(call_id="c2")
    assert claude == [] and first.resource != second.resource
    assert asyncio.run(policy.approve(first.resource)) is None   # an old proposal's button starts nothing
    assert asyncio.run(policy.approve(second.resource)) is not None and claude == ["Fix the tests."]


def test_a_late_yes_does_nothing(claude, isolated_settings, monkeypatch):
    edits_allowed(isolated_settings)
    ask()
    monkeypatch.setattr(policy, "EXPIRES", -1.0)
    assert asyncio.run(policy.approve()) is None and claude == []


def test_a_failing_action_is_reported_not_raised():
    async def boom():
        raise RuntimeError("server down")

    policy.propose("mail.send", "send the e-mail", boom)
    result = asyncio.run(policy.approve())
    assert result.failed and "server down" in result


# MARK: - MCP servers


class FakeServer:
    """Replaces the real MCP client under ConfirmingMCP."""

    def __init__(self):
        self.calls = []

    async def _call_tool(self, name, arguments, **kwargs):
        import mcp.types as t

        self.calls.append((name, arguments))
        return t.CallToolResult(content=[t.TextContent(type="text", text=f"{name} ok")], is_error=False)


def mcp_server(cfg):
    import agent

    class Server(agent.ConfirmingMCP, FakeServer):
        pass

    server = Server()
    server._confirm_setup(cfg)
    return server


def test_mcp_tools_wait_for_a_yes_unless_declared_safe():
    server = mcp_server({"name": "mail", "safe_tools": ["search"]})
    found = asyncio.run(server._call_tool("search", {"q": "invoice"}))
    assert found.content[0].text == "search ok"
    held = asyncio.run(server._call_tool("send_email", {"to": "paul@example.com"}))
    assert "NOT DONE" in held.content[0].text and server.calls == [("search", {"q": "invoice"})]
    assert policy.pending().tool == "mail.send_email"
    done = asyncio.run(policy.approve())
    assert done == "send_email ok" and done.done and server.calls[-1] == ("send_email", {"to": "paul@example.com"})


def test_a_server_can_be_trusted_entirely():
    server = mcp_server({"name": "notes", "confirm": False})
    assert asyncio.run(server._call_tool("delete", {})).content[0].text == "delete ok"


# MARK: - In the conversation


def assistant(monkeypatch):
    import agent

    def no_llm(cfg):
        raise AssertionError("le LLM ne doit pas être appelé")

    published = []

    async def publish(text, ok=None):
        published.append(agent.action_payload(text, ok))

    monkeypatch.setattr(agent, "llm_for", no_llm)
    monkeypatch.setattr(agent, "publish_action", publish)
    a = agent.Assistant.__new__(agent.Assistant)
    a.command_mode, a.silent_prompt, a.typed, a._turn = False, agent.COMMAND_PROMPT, False, {}
    instructions = agent.compose_instructions(agent.load_settings())
    monkeypatch.setattr(agent.Assistant, "instructions", property(lambda self: instructions), raising=False)

    def say(text):
        from livekit.agents.llm import ChatContext

        ctx = ChatContext()
        ctx.add_message(role="system", content=instructions)
        ctx.add_message(role="user", content=text)

        async def collect():
            return "".join([c async for c in a.llm_node(ctx, [], None)])

        return asyncio.run(collect())

    return say, published


def test_the_users_yes_runs_it_without_the_model(monkeypatch):
    done = []

    async def send():
        done.append(1)
        return succeeded("Message sent to Paul.")

    policy.propose("mail.send", "send the e-mail to Paul", send)
    say, published = assistant(monkeypatch)
    assert say("Oui, vas-y.") == "Done. Message sent to Paul." and done == [1]
    assert published[-1]["status"] == "succeeded"


def test_the_users_no_cancels(monkeypatch):
    async def send():
        raise AssertionError("ne doit pas partir")

    policy.propose("mail.send", "send the e-mail to Paul", send)
    say, published = assistant(monkeypatch)
    assert say("No.") == "OK, cancelled." and policy.pending() is None
    assert published[-1] == {"text": "Cancelled: nothing was done.", "ok": False, "status": "failed"}


def test_another_request_drops_the_proposal(monkeypatch):
    import agent

    async def send():
        raise AssertionError("ne doit pas partir")

    policy.propose("mail.send", "send the e-mail to Paul", send)
    say, published = assistant(monkeypatch)
    with pytest.raises(AssertionError, match="LLM"):   # the new request goes to the LLM, the proposal is dropped
        say("What's the weather like?")
    assert policy.pending() is None and published[-1]["text"] == "Not confirmed: nothing was done."
    assert agent.action_payload(outcome.Outcome("Confirm? x", Status.PROPOSED, "p9"))["id"] == "p9"


def test_yes_is_not_background_noise_while_something_waits():
    import agent

    from livekit.agents import StopResponse
    from livekit.agents.llm import ChatContext, ChatMessage

    a = agent.Assistant.__new__(agent.Assistant)
    a.typed = False
    ctx = ChatContext()
    ctx.add_message(role="assistant", content="Done.")
    with pytest.raises(StopResponse):
        asyncio.run(a.on_user_turn_completed(ctx, ChatMessage(role="user", content=["Yes."])))

    async def send():
        return succeeded("sent")

    policy.propose("mail.send", "send it", send)
    asyncio.run(a.on_user_turn_completed(ctx, ChatMessage(role="user", content=["Yes."])))
