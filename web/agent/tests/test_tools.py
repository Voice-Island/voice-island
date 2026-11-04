import json
import asyncio
import plistlib
import time

import pytest

import settings
import tools
from outcome import Status, succeeded


def call(tool, **kwargs):
    """Calls a tool as the agent would (context not needed outside a conversation)."""
    return asyncio.run(tool(None, **kwargs))


def test_tools_hint_names_no_tool():
    # With tool names in the instruction, Qwen3 4B wrote "quit_app({...})" as text instead of calling
    # the tool (measured on Oct 24, 2026).
    for name in tools.ALL_TOOLS:
        assert name not in tools.TOOLS_HINT


def test_every_default_tool_exists():
    assert set(settings.DEFAULTS["tools"]["enabled"]) <= set(tools.ALL_TOOLS)


@pytest.mark.parametrize("model, expected", [
    ("mlx-community/Qwen3-4B-Instruct-2507-4bit", True),
    ("local:Qwen2.5-1.5B-Instruct-4bit-tools", True),
    ("mlx-community/gemma-4-e4b-it-4bit", True),
    ("mlx-community/gemma-3-4b-it-qat-4bit", False),
    ("mlx-community/Llama-3.2-3B-Instruct-4bit", False),
])
def test_model_supports_tools(model, expected):
    assert tools.model_supports_tools(model) is expected


def test_project_folder_defaults_to_this_repo():
    assert tools.resolve_folder("project") == settings.REPO_ROOT
    assert tools.resolve_folder("downloads") == tools.FOLDERS["downloads"]
    assert tools.resolve_folder("no such folder anywhere") is None


@pytest.mark.parametrize("url, expected", [
    ("https://www.youtube.com/results?search_query=x", True),
    ("https://youtube.com/watch?v=abc", True),
    ("https://notyoutube.com/", False),
    ("https://www.google.com/search?q=youtube", False),
])
def test_is_youtube(url, expected):
    assert tools._is_youtube(url) is expected


def test_youtube_results_are_parsed_in_order(monkeypatch):
    html = ('{"videoRenderer":{"videoId":"AAAAAAAAAAA","thumbnail":{},"title":{"runs":[{"text":"First \\u0026 best"}]}}'
            '{"videoRenderer":{"videoId":"BBBBBBBBBBB","title":{"runs":[{"text":"Second"}]}}')

    class Response:
        def read(self):
            return html.encode()

    monkeypatch.setattr(tools.urllib.request, "urlopen", lambda *a, **k: Response())
    assert tools.youtube_results("anything") == [("AAAAAAAAAAA", "First & best"), ("BBBBBBBBBBB", "Second")]


def test_window_count_uses_the_process_name(tmp_path):
    app = tmp_path / "Visual Studio Code.app"
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents" / "Info.plist").open("wb") as f:
        plistlib.dump({"CFBundleIdentifier": "com.microsoft.VSCode", "CFBundleName": "Code",
                       "CFBundleExecutable": "Electron"}, f)
    assert tools._window_count({"Code": 3, "Terminal": 1}, app) == 3
    assert tools._window_count(None, app) is None
    assert tools._bundle_id(app) == "com.microsoft.VSCode"


def test_media_control_rejects_unknown_actions():
    assert call(tools.media_control, action="dance").startswith("Unknown action")


def test_actions_needing_the_mac_app_fail_honestly_without_it():
    # Outside a conversation (no Voice Island): never "it's done".
    assert call(tools.media_control, action="pause").startswith("Could not do it")
    assert call(tools.close_tab).startswith("Could not close the tab")


def test_draft_email_never_sends_and_never_invents_a_recipient(monkeypatch):
    opened = []

    async def fake_open(url, account=""):
        opened.append((url, account))
        return succeeded(f"Opened {url}.", url)

    monkeypatch.setattr(tools, "_open_url", fake_open)
    result = call(tools.draft_email, to="", subject="Hello", body="Hi there,\nSee you.", account="personal")
    url, account = opened[0]
    assert url.startswith("https://mail.google.com/mail/?view=cm")
    assert "to=" not in url and "su=Hello" in url
    assert account == "personal"
    assert "NOT SENT" in result and "no recipient yet" in result


