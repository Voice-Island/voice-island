# Agent

The voice agent (LiveKit Agents, Python). See the [main README](../../README.md).

| File | Role |
|---|---|
| `agent.py` | Session setup, voice / silent / chat modes, action guard, background-noise filter |
| `tools.py` | Mac tools offered to the language model |
| `mac_bridge.py` | Calls into the Voice Island app (Chrome tab, window counts, keys) over LiveKit RPC |
| `stt_moonshine.py` | Streaming Moonshine speech recognition |
| `models_mlx.py`, `tts_server.py` | Voices (Kokoro, Qwen3-TTS) served by a separate process |
| `settings.py` | Shared settings with defaults (`web/settings.json`) |
| `fix_qwen_template.py` | Local Qwen2.5 copies with a fixed tool-calling template (`local:<name>` models) |

```sh
uv run pytest            # unit tests, no servers needed
uv run agent.py dev      # run the agent (web/dev.sh starts everything)
```
