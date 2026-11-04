""""Intuition" cases: the model must take into account what is on screen (Chrome tab, open apps).

Same template as the agent: the screen line is a system message right before the question.
  cd web/agent && uv run python ../test/intuition_eval.py [model ...]
"""

import json
import sys
import time
import urllib.request

from tool_eval import URL, grade, tool_schemas

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "agent"))
from settings import load_settings  # noqa: E402
from tools import compose_instructions  # noqa: E402


def screen(front: str, tab: str = "") -> str:
    line = (f"Right now on the user's screen (use only if relevant, never read it out): front app {front}; "
            f"open apps: Google Chrome (2 windows, 9 tabs in the front one), WhatsApp (1 window), "
            f"Terminal (1 window), Code (3 windows), Finder, Mail")
    return line + (f"; Chrome tab: {tab}." if tab else ".")


YT_RESULTS = screen("Google Chrome", '"lofi hip hop - YouTube" https://www.youtube.com/results?search_query=lofi+hip+hop')
YT_HOME = screen("Google Chrome", '"YouTube" https://www.youtube.com/')
YT_WATCH = screen("Google Chrome", '"Billie Jean - YouTube" https://www.youtube.com/watch?v=Zi_XLOBDo_Y')
# History where the assistant has already confirmed several actions: small models start imitating
# "X has been quit." without calling the tool (logs from Oct 24).
QUITS = []
for app in ["Mail", "WhatsApp", "Finder", "Notes"]:
    QUITS += [{"role": "user", "content": f"Quit {app}."},
              {"role": "assistant", "content": None, "tool_calls": [{"id": f"c_{app}", "type": "function",
               "function": {"name": "quit_app", "arguments": json.dumps({"name": app})}}]},
              {"role": "tool", "tool_call_id": f"c_{app}", "content": f"Quit {app}."},
              {"role": "assistant", "content": f"{app} has been quit."}]
CODE = screen("Code")
MAIL = screen("Mail")

CASES = [
    ("Play the first video.", YT_RESULTS, "play_youtube_video",
     lambda a: not a.get("query") and int(a.get("position", 1)) == 1),
    ("Play the second one.", YT_RESULTS, "play_youtube_video", lambda a: int(a.get("position", 1)) == 2),
    # search_web too: on a YouTube tab in the foreground, it searches YouTube in the same tab.
    ("Search for Michael Jackson.", YT_HOME, "search_youtube|search_web",
     lambda a: "michael" in a.get("query", "").lower()),
    ("Play Billie Jean by Michael Jackson.", CODE, "play_youtube_video",
     lambda a: "billie" in a.get("query", "").lower()),
    ("Open WhatsApp.", CODE, "open_app", lambda a: "whatsapp" in a.get("name", "").lower()),
    ("Write an email to john@example.com telling him I'll be ten minutes late.", CODE, "draft_email",
     lambda a: "john@example.com" in a.get("to", "") and len(a.get("body", "")) > 20),
    ("Draft an email to my team on my work account saying the demo moves to Friday.", CODE, "draft_email",
     lambda a: "work" in a.get("account", "").lower()),
    ("Open the Claude session.", CODE, "open_claude_session", None),
    ("Tell Claude to go ahead with the plan.", CODE, "ask_claude_code", lambda a: a.get("follow_up") is True),
    ("Ask Claude Code what the tts_server file does.", CODE, "ask_claude_code",
     lambda a: not a.get("follow_up")),
    ("Open a new terminal.", CODE, "open_app|open_terminal",
     lambda a: a.get("new_window") is True or "start_claude_code" in a or "folder" in a or a == {}),
    ("Open a terminal and launch Claude Code.", CODE, "open_terminal", lambda a: a.get("start_claude_code") is True),
    ("Open another VS Code window.", CODE, "open_app", lambda a: a.get("new_window") is True),
    ("Pause the video.", YT_WATCH, "media_control", lambda a: "pause" in a.get("action", "")),
    ("Play it again, I paused it.", YT_WATCH, "media_control", None),
    ("Put the video in full screen.", YT_WATCH, "media_control", lambda a: "full" in a.get("action", "")),
    ("Close this page.", YT_WATCH, "close_tab", None),
    ("Open a new terminal window.", CODE, "open_app|open_terminal", None, QUITS),
    ("Now open Apple Music.", CODE, "open_app", lambda a: "music" in a.get("name", "").lower(), QUITS),
    # Work memory (if the tool is enabled in the user's settings)
    ("What did we decide about the Acme proposal?", CODE, "search_work_memory",
     lambda a: "acme" in a.get("query", "").lower()),
    ("Do you remember what I worked on for the Orion project last week?", CODE, "search_work_memory", None),
    # Windows, selection, screen, calendar (Oct 26)
    ("Put Chrome on the left and VS Code on the right.", CODE, "place_window",
     lambda a: a.get("where") in ("left", "right")),
    ("Move Terminal to my external monitor.", CODE, "place_window", lambda a: a.get("screen") == "monitor"),
    ("Summarize this.", MAIL, "read_selection|read_screen", None),
    ("Translate this into French.", MAIL, "read_selection|read_screen", None),
    ("What's wrong with this code?", CODE, "read_screen|read_selection", None),
    ("Reply to this email saying Thursday works for me.", MAIL, "read_selection|read_screen", None),
    ("What's on my calendar today?", CODE, "calendar_events", lambda a: a.get("action") == "list"),
    ("Add lunch with Paul tomorrow at 12:30.", CODE, "calendar_events",
     lambda a: a.get("action") == "add" and "12:30" in a.get("when", "")),
    ("Move my dentist appointment to 4 pm.", CODE, "calendar_events",
     lambda a: a.get("action") == "move" and "dentist" in a.get("title", "").lower()),
    ("Remind me to call the bank at 5.", CODE, "add_reminder", lambda a: "bank" in a.get("title", "").lower()),
]


def ask(model: str, prompt: str, line: str, history: list | None = None) -> tuple[dict, float]:
    settings = load_settings()
    system = compose_instructions(settings)
    body = {"model": model, "messages": [{"role": "system", "content": system}, *(history or []),
                                         {"role": "system", "content": line},
                                         {"role": "user", "content": prompt}],
            "tools": tool_schemas(), "max_tokens": 200, "temperature": 0.2}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    data = json.loads(urllib.request.urlopen(req, timeout=900).read())
    return data["choices"][0]["message"], time.perf_counter() - t


def main() -> None:
    models = sys.argv[1:] or [load_settings()["llm"]["model"]]
    for model in models:
        print(f"\n=== {model.rstrip('/').split('/')[-1]}", flush=True)
        ask(model, "Hi", CODE)
        ok = 0
        for prompt, line, expected, check, *history in CASES:
            message, dt = ask(model, prompt, line, history[0] if history else None)
            names = [c["function"]["name"] for c in message.get("tool_calls") or []]
            options = expected.split("|") if expected else [expected]
            passed, detail = grade(next((o for o in options if o in names), options[0]), check, message)
            ok += passed
            print(f"  {'OK ' if passed else 'NON'} {dt:4.1f}s  {prompt[:50]:50}  {detail[:110]}", flush=True)
        print(f"  -> {ok}/{len(CASES)}", flush=True)


if __name__ == "__main__":
    main()
