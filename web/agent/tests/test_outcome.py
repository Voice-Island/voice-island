"""P0-5: every tool returns a typed result (Outcome), never a text whose meaning is guessed from its start.

Fake Mac bridges: no calls to Voice Island, macOS or the network.
"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import outcome
import tools
from outcome import Outcome, Status, attempted, combine, failed, succeeded, verified

CHROME = Path("/Applications/Google Chrome.app")
NOTES = Path("/System/Applications/Notes.app")


def call(tool, context=None, **kwargs) -> Outcome:
    result = asyncio.run(tool(context, **kwargs))
    assert isinstance(result, Outcome), f"{result!r} n'a pas de statut"
    return result


@pytest.fixture
def mac(monkeypatch):
    """A fake Mac: `open` / `osascript` commands succeed, Voice Island disconnected, no waiting."""
    state = SimpleNamespace(code=0, ran=[], windows=[], bridge={}, asked=[])

    async def fake_run(*args):
        state.ran.append(args)
        return state.code, "" if state.code == 0 else "boom"

    async def fake_windows():
        return state.windows.pop(0) if state.windows else None

    def bridge(name):
        async def answer(*args, **kwargs):
            state.asked.append(name)
            return state.bridge.get(name)
        return answer

    async def no_sleep(_):
        return None

    monkeypatch.setattr(tools, "run", fake_run)
    monkeypatch.setattr(tools, "windows", fake_windows)
    for name in ("keys", "place", "mac_text", "screen", "mac_calendar", "chrome", "active_tab"):
        monkeypatch.setattr(tools, name, bridge(name))
    monkeypatch.setattr(tools.asyncio, "sleep", no_sleep)
    apps = {"chrome": CHROME, "notes": NOTES}
    monkeypatch.setattr(tools, "resolve_app", lambda name: apps.get(name.lower()))
    monkeypatch.setattr(tools, "resolve_running", lambda name: None)
    monkeypatch.setattr(tools, "chrome_profiles", lambda: [])   # never the machine's real Chrome profiles
    return state


# MARK: - Outcome


def test_outcome_is_the_text_the_model_reads_plus_a_status():
    o = failed("Could not quit Slack: boom", "Slack")
    assert o == "Could not quit Slack: boom" and isinstance(o, str)
    assert o.status is Status.FAILED and o.resource == "Slack" and o.failed and not o.ok and not o.done
    assert succeeded("x").done and verified("x").done and attempted("x").ok and not attempted("x").done
    kept = o.with_message("Slack refused to quit.")
    assert kept.status is Status.FAILED and kept.resource == "Slack"
    assert not isinstance(f"{o}!", Outcome)   # an f-string loses the status: hence with_message


def test_several_actions_take_the_status_of_the_least_certain():
    both = combine([verified("Chrome has 2 windows.", "Chrome"), succeeded("Notes is on the left.", "Notes")])
    assert both == "Chrome has 2 windows. Notes is on the left." and both.status is Status.SUCCEEDED
    assert both.resource == "Chrome, Notes"
    assert combine([succeeded("a"), attempted("b")]).status is Status.ATTEMPTED
    assert combine([succeeded("a"), failed("b"), attempted("c")]).status is Status.FAILED
    assert combine([]).done


def test_tracked_keeps_the_result_of_each_call_once():
    ctx = SimpleNamespace(function_call=SimpleNamespace(call_id="call-1"))
    result = call(tools.get_current_time, ctx)
    assert result.status is Status.SUCCEEDED
    assert outcome.take("call-1") is result
    assert outcome.take("call-1") is None


def test_tools_without_a_status_count_as_attempted_and_errors_as_failed():
    # MCP servers: no decorator, only is_error is known.
    assert outcome.for_call("mcp-1", "3 results", is_error=False).status is Status.ATTEMPTED
    assert outcome.for_call("mcp-2", "server down", is_error=True).status is Status.FAILED

    @outcome.tracked
    async def legacy(context):
        return "Did something."

    ctx = SimpleNamespace(function_call=SimpleNamespace(call_id="call-2"))
    assert asyncio.run(legacy(ctx)).status is Status.ATTEMPTED
    assert outcome.for_call("call-2", "Did something.", is_error=False).status is Status.ATTEMPTED


def test_every_tool_is_tracked_and_never_returns_bare_text():
    """Static safeguard: a new tool that returns a text without a status makes this test fail."""
    source = Path(tools.__file__).read_text()
    helpers = {"_new_window", "place_app", "_open_url", "_search", "_open_here_or_new"}
    seen = set()
    for fn in ast.parse(source).body:
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        decorators = [ast.unparse(d) for d in fn.decorator_list]
        if "function_tool" in decorators:
            assert decorators[:2] == ["function_tool", "tracked"], fn.name
            assert all(d.startswith("confirm_first(") for d in decorators[2:]), fn.name
            seen.add(fn.name)
        if "function_tool" not in decorators and fn.name not in helpers:
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and isinstance(node.value, (ast.JoinedStr, ast.Constant, ast.BinOp)):
                pytest.fail(f"{fn.name}, ligne {node.lineno} : texte sans statut")
    assert seen == set(tools.ALL_TOOLS)
    assert "startswith((\"Could not" not in source and ".startswith(\"Opened\")" not in source


def test_the_island_receives_the_status():
    import agent

    assert agent.action_payload(verified("Chrome has 2 windows.")) == {
        "text": "Chrome has 2 windows.", "ok": True, "status": "verified"}
    assert agent.action_payload(attempted("Sent to Claude Code."))["status"] == "attempted"
    assert agent.action_payload(failed("Could not."))["ok"] is False
    # Safeguard alerts, as plain text.
    assert agent.action_payload("Elle allait dire…", ok=False) == {
        "text": "Elle allait dire…", "ok": False, "status": "failed"}
    assert agent.action_payload("Learned.")["status"] == "succeeded"


# MARK: - Applications and windows


def test_open_app(mac):
    assert call(tools.open_app, name="Photoshop").failed
    shown = call(tools.open_app, name="Chrome")
    assert shown.status is Status.SUCCEEDED and shown.resource == "Google Chrome"
    mac.code = 1
    assert call(tools.open_app, name="Chrome").failed
    assert call(tools.open_app, name="Chrome", folder="nowhere-at-all").failed


def test_a_new_window_is_verified_by_counting_windows(mac):
    mac.windows = [{"Google Chrome": 1}, {"Google Chrome": 2}]
    seen = call(tools.open_app, name="Chrome", new_window=True)
    assert seen.status is Status.VERIFIED and "now has 2 windows" in seen

    mac.windows = [{"Google Chrome": 1}, {"Google Chrome": 1}]
    missing = call(tools.open_app, name="Chrome", new_window=True)
    assert missing.failed and missing.startswith("Asked Google Chrome for a new window")


def test_bringing_an_app_forward_without_its_new_window_is_a_failure(mac):
    # Old bug: "Brought Notes to the front but could not…" showed in green.
    mac.windows = [{"Notes": 1}]
    result = call(tools.open_app, name="Notes", new_window=True)
    assert result.failed and result.startswith("Brought Notes to the front")


def test_open_terminal(mac):
    mac.windows = [{"Terminal": 1}, {"Terminal": 2}]
    assert call(tools.open_terminal).status is Status.VERIFIED
    mac.windows = [{"Terminal": 1}, {"Terminal": 1}]
    assert call(tools.open_terminal).failed
    assert call(tools.open_terminal).status is Status.SUCCEEDED   # no window count: not verified
    mac.code = 1
    assert call(tools.open_terminal).failed


def test_quit_app(mac):
    assert call(tools.quit_app, name="Chrome").status is Status.SUCCEEDED
    mac.code = 1
    assert call(tools.quit_app, name="Chrome").failed
    assert call(tools.quit_app, name="Photoshop").failed


def test_place_window(mac):
    assert call(tools.place_window, name="Chrome", where="left").failed   # Voice Island disconnected
    mac.bridge["place"] = {"ok": False, "error": "no window"}
    assert call(tools.place_window, name="Chrome", where="left").failed
    mac.bridge["place"] = {"ok": True}
    placed = call(tools.place_window, name="Chrome", where="left")
    assert placed == "Google Chrome is on the left." and placed.status is Status.SUCCEEDED
    assert call(tools.place_window, name="Chrome", where="upside down").failed


# MARK: - Reading and writing via Voice Island


def test_read_selection_and_screen(mac):
    assert call(tools.read_selection).failed
    assert call(tools.read_screen).failed
    mac.bridge["mac_text"] = {"ok": True, "text": "", "app": "Mail"}
    assert call(tools.read_selection).status is Status.SUCCEEDED   # nothing selected: the read worked
    mac.bridge["screen"] = {"ok": True, "text": "Hello", "app": "Mail"}
    assert call(tools.read_screen).resource == "Mail"


def test_calendar_and_reminders(mac):
    assert call(tools.calendar_events, action="add", title="", when="").failed
    assert call(tools.calendar_events, action="dance").failed
    assert call(tools.calendar_events, action="list").failed   # disconnected
    mac.bridge["mac_calendar"] = {"ok": True, "events": []}
    assert call(tools.calendar_events, action="list").status is Status.SUCCEEDED
    added = call(tools.calendar_events, action="add", title="Lunch", when="2026-11-02 12:30")
    assert added.status is Status.SUCCEEDED and added.resource == "Lunch"
    mac.bridge["mac_calendar"] = {"ok": False, "error": "declined"}
    assert call(tools.calendar_events, action="delete", title="Lunch").failed
    assert call(tools.add_reminder, title="Call Paul").failed
    mac.bridge["mac_calendar"] = {"ok": True}
    assert call(tools.add_reminder, title="Call Paul").resource == "Call Paul"


def test_insert_text(mac):
    assert call(tools.insert_text, text="Hi").failed
    mac.bridge["mac_text"] = {"ok": True, "app": "Mail"}
    assert call(tools.insert_text, text="Hi").status is Status.SUCCEEDED


# MARK: - Browser, YouTube, Gmail


def test_websites_and_search(mac):
    opened = call(tools.open_website, site="github.com")
    assert opened.status is Status.SUCCEEDED and opened.resource == "https://github.com"
    assert call(tools.open_website, site="github.com", account="nobody-has-this").failed
    assert call(tools.search_web, query="ramen").status is Status.SUCCEEDED
    mac.code = 1
    assert call(tools.search_web, query="ramen").failed


def test_youtube_in_the_current_tab_or_a_new_one(mac, monkeypatch):
    mac.bridge["active_tab"] = {"url": "https://www.youtube.com/", "front": True}
    mac.bridge["chrome"] = {"ok": True}
    here = call(tools.search_youtube, query="lofi")
    assert here == "Showing YouTube results for 'lofi' in the current tab." and here.done
    monkeypatch.setattr(tools, "resolve_chrome_profile", lambda account: None)
    assert call(tools.search_youtube, query="lofi", account="work").failed   # no "work" profile
    monkeypatch.setattr(tools, "youtube_results", lambda q: [])
    assert call(tools.play_youtube_video, query="nothing at all").failed
    monkeypatch.setattr(tools, "youtube_results", lambda q: [("abcdefghijk", "Lofi beats")])
    assert call(tools.play_youtube_video, query="lofi").startswith("Playing 'Lofi beats' in the current tab")


def test_draft_email_is_a_draft_or_a_failure(mac, monkeypatch):
    draft = call(tools.draft_email, subject="Hi", body="Hello.")
    assert draft.status is Status.SUCCEEDED and draft.resource == "gmail draft" and "NOT SENT" in draft
    mac.code = 1
    assert call(tools.draft_email, subject="Hi", body="Hello.").failed


def test_media_keys_and_tabs(mac):
    assert call(tools.media_control, action="dance").failed
    assert call(tools.media_control, action="pause").failed   # disconnected
    mac.bridge["keys"] = {"ok": True}
    assert call(tools.media_control, action="pause") == "Toggled play / pause."
    assert call(tools.close_tab).failed
    mac.bridge["chrome"] = {"ok": True, "title": "News"}
    assert call(tools.close_tab).status is Status.SUCCEEDED


# MARK: - Everything else


def test_simple_tools(mac, monkeypatch):
    assert call(tools.set_volume, percent=30).status is Status.SUCCEEDED
    assert call(tools.open_folder, name="attic").failed
    assert call(tools.open_folder, name="downloads").status is Status.SUCCEEDED
    monkeypatch.setattr(tools, "screen_snapshot", lambda: ("", []))
    assert call(tools.list_open_apps).failed
    monkeypatch.setattr(tools, "screen_snapshot", lambda: ("Notes", ["Notes"]))
    assert call(tools.list_open_apps).status is Status.SUCCEEDED

    def down(query):
        raise OSError("connection refused")

    monkeypatch.setattr(tools, "work_memory", down)
    assert call(tools.search_work_memory, query="proposal").failed


def test_claude_code_is_only_sent_not_done(mac, monkeypatch):
    async def no_run(*args, **kwargs):
        return None

    monkeypatch.setattr(tools, "_run_claude_code", no_run)
    monkeypatch.setattr(tools, "claude_tasks", [])
    sent = call(tools.ask_claude_code, SimpleNamespace(session=None), request="Fix the tests.")
    assert sent.status is Status.ATTEMPTED and sent.ok and not sent.done
    monkeypatch.setattr(tools, "claude_sessions", lambda: [])
    assert call(tools.open_claude_session).failed


# MARK: - Instant commands and learned shortcuts


def test_instant_commands_report_the_least_certain_action(mac, monkeypatch):
    import fast_path

    mac.bridge["place"] = {"ok": True}
    both = asyncio.run(fast_path.run([{"kind": "place", "app": CHROME, "place": "left", "screen": "current"},
                                      {"kind": "open", "app": NOTES}]))
    assert both == "Google Chrome is on the left. Opened Notes." and both.done

    mac.bridge["place"] = None
    half = asyncio.run(fast_path.run([{"kind": "open", "app": NOTES},
                                      {"kind": "place", "app": CHROME, "place": "left", "screen": "current"}]))
    assert half.failed


def test_a_new_window_that_failed_is_not_placed(mac):
    import fast_path

    mac.bridge["place"] = {"ok": True}
    result = asyncio.run(fast_path.run([{"kind": "new_window", "app": NOTES, "place": "left", "screen": "current"}]))
    assert result.failed and result.startswith("Brought Notes")
    assert "place" not in mac.asked and "keys" in mac.asked


def test_a_failed_replay_is_not_done(mac, monkeypatch):
    import learned

    entry = {"phrase": "focus", "calls": [{"name": "quit_app", "args": {"name": "Photoshop"}}]}
    monkeypatch.setattr(learned, "load", lambda: {})
    assert asyncio.run(learned.replay(entry)).failed
    gone = {"phrase": "focus", "calls": [{"name": "no_such_tool", "args": {}}]}
    assert asyncio.run(learned.replay(gone)).failed
