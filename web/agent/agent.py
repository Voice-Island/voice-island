"""100% local LiveKit voice agent: Moonshine (STT) -> LLM via mlx_lm.server -> Qwen3-TTS / Kokoro (MLX).

Launch (see ../dev.sh):
  uv run agent.py download-files   # once: end-of-turn detection model
  uv run agent.py dev
"""

from __future__ import annotations

import asyncio
import json
from collections import deque

import httpx
import numpy as np
import logging
import os
import time
from datetime import datetime
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
import re

from livekit.agents import (Agent, AgentServer, AgentSession, APIConnectOptions, JobContext, JobProcess, StopResponse,
                            cli, metrics)
from livekit import rtc
from livekit.agents import stt
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.english import EnglishModel

from models_mlx import BRACKET_RE, TTS_URL, LocalTTS, supports_instruct
from settings import load_settings, resolve_model, write_defaults
from livekit.agents.llm import mcp

import fast_path
import history
import outcome
import policy
from outcome import Outcome, Status, failed, succeeded
import mcp.types as mcp_types
import voice_id
import learned
import lk_keys
import privacy
from privacy import r
import memory_rules
from tools import (claude_status_line, compose_instructions, enabled_tools, model_supports_tools, note_role,
                   screen_context_line)
from stt_moonshine import MoonshineSTT
from stt_parakeet import ParakeetSTT

load_dotenv()
lk_keys.apply_env()   # this install's keys (P0-4), take precedence over an old devkey in .env
privacy.install()     # P0-7: no transcripts in the logs, unless the setting says otherwise
logger = logging.getLogger("voice.agent")

LLM_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:8080/v1")
METRICS_TOPIC = "voice.metrics"
# What the tools really did, shown live in the Mac app's frame.
ACTIONS_TOPIC = "voice.actions"

# Guard: an action request, a reply that says "done", and no tool called = imaginary action
# (seen in the Oct 24 logs: "Opening a new terminal window", "Email sent", without launching anything).
ACTION_REQUEST = re.compile(
    r"\b(open|launch|start|quit|close|kill|play|pause|resume|stop|search|look up|find|google|draft|write|e-?mail|"
    r"send|new (window|tab|terminal)|switch|go to|show|full ?screen|volume|mute|next|skip|put|move|place|snap|"
    r"maximi[sz]e|arrange|split|add|delete|remove|type|insert|write|remind|schedule|set|move)\b", re.I)
ACTION_CLAIM = re.compile(
    r"\b(opening|opened|launching|launched|starting|started|quitting|quit|closing|closed|playing|paused|pausing|"
    r"resumed|searching|searched|sending|sent|drafted|creating|created|switching|switched|skipped|has been|"
    r"moved|moving|placed|placing|snapped|maximi[sz]ed|arranged|put (?:it|them|the)|added|adding|deleted|deleting|"
    r"removed|removing|typed|typing|inserted|inserting|scheduled|reminder (?:is )?set|set (?:the|your|it|a)\b|"
    r"is now|are now|now (open|playing|showing|in))\b", re.I)
# On a plain question (no action verb), only phrasings where the assistant says it is acting count: "creating"
# or "is now" also appear in normal answers ("… creating intricate light shows").
SELF_CLAIM = re.compile(
    r"^\W*(?:(?:i'?m|i am|i'?ll|i will|let me|now)\s+)?(?:searching|opening|launching|playing|checking|looking (?:up|into)|"
    r"sending|drafting|closing|quitting|pausing|starting|fetching)\b|\bi(?:'ve| have) (?:opened|searched|sent|launched|"
    r"played|closed|quit|paused|started|drafted|checked|created)\b", re.I)
RETRY_NOTE = ("Your last answer said you did it, but you called no tool, so nothing happened on the Mac. Call the "
              "right tool now. If no tool can do it, say in one short sentence that you cannot do that.")
HONEST_FALLBACK = "Sorry, I didn't do anything: I couldn't find a way to do that."

# Background noise mistaken for questions (a video playing, a conversation nearby): no reply,
# unless the assistant just asked a question ("yes" then answers something).
FILLERS = {"thank you", "thanks", "thank", "you", "yeah", "yes", "yep", "yup", "okay", "ok", "mm", "mmm", "hmm",
           "mm-hmm", "uh", "um", "uh-huh", "right", "that's right", "bro", "good", "cool", "nice", "wow", "oh",
           "so", "and", "but", "well", "alright", "all right", "sure", "great", "fine", "huh", "ah", "bye",
           "bye-bye", "it", "is", "the", "that", "this", "very"}