def test_claude_status_line(monkeypatch):
    monkeypatch.setattr(tools, "claude_tasks", [])
    assert tools.claude_status_line() == ""
    tools.claude_tasks.append({"request": "find my repo", "started": time.time() - 130, "result": None})
    assert "still working on 'find my repo' (for 2 min)" in tools.claude_status_line()
    tools.claude_tasks[0]["result"] = "Your latest repo is code-to-video."
    assert "finished 'find my repo', its report: Your latest repo is code-to-video." in tools.claude_status_line()


def test_mcp_hints_are_added_only_for_enabled_servers():
    s = settings.load_settings()
    s["tools"]["mcp_servers"] = [{"name": "a", "command": "x", "hint": "Use memory A."},
                                 {"name": "b", "command": "y", "hint": "Use memory B.", "enabled": False}]
    text = tools.compose_instructions(s)
    assert "Use memory A." in text and "Use memory B." not in text


def test_work_memory_returns_details_of_the_best_results(monkeypatch):
    index = ("| ID | Time | T | Title | Read |\n|----|------|---|-------|------|\n"
             "| #3645 | 4:39 PM | x | Acme Proposal v10 | ~475 |\n| #3647 | 4:40 PM | x | Deck live | ~390 |\n")
    details = [{"id": 3645, "title": "Acme Proposal v10", "subtitle": "Retainer removed.",
                "facts": '["Late-payment clause flagged as too punitive."]', "created_at": "2026-08-17T16:39:00"}]
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

    def fake_urlopen(req, timeout=0):
        url = req if isinstance(req, str) else req.full_url
        calls.append(url)
        if "/api/search" in url:
            return Response({"content": [{"type": "text", "text": index}]})
        assert json.loads(req.data) == {"ids": [3645, 3647]}
        return Response(details)

    monkeypatch.setattr(tools.urllib.request, "urlopen", fake_urlopen)
    out = tools.work_memory("Acme proposal")
    assert "[2026-08-17] Acme Proposal v10: Retainer removed. Late-payment clause flagged" in out
    assert len(calls) == 2


def test_memory_hint_only_when_the_memory_tool_is_enabled():
    s = settings.load_settings()
    assert tools.MEMORY_HINT not in tools.compose_instructions(s)
    s["tools"]["enabled"] = [*s["tools"]["enabled"], "search_work_memory"]
    assert tools.MEMORY_HINT in tools.compose_instructions(s)


def test_selection_and_insert_fail_honestly_without_the_mac_app():
    assert "not connected" in call(tools.read_selection)
    assert "not connected" in call(tools.insert_text, text="Thursday works.")
    assert "not connected" in call(tools.place_window, name="Finder", where="left")


def test_parse_when():
    from datetime import datetime, timedelta

    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert tools.parse_when("2026-10-26 16:00") == "2026-10-26 16:00"
    assert tools.parse_when("2026-10-26T16:00:00") == "2026-10-26 16:00"
    assert tools.parse_when("16:30") == f"{today} 16:30"
    assert tools.parse_when("tomorrow 12:30") == f"{tomorrow} 12:30"
    assert tools.parse_when("today", default_time="00:00") == f"{today} 00:00"
    assert tools.parse_when("2026-10-26") == "2026-10-26 09:00"
    assert tools.parse_when("next week sometime") is None


def test_calendar_fails_honestly_without_the_mac_app():
    assert "not connected" in call(tools.calendar_events, action="list")
    assert "Say what the event is" in call(tools.calendar_events, action="add", title="Lunch")
    assert "not connected" in call(tools.add_reminder, title="Call Paul")
