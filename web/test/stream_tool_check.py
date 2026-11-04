"""Checks whether mlx_lm.server returns tool calls in streaming mode, as the agent requests them."""

import json
import sys
import urllib.request

MODEL = sys.argv[1]
body = {
    "model": MODEL,
    "stream": True,
    "max_tokens": 80,
    "messages": [{"role": "system", "content": "Use tools to act."}, {"role": "user", "content": "What time is it?"}],
    "tools": [{"type": "function", "function": {
        "name": "get_current_time", "description": "Get the current local date and time.",
        "parameters": {"type": "object", "properties": {}}}}],
}
req = urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions", data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
text, calls, finish = "", [], None
for line in urllib.request.urlopen(req, timeout=300):
    line = line.decode().strip()
    if not line.startswith("data:") or line == "data: [DONE]":
        continue
    choice = json.loads(line[5:])["choices"][0]
    delta = choice.get("delta", {})
    text += delta.get("content") or ""
    calls += delta.get("tool_calls") or []
    finish = choice.get("finish_reason") or finish
print(f"{MODEL.split('/')[-1]}: appels d'outils={[c.get('function', {}).get('name') for c in calls]} "
      f"fin={finish} texte={text[:120]!r}")