# "Command" mode rooms: transcription + LLM + tools, text reply, no voice.
# The voice is then turned on / off live from the Mac app (RPC VOICE_METHOD, "on" / "off").
COMMAND_PREFIX = "command_"
# "Chat" rooms: the user types (mic off on the app side), text replies, same tools and guards.
# The mic and the voice can still be turned on live from the frame.
CHAT_PREFIX = "chat_"
CHAT_PROMPT = (
    "You are an assistant on the user's Mac. The user types to you. Carry out requests with your tools. "
    "Reply in plain text, short and direct: one to four sentences, no markdown, no lists. Answer in the language "
    "the user writes in."
)
VOICE_METHOD = "agent.set_voice"
# Yes / No clicked in the frame on an action to confirm (P0-6): {"id": "p3", "answer": "yes" | "no"}.
CONFIRM_METHOD = "agent.confirm"
COMMAND_PROMPT = (
    "You are a command assistant on the user's Mac. The user speaks short commands. Carry them out with your "
    "tools. Then reply with a plain-text confirmation of at most ten words. If no tool fits, answer in one short "
    "sentence."
)

_llms: dict[tuple, openai.LLM] = {}
# LiveKit's default is 10 s: too short when the prompt is not cached yet, and the retry
# joins mlx_lm.server's queue (which handles one request at a time).
LLM_CONN = APIConnectOptions(timeout=60.0, max_retry=1)


# MiniCPM5, Qwen3.5, LFM: thinking off (a second or more of hidden text before each reply). Templates
# that don't know the option ignore it. Same value everywhere: the cached prompt stays identical.
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}


def make_llm(cfg: dict) -> openai.LLM:
    # Long timeout: on a busy Mac (Qwen3-TTS voice in parallel), the first token can be slow;
    # a short timeout retries the request and loses several seconds.
    return openai.LLM(
        model=resolve_model(cfg["model"]), base_url=LLM_URL, api_key="local",
        temperature=cfg["temperature"], top_p=cfg["top_p"], max_completion_tokens=int(cfg["max_tokens"]),
        timeout=httpx.Timeout(60.0, connect=5.0), extra_body=NO_THINKING)


def llm_for(cfg: dict) -> openai.LLM:
    """LLM client for the current settings (mlx_lm.server loads the requested model on the fly)."""
    key = (cfg["model"], cfg["temperature"], cfg["top_p"], cfg["max_tokens"])
    if key not in _llms:
        _llms[key] = make_llm(cfg)
    return _llms[key]


