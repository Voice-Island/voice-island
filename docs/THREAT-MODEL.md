# Voice Island: threat model (2026-10-31)

Built from a map of the code, a secrets and privacy audit of the repository and the conversation logs.
Status: **fixed** (in this tree), **open**, or **accepted** (documented, by design).

## What we protect

1. **Control of the Mac**: the assistant can type text at the cursor, open / quit apps, change Chrome's URL, press
   media keys, move windows, read the screen and the selection, add calendar events and reminders, start Claude Code
   and MCP servers. Voice Island holds the Accessibility, Chrome automation, Calendar and microphone permissions.
2. **Private data**: conversation transcripts, dictated text, screen and selection contents, calendar, Chrome tab
   titles, the owner's work memory (claude-mem, `search_work_memory`), Claude Code reports.
3. **The owner's identity and clients** in the public repository and its history.

## Trust boundaries

```
 untrusted: other devices on the Wi-Fi, web pages in the browser, audio around the Mac (videos, people),
            text on screen / in the selection / in tab titles, tool outputs (YouTube pages, MCP, Claude Code)
 ------------------------------------------------------------------------------------------------------------
 local user session: Voice Island (permissions), agent.py, mlx_lm.server :8080, tts_server :8090,
                     livekit-server :7880, optional Next.js :3010, settings.json / memory.json / learned.json
```

## Threats

| # | Path | Severity | Status | Mitigation |
| --- | --- | --- | --- | --- |
| T1 | Device on the LAN joins a LiveKit room with the public `--dev` keys and talks to the agent or calls the Mac app's RPCs (`mac.text` insert, `mac.calendar`…) | Critical if exposed | **Fixed** (defense in depth) | LiveKit binds 127.0.0.1 (`web/dev.sh`); a LAN bind now refuses to start without `LIVEKIT_ALLOW_INSECURE_LAN=1`; the Mac app answers `mac.*` RPCs only from the room's **agent** participant (`AssistantController.swift`). Limit: the participant kind comes from the token, so anyone holding the public dev keys can mint an "agent" token; the real fix is per-install random keys instead of `devkey/secret` (P0-4). |
| T2 | Device on the LAN or a DNS-rebinding web page writes `settings.json` through the web UI (`PUT /api/settings`): adding a stdio MCP server = **arbitrary command execution**; or mints LiveKit tokens (`/api/token`) | Critical with `./dev.sh web` | **Fixed** | Next.js binds 127.0.0.1 (`WEB_BIND`); `/api/*` reject requests whose `Host` / `Origin` are not localhost (`web/frontend/lib/local-only.ts`). |
| T3 | Prompt injection from untrusted text (screen, selection, Chrome tab title in the per-turn screen line, YouTube results, MCP and Claude Code outputs, emails later) makes the LLM call tools: `insert_text`, `open_website`, `draft_email`, `ask_claude_code` (can edit files in `acceptEdits`), MCP tools | High | **Open** | Today: closed tool list, no shell tool, Claude Code read-only by default, drafts never sent. Needed: tag tool outputs and screen text as data; confirmation policy for high-risk tools (P0-6); never escalate Claude Code permissions from a tool call. |
| T4 | Audio around the Mac (a video, a colleague) is taken as a command | Medium | Partly mitigated | Background-filler filter (`is_background_filler`); mic mute; macOS asks before quitting apps with unsaved work; calendar move/delete asks (NSAlert). Needed: confirmation for destructive / external tools (P0-6), visible action line already shows what was done. |
| T5 | False success reported to the user (an action announced but not done) | Medium | Partly mitigated | Guard retries / honest fallback when the reply claims an action without a tool (now also "added / deleted / typed / set…", "None."). Gap: success is inferred from reply text; no typed outcome (P0-5). The guard never blocks a tool call. |
| T6 | Paths that run tools without the LLM or the guard: `fast_path` (also on partial transcripts via `Early`), learned shortcuts, `memory_rules` | Medium | Accepted, bounded | `Early` limited to open / place (reversible); fast path refuses anything unsure (`NOT_SIMPLE`, strict app resolution, high-cutoff fuzzy match on running apps only for close / place). `memory_rules` no longer triggers on a bare "note" (31 oct.). |
| T7 | stdio MCP servers run whatever command is in `settings.json` with the user's rights | High | Accepted (by design) | Only the owner writes settings (T2 fixed). Needed for public release: a trust prompt when a new MCP server is added, allowlist of tools (exists), documented in SECURITY.md. |
| T8 | Local HTTP services: `mlx_lm.server` answers any origin (`Access-Control-Allow-Origin: *`), `tts_server` accepts simple cross-site POSTs | Low | Open | Only compute abuse (no private data returned). Fix: proxy the LLM through the agent or add an origin check; reject non-local `Host` in `tts_server`. |
| T9 | Transcripts, dictated text, screen text and Claude Code reports in plaintext logs (`web/logs/`, DEBUG level with `lk.pii.*`) | Medium (privacy) | Open | Rotation keeps one previous log per service; `dictation.log` grows forever. Needed: `privacy.log_transcripts` (default off for public builds), retention, an "erase my data" action in the dashboard (P0-7). |
| T10 | Owner identity and client names in the **git history** (blockers B1-B2, high H1 in the secrets audit), session URLs in commit messages | Blocker for publishing | **Open** | Publish a fresh repository (history rewritten or single commit), never make the current one public (GitHub keeps old SHAs). Gitleaks pre-commit + CI, synthetic fixtures only (P0-1). |
| T11 | Supply chain: Python / npm packages, `npx -y pnpm@10` at launch, model weights downloaded from Hugging Face at runtime | Medium | Open | Lock files exist (`uv.lock`); offline mode once models are cached (`HF_HUB_OFFLINE=1` in `dev.sh`). Needed: pinned model revisions (commit hashes), safetensors only, license gate (see `docs/audit/LICENSES.md`). |
| T12 | `search_work_memory` reads the owner's work memory (claude-mem :37777): client data can be spoken aloud or pasted with `insert_text` | Medium (privacy) | Open | Off by default for public builds; scoped to explicit requests; never in screen-shared contexts. |
| T13 | Gmail draft URLs carry the email body (browser history) | Low | Accepted | Drafts only, never sent; document it. |

## Release blockers (must be done before the repository goes public)

1. T10: publish from a clean history; scanning in CI.
2. T1: per-install LiveKit keys (or keep loopback-only and document that the LAN mode is unsupported).
3. T3 / T4 / T5: confirmation policy for high-risk tools and a typed action outcome (proposed → approved →
   attempted → succeeded / failed → verified), shown in the island.
4. T9: transcript logging off by default for public builds, retention and erase.
5. Licenses: see `NOTICE` (restricted turn-detector weights, optional research-only model).
