# Roadmap

Ideas for later. Nothing here is scheduled.

## Fast tool model: fine-tuned Qwen3-1.7B (option, after the public release)

**Why.** The default Qwen3-4B is reliable with tools (22/22 on `web/test/tool_eval.py` +
`intuition_eval.py`), but slow to read its ~2,700-token prompt. Untrained small models are not good enough:
Qwen2.5-1.5B scored 7/12. A small model only becomes useful through training.

**Plan.**
- Base model: Qwen3-1.7B (Apache 2.0, newer than Qwen2.5-1.5B). Measure it untrained first.
- Method: LoRA with `mlx_lm.lora` on the Mac (`--mask-prompt`, so only the assistant's turns are learned).
  Limits the risk of losing general conversation skills.
- Data: 2,000 to 5,000 examples in the chat format with tools (see below), covering:
  - every tool, with many phrasings, including speech-recognition errors ("cloud code" for "Claude Code");
  - requests that need no tool: general questions, jokes, "no, stop";
  - screen-dependent cases: YouTube already open, window counts, the visible Chrome tab;
  - follow-ups and clarification questions;
  - the reply after a tool: summarise the result honestly, never say "sent" for a draft;
  - some general conversation, so it stays pleasant.
- Data generation: a large open model (for example a big Apache-2.0 Qwen3) writes candidates, then human
  review and filtering. Do not use a commercial model as the teacher: their terms often forbid training a
  competing model on their outputs. Real conversation logs only if anonymised.
- Held-out evaluation: `tool_eval.py` and `intuition_eval.py`, never trained on. Goal: match the 4B at
  twice the speed.

**Estimate (to confirm on the first run).**

| Step | Time | Cost |
|---|---|---|
| Prepare the data | 3 to 5 days (most of the work) | 0 locally, about $20–100 if generated online |
| LoRA training | 1 to 3 h on the Mac (MLX), or under 1 h on a rented GPU | 0, or about $5–20 |
| Evaluation and 2–3 iterations | 2 to 3 days | negligible |
| Total | 1 to 2 weeks of work | under about $100–200 |

**Risks.**
- General conversation may get worse; the conversation share of the data and LoRA limit this.
- Every new tool needs retraining, where today a prompt change is enough. User-added MCP tools will work
  less well with the small model.
- Quality depends on the data. Sloppy data gives a model that hallucinates actions.

**Outcome.** The 4B stays the "quality" option; the trained 1.7B becomes the "fast" option and the project's
first own model.

### What one training example looks like

One line of the JSONL file (pretty-printed here). `tools` holds the same schemas the agent sends
(`build_legacy_openai_schema`), shortened below to one tool.

```json
{
  "tools": [
    {"type": "function", "function": {
      "name": "play_youtube_video",
      "description": "Play a song or a video on YouTube. With no query, plays a video from the YouTube search the user is looking at.",
      "parameters": {"type": "object", "properties": {
        "query": {"type": "string"}, "position": {"type": "integer"}, "account": {"type": "string"}}}}}
  ],
  "messages": [
    {"role": "system", "content": "You are a voice assistant. ... (system prompt + tool rules, as in production)"},
    {"role": "system", "content": "Current local time: 21:14, Friday 24 October 2026.\nRight now on the user's screen (use only if relevant, never read it out): front app Google Chrome; open apps: Google Chrome (2 windows, 7 tabs in the front one), Code (3 windows); Chrome tab: \"lofi hip hop - YouTube\" https://www.youtube.com/results?search_query=lofi+hip+hop."},
    {"role": "user", "content": "Play the second one."},
    {"role": "assistant", "content": "", "tool_calls": [
      {"id": "call_1", "type": "function",
       "function": {"name": "play_youtube_video", "arguments": "{\"query\": \"\", \"position\": 2}"}}]},
    {"role": "tool", "tool_call_id": "call_1",
     "content": "Playing 'Best of lofi hip hop 2021' in the current tab."},
    {"role": "assistant", "content": "Playing the second one: Best of lofi hip hop 2021."}
  ]
}
```

What the model learns from it: read the screen line, choose the tool, fill the arguments (`query` empty because
the search is already on screen, `position` 2), then report exactly what the tool returned. Negative examples
have the same shape with a plain assistant answer and no `tool_calls`.

Caveat (checked in `mlx_lm/tuner/datasets.py`, mlx-lm 0.32): with `--mask-prompt`, only the **last** message of
each example is trained. Split every conversation into two lines: one that ends with the assistant's
`tool_calls` message (learns the call), and the full one above (learns the honest summary).