def warm_tts(engine: str) -> None:
    """Asks the voice server to load the chosen engine (if it hasn't already)."""
    req = urllib.request.Request(
        f"{TTS_URL}/preload", data=json.dumps({"engine": engine}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=300).read()
    except Exception as e:
        logger.warning("serveur de voix injoignable (%s) : tts_server.py tourne-t-il ?", e)


def warm_llm(model: str) -> None:
    """Has mlx_lm.server load the model before the first call."""
    body = json.dumps({"model": resolve_model(model), "messages": [{"role": "user", "content": "Hi"}],
                       "max_tokens": 1, **NO_THINKING})
    req = urllib.request.Request(
        f"{LLM_URL}/chat/completions", data=body.encode(), headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=300).read()
    except Exception as e:
        logger.warning("préchauffage LLM impossible (%s) : mlx_lm.server tourne-t-il ?", e)


def strip_brackets(buffer: str) -> tuple[str, str]:
    """Removes complete [tags]; holds back a "[" that is not closed yet."""
    clean = BRACKET_RE.sub("", buffer)
    open_at = clean.rfind("[")
    if open_at != -1 and "]" not in clean[open_at:] and len(clean) - open_at < 80:
        return clean[:open_at], clean[open_at:]
    return clean, ""


class ConfirmingMCP:
    """An MCP tool can do anything (send, publish, delete): each call first waits for the user's yes
    (P0-6), except tools listed in "safe_tools" or a server set to "confirm": false."""

    def _confirm_setup(self, cfg: dict) -> None:
        self._label = cfg.get("name") or "MCP"
        self._confirm = cfg.get("confirm", True)
        self._safe = set(cfg.get("safe_tools") or [])

    async def _call_tool(self, name, arguments, **kwargs):
        if not getattr(self, "_confirm", False) or name in self._safe:
            return await super()._call_tool(name, arguments, **kwargs)
        server = self

        async def run() -> Outcome:
            result = await super(ConfirmingMCP, server)._call_tool(name, arguments)
            text = "\n".join(getattr(part, "text", str(part)) for part in result.content)
            return failed(text, name) if result.is_error else succeeded(text, name)

        args = json.dumps(arguments, ensure_ascii=False)
        proposed = policy.propose(f"{self._label}.{name}", f"{name} ({self._label}) {args[:140]}", run)
        return mcp_types.CallToolResult(content=[mcp_types.TextContent(type="text", text=str(proposed))],
                                        is_error=False)


class ConfirmingHTTPServer(ConfirmingMCP, mcp.MCPServerHTTP):
    pass


class FilteredStdioServer(ConfirmingMCP, mcp.MCPServerStdio):
    """Local MCP server limited to some tools: LiveKit only offers allowed_tools for HTTP servers.
    Small models get lost with too many tools (claude-mem exposes 12, 2 are enough)."""

    def __init__(self, *args, allowed_tools: list[str] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._allowed = set(allowed_tools) if allowed_tools else None

    async def list_tools(self, *, tool_options=None):
        tools = await super().list_tools(tool_options=tool_options)
        if self._allowed is None:
            return tools
        return [t for t in tools if getattr(getattr(t, "info", None), "name", None) in self._allowed]


def mcp_servers(settings: dict) -> list[mcp.MCPServer]:
    """MCP servers added by the user in the settings."""
    servers: list[mcp.MCPServer] = []
    for cfg in settings["tools"].get("mcp_servers", []):
        if not cfg.get("enabled", True):
            continue
        try:
            if cfg.get("url"):
                server = ConfirmingHTTPServer(
                    url=cfg["url"], headers=cfg.get("headers"), allowed_tools=cfg.get("allowed_tools"),
                    client_session_timeout_seconds=15)
            elif cfg.get("command"):
                server = FilteredStdioServer(
                    command=str(Path(cfg["command"]).expanduser()), args=cfg.get("args", []), env=cfg.get("env"),
                    client_session_timeout_seconds=15, allowed_tools=cfg.get("allowed_tools"))
            else:
                continue
            server._confirm_setup(cfg)
            servers.append(server)
        except Exception as e:
            logger.warning("serveur MCP « %s » ignoré : %s", cfg.get("name", "?"), e)
    return servers


def is_background_filler(text: str, last_assistant: str = "") -> bool:
    """Just "Thank you", "Yeah"… (12 words at most): probably a voice or a video nearby. Unless the
    assistant just asked a question: "yes" answers it."""
    words = re.findall(r"[a-z'-]+", text.lower())
    if not words or len(words) > 12 or not all(w in FILLERS for w in words):
        return False
    return not (last_assistant or "").rstrip().endswith("?")


class Assistant(Agent):
    def __init__(self, settings: dict, turn_metrics: dict, command_mode: bool = False,
                 silent_prompt: str = COMMAND_PROMPT, typed: bool = False, product: str = "assistant",
                 gate: voice_id.Gate | None = None, gate_stream: bool = False) -> None:
        # command_mode: replies without voice (switchable live), with silent_prompt (command or chat).
        self.command_mode = command_mode
        self.silent_prompt = silent_prompt
        self.typed = typed
        # The room's product (command | assistant): "Listen only to my voice" is set product by product.
        self.product = product
        # Voice gatekeeper. Parakeet checks it before transcribing; a live transcription (Moonshine)
        # writes before knowing who is speaking: gate_stream then holds its text until the end of the sentence.
        self.gate = gate
        self.gate_stream = gate_stream
        self._heard: deque = deque()   # audio since the last sentence, to judge the voice
        self._heard_samples = 0
        super().__init__(instructions=self._compose_instructions(settings), tools=enabled_tools(settings),
                         mcp_servers=mcp_servers(settings))
        self._turn = turn_metrics

    def _compose_instructions(self, settings: dict) -> str:
        if self.command_mode:
            return compose_instructions({**settings, "llm": {**settings["llm"], "system_prompt": self.silent_prompt}})
        return compose_instructions(settings)

    async def stt_node(self, audio, model_settings):
        """Usual transcription. With "Listen only to my voice" and a live transcription, the text is only
        released at the end of the sentence, if the voice is the owner's; otherwise nothing is shown or kept."""
        async def tee():
            async for frame in audio:
                self._heard.append(frame)
                self._heard_samples += frame.samples_per_channel
                while self._heard and self._heard_samples > 15 * frame.sample_rate:
                    self._heard_samples -= self._heard.popleft().samples_per_channel
                yield frame

        async for event in Agent.default.stt_node(self, tee(), model_settings):
            if not (self.gate_stream and self.gate and isinstance(event, stt.SpeechEvent) and self.gate.active()):
                yield event
                continue
            if event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT:
                continue   # no live text: we don't know yet who is speaking
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                frames, self._heard, self._heard_samples = list(self._heard), deque(), 0
                if frames and not await asyncio.to_thread(self.gate.accepts, self._pcm16k(frames)):
                    continue
            yield event

    @staticmethod
    def _pcm16k(frames: list) -> np.ndarray:
        pcm = np.concatenate([np.frombuffer(f.data, dtype=np.int16).reshape(-1, f.num_channels).mean(axis=1)
                              for f in frames]).astype(np.float32) / 32768
        return voice_id.to_16k(pcm, frames[0].sample_rate)

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        if self.typed:  # typed text: no background noise to filter
            return
        text = new_message.text_content or ""
        last = next((i for i in reversed(turn_ctx.items) if getattr(i, "role", None) == "assistant"), None)
        # An action is waiting for a yes: "yes" alone is then an answer, not background noise.
        if policy.pending() is None and is_background_filler(text, last.text_content if last else ""):
            logger.info("ignoré (bruit de fond probable) : %s", r(text))
            raise StopResponse()

    async def _shortcut(self, asked: str):
        """Reply (str) if the request is handled by a shortcut, (name, action) if a named shortcut must first
        be done by the LLM, None otherwise."""
        name = learned.forget_request(asked)
        if name:
            phrase = learned.forget(name)
            return succeeded(f"Forgot “{phrase}”.") if phrase else failed(f"There is no shortcut called “{name}”.")
        named = learned.named(asked)
        if named:
            name, action = named
            current = await fast_path.front_app() if re.search(r"\b(it|this|that|window|fenêtre)\b", action.lower()) else ""
            actions = fast_path.parse(action, fast_path.resolve_strict, lambda: current)
            calls = learned.calls_from_actions(actions or [])
            if not calls:
                return named
            reply = await fast_path.run(actions)
            if reply.done and learned.learn(name, calls, named=True):
                logger.info("📚 raccourci « %s » : %s", r(name), r(calls))
                return reply.with_message(f"{reply} Learned: say “{name}” any time.")
            return reply
        entry = learned.find(asked)
        if entry:
            start = time.perf_counter()
            reply = await learned.replay(entry)
            self._turn["llm_ms"] = (time.perf_counter() - start) * 1000
            logger.info("📚 rejoué (%.0f ms) : %s -> %s", self._turn["llm_ms"], r(asked), r(reply))
            return reply
        return None

    def _learn_from_tools(self) -> None:
        """The tools of the LLM's previous reply, all successful and replayable: learned for next time."""
        learning = getattr(self, "_learning", None)
        if not learning or not learning["calls"]:
            return
        self._learning = None
        if not all(c.pop("ok", False) for c in learning["calls"]):
            return
        phrase = learning["name"] or learning["question"]
        if learned.learn(phrase, learning["calls"], named=bool(learning["name"])):
            logger.info("📚 appris : %s -> %s", r(phrase), r(learning["calls"]))
            if learning["name"]:
                asyncio.ensure_future(publish_action(f"Learned: say “{phrase}” any time."))

    async def on_enter(self) -> None:
        # Normally already cached (prewarmed when the process starts, see prewarm): ~0.4 s. Otherwise, the
        # LLM reads the prompt and tools now rather than at the first question.
        self._warming = asyncio.ensure_future(warm_prompt(self.instructions, self.tools))

    async def transcription_node(self, text, model_settings):
        # The displayed text does not show the tone [tags] written by the LLM.
        pending, said = "", ""
        async for delta in text:
            if not isinstance(delta, str):
                yield delta
                continue
            out, pending = strip_brackets(pending + delta)
            if out:
                said += out
                yield out
        if pending:
            said += pending
            yield pending
        logger.info("🤖 %s", r(said.strip()))

    async def llm_node(self, chat_ctx: ChatContext, tools, model_settings):
        settings = load_settings()
        cfg = settings["llm"]
        # System prompt changed from the interface: applied from this reply on.
        instructions = self._compose_instructions(settings)
        if instructions != self.instructions:
            old = self.instructions
            for item in chat_ctx.items:
                if getattr(item, "role", None) in ("system", "developer") and item.content:
                    item.content = [c.replace(old, instructions) if isinstance(c, str) else c
                                    for c in item.content]
            await self.update_instructions(instructions)
        # Memory window: the last N exchanges (the system prompt is always kept).
        chat_ctx = chat_ctx.copy()
        chat_ctx.truncate(max_items=max(1, int(cfg["memory_turns"])) * 2)
        # What is open on screen, as a system message just before the last question, for this reply
        # only. Not at the end of the system prompt: with Qwen, the tools come after it, and a line that
        # changes every turn forced rereading the whole prompt (12 s instead of 1 s per reply).
        # Not in the user's message either: small models recited it out loud.
        last_user = max((i for i, item in enumerate(chat_ctx.items) if getattr(item, "role", None) == "user"),
                        default=None)
        # First reply to an action request (not the sentence that follows a tool result). Computed
        # before inserting the note below, which shifts the question's position.
        # Guard on the first reply to a question (not on the sentence that follows a tool result).
        # Action request: the whole reply is held. Otherwise, only the first sentence: "Searching your
        # work memory…" without a tool (seen Oct 24 on "What did we decide about…?") is also a fake action.
        guard = last_user is not None and last_user == len(chat_ctx.items) - 1
        hold_all = guard and bool(ACTION_REQUEST.search(chat_ctx.items[last_user].text_content or ""))
        # After the tools of an LLM reply: what worked becomes a learned shortcut (learned.py).
        self._learn_from_tools()
        # Risky action pending (P0-6): only a yes from the user runs it, here, without going through the LLM.
        if guard and policy.pending() is not None:
            said = policy.answer(chat_ctx.items[last_user].text_content or "")
            if said is not None:
                reply = await confirm_pending(said)
                yield reply
                return
            policy.cancel("(autre demande)")
            await publish_action(failed("Not confirmed: nothing was done."))
        # Learned shortcuts ("when I say…", "forget…", request already solved): replayed without the LLM.
        if guard and settings["tools"].get("instant_commands", True):
            asked = chat_ctx.items[last_user].text_content or ""
            remembered = memory_rules.handle(asked)
            if remembered:
                await publish_action(remembered)
                yield remembered
                return
            shortcut = await self._shortcut(asked)
            if isinstance(shortcut, str):
                await publish_action(shortcut)
                yield shortcut
                return
            if isinstance(shortcut, tuple):   # named shortcut the LLM must first learn to do
                chat_ctx.items[last_user] = ChatMessage(role="user", content=[shortcut[1]])
                asked = shortcut[1]
            self._learning = {"question": asked, "name": shortcut[0] if isinstance(shortcut, tuple) else None,
                              "calls": []}
        # Simple commands (open, close, place, pause, search): run without the LLM, immediate reply.
        if guard and settings["tools"].get("instant_commands", True):
            asked = chat_ctx.items[last_user].text_content or ""
            current = await fast_path.front_app() if re.search(r"\b(it|this|that|window|fenêtre)\b", asked.lower()) else ""
            actions = fast_path.parse(asked, fast_path.resolve_strict, lambda: current)
            if actions:
                start = time.perf_counter()
                # Already done while the user was speaking (Early): not a second time.
                early = getattr(self, "early", None)
                todo, done = early.already(actions) if early else (actions, [])
                result = await fast_path.run(todo) if todo else succeeded("")
                reply = result.with_message(" ".join([*done, result]).strip())
                self._turn["llm_ms"] = (time.perf_counter() - start) * 1000
                logger.info("⚡ instantané (%.0f ms%s) : %s -> %s", self._turn["llm_ms"],
                            ", déjà fait en avance" if done else "", r(asked), r(reply))
                if todo:
                    await publish_action(reply)
                yield reply
                return
        # The exact time with each question: without it, the model makes one up ("14:32", seen twice).
        notes = [datetime.now().strftime("Current local time: %H:%M, %A %d %B %Y."), claude_status_line(),
                 memory_rules.note()]
        if settings["tools"].get("screen_context"):
            notes.append(await screen_context_line())
        notes = [n for n in notes if n]
        if notes and last_user is not None:
            chat_ctx.items.insert(last_user, ChatMessage(role=note_role(cfg["model"]), content=["\n".join(notes)]))
        # First question during the prewarm: mlx_lm.server looks up the cache when the request arrives, not
        # when it processes it. Sent at the same time, it reread the whole prompt right after the prewarm (Oct 31:
        # 31 s then another 16 s). Waiting for the prewarm makes it start from the cache (~0.3 s).
        warming = getattr(self, "_warming", None)
        if warming is not None and not warming.done():
            await asyncio.shield(warming)

        start = time.perf_counter()
        first = True
        # Model without tool calling (Gemma, Llama…): no tools are sent to it.
        if not model_supports_tools(cfg["model"]):
            tools = []
        if not getattr(self, "_tools_logged", False):
            self._tools_logged = True
            names = [getattr(getattr(t, "info", None), "name", "?") for t in tools]
            logger.info("outils envoyés au LLM (%d) : %s", len(names), ", ".join(names))
        held: list = []  # reply held until we know whether it claims an action without a tool
        called = False
        async with llm_for(cfg).chat(chat_ctx=chat_ctx, tools=tools, conn_options=LLM_CONN) as stream:
            async for chunk in stream:
                if first:
                    self._turn["llm_ms"] = (time.perf_counter() - start) * 1000
                    first = False
                if chunk.delta and chunk.delta.tool_calls:
                    called = True
                if not guard or called:
                    for c in held:
                        yield c
                    held.clear()
                    yield chunk
                    continue
                held.append(chunk)
                if not hold_all:
                    # First complete sentence (or 80 characters): if it claims no action, release it.
                    said = "".join(c.delta.content or "" for c in held if c.delta)
                    if re.search(r"[.!?…](\s|$)", said) or len(said) >= 80:
                        if SELF_CLAIM.search(said):
                            hold_all = True
                        else:
                            guard = False
                            for c in held:
                                yield c
                            held.clear()
        if not held:
            return
        said = "".join(c.delta.content or "" for c in held if c.delta)
        if not ACTION_CLAIM.search(said):
            for c in held:
                yield c
            return
        # It says it did it without launching anything: a second chance, with the instruction to call the tool.
        logger.warning("action annoncée sans outil : %s -> nouvel essai", r(said))
        retry = chat_ctx.copy()
        retry.add_message(role="assistant", content=said)
        retry.add_message(role=note_role(cfg["model"]), content=RETRY_NOTE)
        held, called = [], False
        async with llm_for(cfg).chat(chat_ctx=retry, tools=tools, conn_options=LLM_CONN) as stream:
            async for chunk in stream:
                if chunk.delta and chunk.delta.tool_calls:
                    called = True
                held.append(chunk)
        said = "".join(c.delta.content or "" for c in held if c.delta)
        # "None." (Qwen2.5 1.5B, Oct 31) is not an answer: same honest fallback as a fake claim.
        empty = re.fullmatch(r"\W*(?:none|null|n/?a)?\W*", said, re.I) is not None
        if called or not (ACTION_CLAIM.search(said) or empty):
            for c in held:
                yield c
            return
        logger.warning("toujours sans outil : %s -> réponse honnête", r(said))
        await publish_action(f"It was about to say “{said[:70]}” without doing anything: nothing was run.", ok=False)
        yield HONEST_FALLBACK


def action_payload(text: str, ok: bool | None = None) -> dict:
    """The Outcome's status if there is one; otherwise succeeded, or failed if ok=False (guard alerts).
    An action to confirm carries the proposal's id, sent back by the frame's Yes / No button."""
    if isinstance(text, Outcome):
        status = text.status
    else:
        status = outcome.Status.FAILED if ok is False else outcome.Status.SUCCEEDED
    payload = {"text": str(text), "ok": status is not outcome.Status.FAILED, "status": status.value}
    if status is Status.PROPOSED and isinstance(text, Outcome):
        payload["id"] = text.resource
    return payload


async def confirm_pending(said: str, proposal_id: str | None = None) -> str:
    """The user's yes / no (voice, text or click) on the pending action -> reply sentence."""
    if said == "no":
        if policy.cancel("par l'utilisateur") is None:
            return "There was nothing waiting."
        await publish_action(failed("Cancelled: nothing was done."))
        return "OK, cancelled."
    result = await policy.approve(proposal_id)
    if result is None:
        await publish_action(failed("Nothing to confirm any more (expired)."))
        return "That request expired: ask me again."
    await publish_action(result.with_message(str(result)[:120]))
    return policy.spoken(result)


async def publish_action(text: str, ok: bool | None = None) -> None:
    """Line shown live in the Mac app's frame (topic voice.actions), with the action's status."""
    try:
        from livekit.agents import get_job_context

        payload = action_payload(text, ok)
        room = get_job_context().room
        history.record(room.name, "action", payload["text"], payload["status"])
        await room.local_participant.publish_data(json.dumps(payload).encode(), topic=ACTIONS_TOPIC, reliable=True)
    except Exception as e:
        logger.debug("action non affichée : %s", e)


async def warm_prompt(instructions: str, tools: list, client: openai.LLM | None = None) -> None:
    """Has the LLM read a system prompt and its tools, so the following questions find them in the cache
    (~20 s cold for ~2,700 tokens on a 4B model, ~0.4 s afterwards)."""
    cfg = load_settings()["llm"]
    ctx = ChatContext()
    ctx.add_message(role="system", content=instructions)
    ctx.add_message(role="user", content="Hi")
    start = time.perf_counter()
    try:
        async with (client or llm_for(cfg)).chat(
                chat_ctx=ctx, tools=tools if model_supports_tools(cfg["model"]) else [], conn_options=LLM_CONN) as stream:
            async for _ in stream:
                break
        logger.info("prompt et outils en cache (%.1f s)", time.perf_counter() - start)
    except Exception as e:
        logger.warning("préchauffage du prompt impossible : %s", e)


def warm_all_modes() -> None:
    """Prewarms the prompts of the three modes (voice, voiceless, chat) before any conversation. In a separate
    thread: the process is ready right away, and a conversation that arrives meanwhile goes right after."""
    async def run() -> None:
        settings = load_settings()
        tools = enabled_tools(settings)
        # Separate client: the conversations' client must not be created in this thread's asyncio loop.
        client = make_llm(settings["llm"])
        for prompt in (None, COMMAND_PROMPT, CHAT_PROMPT):
            s = settings if prompt is None else {**settings, "llm": {**settings["llm"], "system_prompt": prompt}}
            await warm_prompt(compose_instructions(s), tools, client)
        await client.aclose()

    import threading

    threading.Thread(target=lambda: asyncio.run(run()), daemon=True, name="warm-prompts").start()


# Preloading (MLX voice, LLM prewarm) takes longer than the default 10 s.
def stt_config(cfg: dict) -> tuple:
    return (cfg["model"], bool(cfg["streaming"]), float(cfg["vad_window"]), float(cfg["update_interval"]),
            tuple(cfg.get("keyterms", [])))


def make_stt(cfg: dict) -> MoonshineSTT:
    size, streaming, vad_window, update_interval, keyterms = stt_config(cfg)
    return MoonshineSTT(size, streaming=streaming, vad_window=vad_window, update_interval=update_interval,
                        keyterms=keyterms)


server = AgentServer(num_idle_processes=1, initialize_process_timeout=180, job_memory_warn_mb=6000)


def prewarm(proc: JobProcess) -> None:
    s = load_settings()
    proc.userdata["vad"] = silero.VAD.load(
        activation_threshold=s["turn"]["vad_threshold"], min_silence_duration=s["turn"]["min_silence"])
    proc.userdata["stt"] = make_stt(s["stt"])
    warm_tts(s["tts"]["engine"])
    warm_llm(s["llm"]["model"])
    warm_all_modes()
    logger.info("modèles prêts : %s / moonshine %s / %s", s["llm"]["model"], s["stt"]["model"], s["tts"]["engine"])


server.setup_fnc = prewarm


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    s = load_settings()
    turn = s["turn"]
    chat_mode = ctx.room.name.startswith(CHAT_PREFIX)
    command_mode = chat_mode or ctx.room.name.startswith(COMMAND_PREFIX)
    logger.info("nouvelle conversation (%s)",
                "chat" if chat_mode else "commande, sans voix" if command_mode else "voix")
    vad = ctx.proc.userdata["vad"]
    # Transcription chosen per product: Parakeet (multilingual) by default for commands, Moonshine for voice.
    engine = s["stt"].get("command_engine" if command_mode else "engine", "moonshine")
    gate = voice_id.Gate("command" if command_mode else "assistant")
    if engine == "parakeet":
        stt = ParakeetSTT(gate=gate)
    else:
        stt = ctx.proc.userdata["stt"]
        if stt.config != stt_config(s["stt"]):
            stt = make_stt(s["stt"])
            ctx.proc.userdata["stt"] = stt

    local_tts = LocalTTS()
    expressive: bool | dict = False
    if not command_mode and s["emotion"]["mode"] == "auto" and supports_instruct(s["tts"]["engine"]):
        local_tts.expressive_enabled = True
        expressive = {
            "tts_instructions_template": s["emotion"]["instructions"],
            "speech_steering": {"nonverbal_sounds": bool(s["emotion"]["sounds"]), "disfluencies": False},
        }

    # The voice engine is always plugged in; in command mode, audio output is turned off (below) and
    # replies go out as text. It turns back on live without losing the conversation.
    session = AgentSession(
        stt=stt,
        vad=vad,
        llm=llm_for(s["llm"]),
        tts=local_tts,
        expressive=expressive,
        turn_handling={
            # No preemptive reply: with a single local LLM that handles one request at a time, a reply
            # started then dropped blocks the real one.
            "preemptive_generation": {"enabled": False},
            # The end-of-turn detector only knows English: with Parakeet (French possible), silence decides.
            "turn_detection": EnglishModel() if turn["turn_detector"] and engine != "parakeet" else "vad",
            "endpointing": {"min_delay": turn["min_endpointing_delay"], "max_delay": turn["max_endpointing_delay"]},
            "interruption": {
                "enabled": bool(turn["allow_interruptions"]),
                "mode": "vad",
                "min_duration": turn["min_interruption_duration"],
                "min_words": int(turn["min_interruption_words"]),
            },
        },
    )

    # Metrics of each turn sent to the interface, keyed by reply (speech_id): an interrupted
    # reply does not get mixed with the next one.
    turns: dict[str, dict] = {}
    turn_metrics: dict = {}  # current turn, filled in by Assistant.llm_node

    async def publish(payload: dict) -> None:
        try:
            await ctx.room.local_participant.publish_data(
                json.dumps(payload).encode(), topic=METRICS_TOPIC, reliable=True)
        except Exception as e:
            logger.warning("mesures non envoyées : %s", e)

    @session.on("metrics_collected")
    def _on_metrics(ev) -> None:
        m = ev.metrics
        if isinstance(m, metrics.EOUMetrics) and m.speech_id:
            turn_metrics.clear()
            turn_metrics.update(eou_ms=m.end_of_utterance_delay * 1000, stt_ms=m.transcription_delay * 1000)
            turns[m.speech_id] = turn_metrics
        elif (isinstance(m, metrics.TTSMetrics)
              or (not session.output.audio_enabled and isinstance(m, metrics.LLMMetrics))) \
                and m.speech_id in turns:
            turn = dict(turns.pop(m.speech_id))
            turn["tts_ms"] = m.ttfb * 1000 if isinstance(m, metrics.TTSMetrics) else 0.0
            turn["total_ms"] = turn["eou_ms"] + turn.get("llm_ms", 0) + turn["tts_ms"]
            current = load_settings()
            turn["engines"] = {"llm": current["llm"]["model"].split("/")[-1], "stt": stt.model,
                               "tts": current["tts"]["engine"]}
            logger.info("tour : %s", {k: round(v) for k, v in turn.items() if isinstance(v, float)})
            asyncio.ensure_future(publish(turn))

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        # History: what the user said or typed, and the replies (displayed text, without [tags]).
        role = getattr(ev.item, "role", None)
        if role in ("user", "assistant"):
            text = ev.item.text_content or ""
            if role == "assistant":
                text = re.sub(r"<expr\b[^>]*?/?>|</expr\s*>", "", BRACKET_RE.sub("", text))
            history.record(ctx.room.name, role, " ".join(text.split()))

    @session.on("function_tools_executed")
    def _on_tools(ev) -> None:
        learning = getattr(assistant, "_learning", None)
        for call, out in zip(ev.function_calls, ev.function_call_outputs):
            text = (out.output if out else "") or ""
            result = outcome.for_call(call.call_id, text, is_error=out is None or out.is_error)
            waiting = policy.pending()
            if waiting is not None and (result.status is Status.PROPOSED or text == waiting.message()):
                # The frame shows what is waiting for a yes, not the instruction meant for the LLM.
                result = Outcome(f"Confirm? {waiting.summary}", Status.PROPOSED, waiting.id)
                asyncio.ensure_future(publish_action(result))
            else:
                asyncio.ensure_future(publish_action(result.with_message(text[:120] or call.name)))
            if learning is not None:
                try:
                    args = json.loads(call.arguments or "{}")
                except ValueError:
                    args = {}
                learning["calls"].append({"name": call.name, "args": args, "ok": result.done})

    assistant = Assistant(s, turn_metrics, command_mode, CHAT_PROMPT if chat_mode else COMMAND_PROMPT, typed=chat_mode,
                          product=gate.product, gate=gate, gate_stream=engine != "parakeet")
    # Simple commands run while the user is still speaking (see fast_path.Early).
    assistant.early = fast_path.Early(publish=publish_action)

    @session.on("user_input_transcribed")
    def _on_transcript(ev) -> None:
        # No early execution when the voice must be checked first (it is only checked at the end of the sentence).
        if not ev.is_final and not chat_mode and load_settings()["tools"].get("instant_commands", True) \
                and not voice_id.settings_for(assistant.product)[0]:
            assistant.early.heard(ev.transcript)
    await session.start(agent=assistant, room=ctx.room)
    if command_mode:
        session.output.set_audio_enabled(False)
    await ctx.connect()

    @ctx.room.local_participant.register_rpc_method(VOICE_METHOD)
    async def _set_voice(data) -> str:
        on = data.payload.strip() == "on"
        # Cuts the sentence being played, but not a reply being prepared: the action runs to completion.
        if not on and session.agent_state == "speaking":
            session.interrupt()
        session.output.set_audio_enabled(on)
        assistant.command_mode = not on
        logger.info("réponses %s", "à voix haute" if on else "en texte seulement")
        return "on" if on else "off"

    @ctx.room.local_participant.register_rpc_method(CONFIRM_METHOD)
    async def _confirm(data) -> str:
        # Only the user (the room's non-agent participant) can confirm.
        caller = ctx.room.remote_participants.get(data.caller_identity)
        if caller is None or caller.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            return "refused"
        try:
            request = json.loads(data.payload)
        except ValueError:
            return "bad request"
        said = "yes" if request.get("answer") == "yes" else "no"
        reply = await confirm_pending(said, request.get("id"))
        session.say(reply, allow_interruptions=True)
        return said


if __name__ == "__main__":
    write_defaults()
    cli.run_app(server)
