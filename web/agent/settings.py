"""Settings shared by the agent, the Mac app and the web: a single JSON file, web/settings.json.

The front end writes it (API /api/settings); the agent rereads it at the start of each session and on every
reply for the settings that apply live.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"
# Defaults published for the front end (single source: this file).
DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "settings.defaults.json"
# Local copies of Qwen2.5 with a fixed tool-call template (see fix_qwen_template.py).
LOCAL_MODELS = Path(__file__).resolve().parent.parent / "models"
# Repo root: Claude Code's default working folder.
REPO_ROOT = Path(__file__).resolve().parents[2]
# Qwen3 4B Instruct 2507: Apache 2.0 license, best measured tool choice (web/test/tool_eval.py).
# (Qwen2.5-3B is under the Qwen research license, no commercial use: not the default.)
DEFAULT_LLM = "mlx-community/Qwen3-4B-Instruct-2507-4bit"

DEFAULT_PROMPT = (
    "You are a voice assistant. Always answer in English, even if the user uses another language. "
    "Answer in one to three short sentences, as if speaking out loud: no lists, no markdown, no emojis."
)

# Short instruction given to the LLM in automatic emotion mode (LiveKit's Inworld template
# is ~1000 tokens, too heavy for a small model).
DEFAULT_EMOTION_INSTRUCTIONS = (
    "Before EVERY sentence, write a delivery tag describing how it should sound, in exactly this XML form "
    '<expr type="expression" label="gently warm and reassuring"/>. The label is 2 to 6 plain English '
    'words with a degree word ("a little", "gently", "really"): mood, volume and pace. Match the '
    "moment: soft and slow for sad news, bright for good news, calm by default. Never use square brackets, "
    "never explain the tags.\n"
    'Example: <expr type="expression" label="really bright and excited"/> We did it! '
    '<expr type="expression" label="a little softer, warm"/> Thanks for sticking with me.'
)

DEFAULTS: dict = {
    "llm": {
        "model": DEFAULT_LLM,
        "system_prompt": DEFAULT_PROMPT,
        "temperature": 0.3,
        "top_p": 1.0,
        "max_tokens": 200,
        "memory_turns": 10,
    },
    "stt": {
        # Engine per product: moonshine (English, live text, the fastest) | parakeet (NVIDIA Parakeet v3,
        # 25 languages including French, the same model as dictation, whole sentence transcribed after the silence).
        "engine": "moonshine",          # voice conversation (⌥ Space)
        "command_engine": "parakeet",   # voiceless commands (⌃ ⌥ Space) and chat
        "model": "small",  # tiny | small | medium (Moonshine streaming, English)
        "streaming": True,
        "vad_window": 0.3,  # silence (s) before Moonshine closes the sentence
        "update_interval": 0.3,
        # Proper nouns the transcription must recognize ("Claude", not "cloud").
        "keyterms": ["Claude", "Claude Code", "ClickUp", "YouTube", "Chrome", "VS Code", "Xcode",
                     "LiveKit", "WhatsApp", "Notion"],
    },
    "turn": {
        "vad_threshold": 0.5,
        "min_silence": 0.4,
        # LiveKit's end-of-turn detector (English): off by default, its weights are under the LiveKit Model
        # License (usable only with LiveKit Agents, not an open-source license). Otherwise: silence decides.
        "turn_detector": False,
        "min_endpointing_delay": 0.3,
        "max_endpointing_delay": 2.5,
        "allow_interruptions": True,
        "min_interruption_duration": 0.5,
        "min_interruption_words": 1,
    },
    "tts": {
        "engine": "kokoro",  # kokoro | qwen3-0.6b | qwen3-1.7b | chatterbox-turbo
        "kokoro_voice": "af_heart",
        "qwen_speaker": "Ryan",
        "speed": 1.0,
    },
    "emotion": {
        "mode": "off",  # off | fixed | auto
        "fixed_style": "gently warm and calm",
        "instructions": DEFAULT_EMOTION_INSTRUCTIONS,
        "sounds": False,
    },
    "tools": {
        "enabled": ["open_app", "quit_app", "place_window", "read_selection", "insert_text", "read_screen", "calendar_events", "add_reminder", "open_website", "search_web", "open_folder", "set_volume",
                    "get_current_time", "list_open_apps", "search_youtube", "play_youtube_video", "draft_email",
                    "open_claude_session", "open_terminal", "media_control", "close_tab"],
        # One line per question: front app and open apps (~30 tokens).
        "screen_context": True,
        # Open, close, place a window, pause, search: run without the LLM (fast_path.py), immediate reply.
        "instant_commands": True,
        # Label -> Chrome profile ("personal" = default profile if missing).
        "accounts": {"personal": "Default"},
        "claude_code": {
            "project_dir": "",  # empty = this repo
            "permission_mode": "plan",  # plan = read-only; acceptEdits = can change files
            "timeout_minutes": 10,
        },
        # MCP servers: {"name", "url"} (HTTP) or {"name", "command", "args", "env"} (stdio),
        # "allowed_tools" to limit the list (small models get lost with too many tools).
        # Each call waits for the user's yes (P0-6), except "safe_tools": [...] or "confirm": false.
        "mcp_servers": [],
    },
    # Looking at the screen (look_at_screen tool): 2nd local server (mlx_vlm.server, port 8081, web/vision), started
    # by dev.sh only if the tool is enabled. Choice of Nov 2: Qwen3.5 2B, 10/12 on web/test/vision_eval.py; the
    # main LLM stays on mlx_lm.server (same model on mlx_vlm.server: 16/20 tools instead of 18/20).
    # On demand only, never continuous; the image stays in the app and this server, nothing on disk.
    "vision": {
        "model": "mlx-community/Qwen3.5-2B-MLX-8bit",
        "max_side": 1280,   # px, longest side of the window capture (~1,000 image tokens)
        "max_tokens": 160,
    },
    # Dictation anywhere (Mac app, ⌃ ⌥ D held): Apple recognition on the Mac, then cleanup by the LLM.
    "dictation": {
        # parakeet: NVIDIA Parakeet TDT 0.6B v3 (CC-BY-4.0, 25 languages including French and German) on the voice
        # server, 0.2-0.3 s for 6 s of audio, 1.3 GB. Whisper large-v3-turbo gave the same text in 3.5 s (Oct 26).
        # apple: Apple recognition, requires macOS Dictation to be enabled.
        "engine": "parakeet",
        "model": "mlx-community/parakeet-tdt-0.6b-v3",
        "language": "auto",  # apple engine only: auto (Mac language) | fr-FR | en-US | de-CH…
        "cleanup": False,    # Parakeet already punctuates; True: the LLM also removes hesitations
        # Trigger: shortcut (dashboard shortcut, ⌥ N by default) | fn (hold Fn alone; Fn + another
        # key cancels). The macOS 🌐 key must then be set to "Do Nothing".
        "trigger": "shortcut",
    },
    # "Listen only to my voice" (voice_id.py): chosen product by product, only if a voiceprint exists ("My voice"
    # page of the dashboard). A sentence from another voice is ignored. It is not a lock: a
    # recording of the voice gets through.
    "voice_id": {
        "dictation": False,   # dictation (⌥ N)
        "command": False,     # voice commands (⌃ ⌥ Space)
        "assistant": False,   # voice assistant (⌥ Space)
        "threshold": 0.39,    # measured Oct 31: owner ≥ 0.42, other voices ≤ 0.36
        # A very short word (< 1 s) is hard to judge: accepted if the owner's previous sentence is less than N s old.
        "short_grace": 20,
    },
    "ui": {
        "visualizer": "bar",  # bar | wave | grid | radial | aura (LiveKit visualizers)
        "theme": "blue",
        "show_metrics": True,
        # Mac app frame: "notch" (top center, under the camera) or "top_left" (top left).
        "island_position": "notch",
        # Short sound when an action is done (or fails): immediate feedback, even without voice.
        "action_sounds": True,
    },
    # P0-7. History (web/history/, one file per day): conversations, actions done and dictations, erasable in
    # Dashboard › Privacy. Kept by default (choice of Oct 31), for at most history_days days.
    # log_transcripts: what you say, in the service logs (web/logs), for debugging. Off by
    # default: the logs only keep the length of the text (privacy.py).
    "privacy": {
        "keep_history": True,
        "history_days": 30,
        "log_transcripts": False,
    },
}


def resolve_model(name: str) -> str:
    """'local:<folder>' -> path in web/models (copies with the fixed tool template); otherwise unchanged."""
    return str(LOCAL_MODELS / name.removeprefix("local:")) if name.startswith("local:") else name


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_settings() -> dict:
    """Saved settings merged with the defaults (a new setting takes its default)."""
    try:
        saved = json.loads(SETTINGS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        saved = {}
    return _merge(DEFAULTS, saved)


def write_defaults() -> None:
    DEFAULTS_PATH.write_text(json.dumps(DEFAULTS, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    write_defaults()
    print(DEFAULTS_PATH)
