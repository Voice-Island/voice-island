# Voice Island (working name)

A voice assistant that runs **entirely on your Mac**: speech recognition, language model and voice, with no
cloud service. It is three products in one menu-bar app:

1. **Dictation anywhere**: hold a key (Fn alone, or a hotkey), speak, release: the text is typed at the cursor
   in any app, never sent. French, English, German and 22 more languages.
2. **Voice commands**: talk, it acts on your Mac and answers in text: opens apps and new windows, places them
   left / right, searches and plays YouTube in the tab you are on, pauses music, drafts Gmail e-mails, hands
   coding tasks to Claude Code. Simple commands ("Chrome on the left", "mets Notes à droite") run instantly,
   without the language model. Risky actions (sending, publishing, letting Claude Code change files, MCP tools)
   wait for your yes, said or clicked in the island ([SECURITY.md](SECURITY.md)).
3. **Voice assistant**: a spoken conversation, with the same actions.

Optionally, each product can **listen only to your voice**: other people and videos around you are ignored,
never transcribed. And the assistant can **look at your screen** when you ask ("what am I looking at?", "which
month has the highest bar?", "where is the Save button?"), with a local vision model, never continuously.

> **Status: alpha.** Built and tested on one MacBook Air (Apple M3, 24 GB). Expect rough edges.

## How it works

A streaming pipeline of open models, orchestrated with [LiveKit Agents](https://github.com/livekit/agents):

```
mic ─▶ Silero VAD ─▶ [is it your voice? WeSpeaker, optional] ─▶ Parakeet v3 or Moonshine (speech → text)
    ─▶ instant commands, or Qwen3-4B on MLX (+ tools) ─▶ Kokoro / Qwen3-TTS ─▶ speaker (assistant only)
```

| Part | Default | Runs on |
|---|---|---|
| Speech to text, dictation and commands | Parakeet TDT 0.6B v3, 25 languages (CC-BY-4.0) | GPU, `tts_server.py` |
| Speech to text, assistant | Moonshine streaming small, English (MIT); Parakeet selectable | CPU |
| Language model | Qwen3-4B-Instruct-2507, 4-bit (Apache 2.0); MiniCPM5 2B, Qwen3.5 2B / 4B and others selectable | GPU, `mlx_lm.server` |
| Voice | Kokoro-82M (Apache 2.0), or Qwen3-TTS for emotions | GPU, `tts_server.py` |
| Speaker check (optional) | WeSpeaker ResNet34-LM, ONNX (CC-BY-4.0) | CPU |
| Looking at the screen (optional) | Qwen3.5 2B, 8-bit (Apache 2.0), on request only | GPU, `mlx_vlm.server` (`web/vision`) |
| Transport | LiveKit server, local only | — |
| App | Voice Island, SwiftUI menu-bar app | — |

What this project adds on top of those models: the Mac tools and the native app, instant commands in French
and English, a guard that stops the model from claiming actions it did not take, live display of what was
really done (verified, done, sent without a known result, or failed), background-noise filtering, window and
Chrome-tab awareness, a chat mode and the speaker check.

## Hotkeys (all editable in the dashboard)

| Product | Default | What it does |
|---|---|---|
| Dictation | ⌃ ⌥ D held, or **Fn alone** held (dashboard › Dictée › Déclencheur) | Types what you say at the cursor; Fn with another key cancels |
| Voice commands | ⌃ ⌥ Space | Talk, it acts and answers in text. During a voice conversation, toggles spoken replies |
| Chat | ⌃ ⌥ T | Type, it acts and answers in text |
| Voice assistant | ⌥ Space | Talk, it answers out loud. ⌃ ⌥ M mutes the microphone, the conversation stays open |
| Dashboard | ⌥ ⇧ Space | Services, then one page per product, then what they share: language model, tools, my voice, appearance |

## Listen only to my voice (optional)

In the dashboard, **Ma voix** records three short readings and stores a voiceprint (256 numbers, not audio)
in the macOS login keychain. Then turn it on per product (dictation, commands, assistant): a sentence spoken
by another voice is dropped before transcription, silently. Measured on the owner's voice against 300 other
voices and voices replayed in the room: full separation at the default threshold, about 0.1 s per sentence
([docs/VOICE-ID-BENCHMARK.md](docs/VOICE-ID-BENCHMARK.md)). It filters people and videos around you; it is not
authentication: a recording or an imitation of your voice can pass.

## Look at my screen (optional)

Turn on **Regarder l'écran** in dashboard › Outils, then restart the stack (`web/dev.sh`): it starts a second
local server, `mlx_vlm.server` on 127.0.0.1:8081, with Qwen3.5 2B (about 2.5 GB more memory, 2.4 GB download).
When you ask something visual, the app captures the window in front (not the whole screen, never its own
windows) and sends it to that server itself; only the text answer reaches the assistant. Nothing runs between
questions, and the image is never written to disk; what the assistant says about it is kept like any other
answer, in the conversation history and `web/logs/` (erasable from the dashboard). It needs the Screen
Recording permission. About 2 to 4 s per
answer on a MacBook Air M3. Text-only questions ("read this e-mail") still use the faster **Lire la fenêtre**
tool (Accessibility, then Apple's on-device OCR). Model choice: `web/test/vision_eval.py`, 12 synthetic Mac
screens.

## Requirements

- A Mac with Apple Silicon (M1 or newer) and macOS 14 or later. 16 GB of memory or more for all three products
  (8 GB: dictation only). The dashboard page "Ton Mac" tells you what your Mac runs well.
- [Xcode](https://apps.apple.com/app/xcode/id497799835) (free, App Store): open it once after installing.
- [Homebrew](https://brew.sh) (free).
- About 10 GB of free disk space (the models take about 5 GB).
- Optional: Google Chrome for the browser tools; Node 22 and pnpm for the web interface;
  [Claude Code](https://claude.com/claude-code) for the coding tool.

## Install

Open the Terminal app, paste these three lines, press Enter:

```sh
git clone https://github.com/Voice-Island/voice-island.git
cd voice-island
./install.sh
```

`install.sh` checks your Mac, installs the tools (`livekit`, `xcodegen`, `uv` with Homebrew), the Python
packages, builds the app into `~/Applications/Voice Island.app`, downloads the models and starts everything.
The first run takes a while, mostly downloads. It stops with a clear message if something is missing,
and writes the details to `web/logs/install.log`. Run it again at any time, for example after `git pull` to update.

Every day after that: open **Voice Island** (Spotlight), then Dashboard (⌥ ⇧ Space) › État › Démarrer tout.
A signed `.dmg` for people who don't use the Terminal is planned.

<details>
<summary>What install.sh does, step by step (manual install)</summary>

```sh
brew install livekit xcodegen uv

# Agent: Python packages and the voice-activity model
cd web/agent && uv sync && uv run agent.py download-files && cd ../..

# Build the Mac app
cd mac && xcodegen generate
xcodebuild -project VoiceIsland.xcodeproj -scheme VoiceIsland -configuration Debug -derivedDataPath build build
cp -R build/Build/Products/Debug/VoiceIsland.app ~/Applications/"Voice Island.app" && cd ..

# Start the local stack (LiveKit, language model, voice server, agent), then open the app
web/dev.sh
open ~/Applications/"Voice Island.app"
```

</details>

The app asks for the microphone, Accessibility (window placement, media keys, the Fn key), Input Monitoring
(the Fn key), Screen Recording (reading or looking at the window in front, on request) and control of Google
Chrome. It is signed ad hoc by default, so macOS asks again after every
rebuild; to keep the permissions, create `mac/Config/Local.xcconfig` with your own signing identity (see
`mac/Config/Base.xcconfig`).

To dictate with **Fn alone**, choose it in dashboard › Dictée, set System Settings › Keyboard › "Press 🌐 key
to" to "Do Nothing", and remove Fn from any other dictation app.

`web/dev.sh web` also starts the web interface on http://localhost:3010. It receives this install's LiveKit keys
(generated on first start in `web/livekit.keys`, never committed) from `dev.sh`, and only answers this Mac.
`web/dev.sh stop` stops everything.

## Tests

```sh
cd web/agent && uv run pytest          # fast, no models or servers needed
```

End-to-end checks against a running stack live in `web/test/` (`make_clips.sh` first):
`live_switch_check.py` (silent / voice switch, Chrome bridge, media keys), `chat_check.py`,
`tool_eval.py [model …]` and `intuition_eval.py` (tool choice of the language model, French and window cases),
`route_report.py` (which real sentences run instantly), `vision_eval.py` (vision models on synthetic screens,
`web/test/vision/`). Speaker-check measurements: `web/test/voice_id/`.

## Roadmap

[docs/ROADMAP.md](docs/ROADMAP.md) for product ideas. Security model in [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

## Work memory (optional)

If you use [claude-mem](https://github.com/thedotmack/claude-mem) with Claude Code, enable the
`search_work_memory` tool: the assistant can then answer questions about your past projects and decisions
from that local memory ("What did we decide about the X proposal?").

## Privacy and security

Nothing leaves your Mac, except what you ask for (websites, YouTube, Claude Code, MCP servers you add).
LiveKit here is the open-source server running on your Mac (`ws://127.0.0.1:7880`), not LiveKit Cloud. Once
the models are downloaded, `web/dev.sh` starts in offline mode and the assistant works without internet
(`HF_HUB_OFFLINE=0 web/dev.sh` to download a newly chosen model).

Everything listens on 127.0.0.1 only. Opening LiveKit to the local network needs an explicit
`LIVEKIT_ALLOW_INSECURE_LAN=1`, because its dev keys are public; the Mac app only obeys the assistant
participant; the web interface's API refuses requests that do not come from this Mac.

With Wi-Fi turned off, the Mac has no network address left except 127.0.0.1, which the WebRTC engine of the
LiveKit SDKs ignores, so the app cannot connect. `sudo web/scripts/local-address.sh install` adds a private
address (10.254.77.1, on the Thunderbolt bridge) at every boot so it works with Wi-Fi off too; the address never
leaves the Mac. `test` adds it until the next restart, `uninstall` removes it.

Conversations and dictated text are logged in `web/logs/` locally; the voiceprint stays in your keychain and
can be erased from the dashboard. See [SECURITY.md](SECURITY.md) for what the tools can do, the network
settings and the macOS permissions.

## License

Voice Island is dual-licensed:

- **GNU AGPL-3.0** ([LICENSE](LICENSE)): free for everyone, including companies. If you modify Voice Island and
  distribute it, or let others use it over a network, you must publish your full source under the AGPL-3.0.
- **Commercial license** ([COMMERCIAL.md](COMMERCIAL.md)): for products and companies that want to build on
  Voice Island without publishing their own code.

Code contributions will open once the Contributor License Agreement, which keeps this dual licensing possible, is
ready. Until then, issues, bug reports and tests on other Macs are very welcome. The models and libraries this
builds on keep their own licenses, listed in [NOTICE](NOTICE). Note that the
default LiveKit turn detector may only be used with LiveKit Agents, and that Parakeet and WeSpeaker require
attribution (CC-BY-4.0).
