# Security

Voice Island lets a language model act on your Mac (open and quit apps, open websites, press media keys,
hand tasks to Claude Code). Please read this before running it.

## What it can and cannot do

- Tools are a closed list (`web/agent/tools.py`). There is no free-form shell command tool.
- Claude Code is started in read-only `plan` mode by default (`tools.claude_code.permission_mode`).
- **Risky actions wait for your yes.** Opening, placing, searching or typing at the cursor run at once. An action
  that leaves the Mac or cannot be undone (Claude Code allowed to change files, any MCP tool by default) is only
  proposed: the island shows Yes / No, and it runs only when you click Yes or answer "yes" / "oui" yourself. The
  language model, a tool result or a web page can never approve it; calling the tool again only proposes again.
  A proposal expires after 2 minutes or as soon as you ask something else. Per MCP server, `"safe_tools": [...]`
  lists tools that may run without asking, and `"confirm": false` trusts the whole server.
- MCP servers you add in the settings run with your user's rights: only add servers you trust.
- E-mails are only ever opened as Gmail drafts. Nothing is sent without you pressing Send.
- Everything runs locally: speech, language model and voices. Conversations are logged in `web/logs/`
  on your Mac only; delete that folder to erase them.

- "Listen only to my voice" (optional, per product) compares each sentence with a voiceprint: 256 numbers, not
  audio, kept in the macOS login keychain ("VoiceIsland voiceprint") and erasable from the dashboard ("Ma voix").
  It filters other people and videos around you; it is not authentication: a recording or an imitation of your
  voice can pass.

## Network exposure

The local LiveKit server runs in `--dev` mode, whose API key and secret are public. `dev.sh` therefore binds
it to `127.0.0.1` only. `LIVEKIT_ALLOW_INSECURE_LAN=1 LIVEKIT_BIND=0.0.0.0 ./dev.sh` opens it to your local network
(for a phone client); without the first variable `dev.sh` refuses, because:
anyone on that network could then talk to the assistant and drive your Mac. Only do this on a network you trust.

The optional web interface (`./dev.sh web`, port 3010) can change the settings, including MCP servers that start
commands on your Mac, and signs LiveKit tokens. It listens on `127.0.0.1` only (`WEB_BIND`), and its `/api` routes
answer requests from this Mac only: the `Host` and `Origin` headers must be `localhost` or `127.0.0.1`, which also
blocks DNS-rebinding attacks from web pages.

## macOS permissions

The Voice Island app asks for: the microphone, control of Google Chrome (read the active tab, change its
address, close it) and Accessibility (media keys, ⌘N for new windows). Terminal never needs these.

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting on this repository
(Security tab, "Report a vulnerability").
