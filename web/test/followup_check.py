"""Replays the "after the tool" step: the model receives a tool's result and must answer out loud."""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
from settings import load_settings  # noqa: E402
from tools import TOOLS_HINT  # noqa: E402

MODEL = sys.argv[1]
prompt = load_settings()["llm"]["system_prompt"] if len(sys.argv) < 3 else sys.argv[2]
messages = [
    {"role": "system", "content": f"{prompt}\n\n{TOOLS_HINT}"},
    {"role": "user", "content": "What time is it?"},
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "get_current_time", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "It is 14:40 on Friday, October 23, 2026."},
]
tools = [{"type": "function", "function": {"name": "get_current_time", "description": "Get the current time.",
                                           "parameters": {"type": "object", "properties": {}}}}]
for stream in (False, True):
    body = {"model": MODEL, "messages": messages, "tools": tools, "max_tokens": 80, "stream": stream}
    req = urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=300)
    if not stream:
        msg = json.loads(resp.read())["choices"][0]["message"]
        print("sans flux :", repr(msg.get("content")), msg.get("tool_calls"))
    else:
        text = ""
        for line in resp:
            line = line.decode().strip()
            if line.startswith("data:") and line != "data: [DONE]":
                text += json.loads(line[5:])["choices"][0].get("delta", {}).get("content") or ""
        print("en flux   :", repr(text))
