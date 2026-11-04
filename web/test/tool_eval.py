"""Compares local LLMs on tool choice: same system prompt and same tools as the agent.

Goes through mlx_lm.server (port 8080), so it measures exactly what the assistant will experience.
  cd web/agent && uv run python ../test/tool_eval.py [model …]   # no argument: the MODELS list
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
from livekit.agents.llm import ChatContext, function_tool  # noqa: E402,F401
from livekit.agents.llm.utils import build_legacy_openai_schema  # noqa: E402

from settings import LOCAL_MODELS, load_settings  # noqa: E402
from tools import ALL_TOOLS, TOOLS_HINT, note_role  # noqa: E402

URL = os.environ.get("TOOL_EVAL_URL", "http://127.0.0.1:8080/v1/chat/completions")
MODELS = [
    str(LOCAL_MODELS / "Qwen2.5-1.5B-Instruct-4bit-tools"),
    str(LOCAL_MODELS / "Qwen2.5-3B-Instruct-4bit-tools"),
    "mlx-community/Qwen3-4B-Instruct-2507-4bit",
    "mlx-community/gemma-4-e4b-it-4bit",
]
SCREEN = ("(Screen context, do not read aloud) Front app: Google Chrome. Open apps: Google Chrome, Terminal, "
          "Code, Finder, Notes, Mail.")

# (request, expected tool or None, argument check)
CASES = [
    ("Open Chrome please.", "open_app", lambda a: "chrome" in a.get("name", "").lower()),
    ("Is my terminal open?", "list_open_apps|none", None),
    ("Search YouTube for lofi beats on my personal account.", "search_youtube",
     lambda a: "lofi" in a.get("query", "").lower() and "personal" in a.get("account", "personal").lower()),
    ("Search YouTube for the quarterly demo video with my work account.", "search_youtube",
     lambda a: "work" in a.get("account", "").lower()),
    ("Ask Claude Code to check if I have new open tasks on ClickUp.", "ask_claude_code", None),
    ("Can you check on cloud code if the build passes?", "ask_claude_code", None),
    ("What time is it?", "get_current_time", None),
    ("Set the volume to 30.", "set_volume", lambda a: int(a.get("percent", -1)) == 30),
    ("Open my downloads folder.", "open_folder", lambda a: "download" in a.get("name", "").lower()),
    ("What's the capital of France? Don't search the web.", None, None),
    ("Tell me a short joke.", None, None),
    ("Quit Music.", "quit_app", lambda a: "music" in a.get("name", "").lower()),
    # Windows and French (Oct 29): what the voiceless commands hear with Parakeet.
    ("Open a new Chrome window.", "open_app",
     lambda a: "chrome" in a.get("name", "").lower() and a.get("new_window") in (True, "true")),
    ("Put Notes on the right side of the screen.", "place_window",
     lambda a: "notes" in a.get("name", "").lower() and a.get("where", "").lower() == "right"),
    ("Mets Chrome à gauche.", "place_window",
     lambda a: "chrome" in a.get("name", "").lower() and a.get("where", "").lower() == "left"),
    ("Ouvre Notes.", "open_app", lambda a: "notes" in a.get("name", "").lower()),
    ("Mets le volume à 40.", "set_volume", lambda a: int(a.get("percent", -1)) == 40),
    ("Quelle heure est-il ?", "get_current_time", None),
    ("Ferme Spotify.", "quit_app", lambda a: "spotify" in a.get("name", "").lower()),
    ("Raconte-moi une blague courte.", None, None),
]


def tool_schemas() -> list:
    return [build_legacy_openai_schema(t) for t in ALL_TOOLS.values()]


def ask(model: str, prompt: str) -> tuple[dict, float]:
    settings = load_settings()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": f"{settings['llm']['system_prompt']}\n\n{TOOLS_HINT}"},
            {"role": note_role(model), "content": SCREEN},
            {"role": "user", "content": prompt},
        ],
        "tools": tool_schemas(),
        "max_tokens": 120,
        "temperature": float(os.environ.get("TOOL_EVAL_TEMPERATURE", "0.2")),
        "chat_template_kwargs": {"enable_thinking": False},   # like the agent (NO_THINKING)
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    data = json.loads(urllib.request.urlopen(req, timeout=900).read())
    return data["choices"][0]["message"], time.perf_counter() - t


def grade(expected, check, message) -> tuple[bool, str]:
    calls = message.get("tool_calls") or []
    names = [c["function"]["name"] for c in calls]
    if expected is None:
        return not calls, ("aucun outil" if not calls else f"a appelé {names}")
    if expected == "list_open_apps|none":
        text = (message.get("content") or "").lower()
        ok = names == ["list_open_apps"] or (not calls and "yes" in text)
        return ok, (", ".join(names) or f"texte : {text[:60]}")
    if not calls:
        return False, f"aucun outil (texte : {(message.get('content') or '')[:60]})"
    call = calls[0]
    if call["function"]["name"] != expected:
        return False, f"mauvais outil : {call['function']['name']}"
    try:
        args = json.loads(call["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return False, "arguments illisibles"
    if check and not check(args):
        return False, f"mauvais arguments : {args}"
    return True, f"{expected}({args})"


def main() -> None:
    results = {}
    for model in sys.argv[1:] or MODELS:
        name = model.rstrip("/").split("/")[-1]
        print(f"\n=== {name} (chargement…)", flush=True)
        try:
            ask(model, "Hi")  # download / load, not measured
        except Exception as e:
            print(f"  impossible de charger : {e}", flush=True)
            continue
        ok, times = 0, []
        for prompt, expected, check in CASES:
            try:
                message, dt = ask(model, prompt)
                passed, detail = grade(expected, check, message)
            except Exception as e:
                passed, detail, dt = False, f"erreur : {e}", 0.0
            ok += passed
            times.append(dt)
            print(f"  {'OK ' if passed else 'NON'} {dt:4.1f}s  {prompt[:55]:55}  {detail[:90]}", flush=True)
        results[name] = (ok, len(CASES), sum(times) / len(times))
        print(f"  -> {ok}/{len(CASES)} corrects, {results[name][2]:.1f} s en moyenne", flush=True)
    print("\n=== Résumé")
    for name, (ok, total, avg) in results.items():
        print(f"{name:40} {ok:2}/{total}  {avg:.1f} s")


if __name__ == "__main__":
    main()
