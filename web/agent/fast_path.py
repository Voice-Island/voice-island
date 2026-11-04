"""Instant commands, without the LLM: open, close, place windows, search the web, pause / next.

Idea taken from Atticus (MIT): simple requests skip the model. ~0.1 s instead of 1.5 to 3 s
with Qwen3 4B. Anything uncertain (new window, tab, folder, YouTube, "it" with no app in the
foreground…) goes to the LLM as before.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable
from pathlib import Path

from outcome import Outcome, combine, failed, succeeded

LEAD = re.compile(r"^(?:(?:hey|ok|okay|so|um|uh|please|can you|could you|would you|will you|go ahead and|just|"
                  r"i want you to|i'd like you to|now)\s+)+")
TAIL = re.compile(r"(?:,?\s+(?:please|for me|now|thanks|thank you))+$")

MEDIA = {
    "pause": ["pause", "pause it", "pause the music", "pause the song", "pause the video", "stop the music",
              "pause music"],
    "resume": ["resume", "resume it", "resume the music", "resume the song", "resume the video", "unpause",
               "resume music"],
    "next": ["next", "next song", "next track", "skip", "skip it", "skip this", "skip this song", "skip this one",
             "skip the song", "skip song", "next one"],
    "previous": ["previous", "previous song", "previous track", "go back to the previous song", "last song",
                 "previous one", "go back a song"],
}
MEDIA_PHRASES = {p: action for action, phrases in MEDIA.items() for p in phrases}

PLACE_WORDS = {"left": "left", "right": "right", "full": "full", "fullscreen": "full", "maximize": "full",
               "maximized": "full", "center": "center", "centre": "center", "middle": "center"}
SCREEN_WORDS = {"laptop": "laptop", "macbook": "laptop", "monitor": "monitor", "external": "monitor"}
# A request with these words asks for more than open or place: the LLM handles it (new window, tab…).
NOT_SIMPLE = re.compile(r"\b(new|another|window|windows|tab|tabs|folder|file|files|youtube|video|song|playlist|"
                        r"search|email|mail to|message|draft|claude|cloud code|project|terminal in|what|why|how|"
                        r"which|where|who|when|is|are|do|does|can)\b")
DEICTIC = {"it", "that", "this", "this window", "that window", "the window", "this app", "that app"}
VERBS = r"(?:snap|move|put|throw|send|place|drag|make)"

# French -> the same sentence in plain English, for the same rules (Parakeet transcribes French as is).
# Order matters: long phrases ("nouvelle fenêtre", "sur YouTube") come before single words.
FRENCH = [
    (r"\b(?:vas-y|vas y|allez|est-ce que tu peux|est-ce que tu pourrais|tu peux|peux-tu|tu pourrais|"
     r"s'il te pla[iî]t|s'il vous pla[iî]t|stp|svp|merci)\b", " "),
    (r"\b(?:joue|mets|lance) (.+?) sur youtube\b", r"play \1 on youtube"),
    (r"\bcherche (.+?) sur youtube\b", r"search for \1 on youtube"),
    (r"\b(?:mets|mettre) (?:en )?pause\b|\bfais pause\b", "pause"),
    (r"\b(?:reprends|relance) (?:la musique|la lecture)\b|\breprends\b", "resume"),
    (r"\b(?:(?:la )?(?:chanson|musique|piste|vidéo) suivante|suivant(?:e)?)\b", "next"),
    (r"\b(?:(?:la )?(?:chanson|musique|piste|vidéo) précédente|précédent(?:e)?)\b", "previous"),
    (r"\b(?:une |la )?(?:nouvelle|autre) fenêtre\b|\bune fenêtre\b", "a new window"),
    (r"\b(?:cette|la) fenêtre\b(?! d)", "it"),
    (r"\bla fenêtre (?:de |d')", ""),
    (r"\b(?:ouvre|ouvrir|ouvre-moi|lance|lancer|démarre|affiche)\b", "open"),
    (r"\b(?:ferme|fermer|quitte|quitter)\b", "quit"),
    (r"\b(?:mets|mettre|met|place|placer|déplace|déplacer|bouge|envoie|colle)(?:-la|-le)?\b", "put"),
    (r"\bcherche(?: sur google| sur internet| sur le web)?\b", "search"),
    (r"\b(?:sur la |à |a )gauche\b", " on the left"),
    (r"\b(?:sur la |à |a )droite\b", " on the right"),
    (r"\b(?:en )?plein écran\b", " fullscreen"),
    (r"\bau (?:centre|milieu)\b", " center"),
    (r"\bsur (?:le grand écran|l'écran externe|l'autre écran|le moniteur|l'écran)\b", " on the monitor"),
    (r"\bsur (?:l'écran du mac|le portable|le macbook)\b", " on the laptop"),
    (r"\bl'app(?:lication)? ", ""),
    (r"\b(?:et puis|puis|et)\b", "and"),
]


FR_ARTICLES = r"le|la|les|mon|ma|mes|du"


def french(t: str) -> str:
    for pattern, repl in FRENCH:
        t = re.sub(pattern, repl, t)
    return t


def plain(text: str) -> str:
    t = french(text.lower().replace("’", "'"))
    t = re.sub(r"[^\w\s',-]", " ", t)   # the comma stays: it separates "Chrome left, Terminal right"
    t = re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", t)).strip(" ,")
    t = LEAD.sub("", t)
    return TAIL.sub("", t).strip(" ,")


def _where(words: str) -> tuple[str | None, str | None, str]:
    """"on the left of my monitor" -> (left, monitor, rest). full screen -> full."""
    t = re.sub(r"\bfull ?screen\b", "fullscreen", words)
    t = re.sub(r"\b(?:big|bigger|large|external|second) (?:screen|monitor|display)\b", "monitor", t)
    t = re.sub(r"\b(?:laptop|macbook|built in|small) (?:screen|display)\b", "laptop", t)
    places = [PLACE_WORDS[w] for w in t.split() if w in PLACE_WORDS]
    screens = [SCREEN_WORDS[w] for w in t.split() if w in SCREEN_WORDS]
    if len(places) > 1 or len(screens) > 1:
        return None, None, t
    t = re.sub(r"\bl'(?=\w)", "", t)
    rest = re.sub(r"\b(?:%s|%s|%s|to|on|onto|in|at|the|my|of|side|half|screen|display|it's|its|%s)\b" % (
        VERBS, "|".join(PLACE_WORDS), "|".join(SCREEN_WORDS), FR_ARTICLES), " ", t)
    return (places[0] if places else None), (screens[0] if screens else None), re.sub(r"\s+", " ", rest).strip()


def parse_one(t: str, resolve: Callable[[str], Path | None], front: Callable[[], str]) -> dict | None:
    """An already cleaned request (plain) -> one action, or None."""
    if t in MEDIA_PHRASES:
        return {"kind": "media", "action": MEDIA_PHRASES[t]}
    m = (re.fullmatch(r"(?:play|put on) (.+?) on youtube", t)
         or re.fullmatch(r"(?:search|look up)(?: on)? youtube for (.+)|(?:search|look up)(?: for)? (.+?) on youtube", t))
    if m:   # "Play Booba on YouTube", "search YouTube for lofi": the usual YouTube tools, without the LLM
        query = next(g for g in m.groups() if g).strip()
        if query in DEICTIC or re.fullmatch(r"(?:something|anything|any song|a song|music|some music)", query) \
                or re.search(r"\b(?:for|the|a|an|and|to|of|some|on)$", query):
            return None
        return {"kind": "youtube_play" if t.startswith(("play", "put on")) else "youtube_search", "query": query}
    m = re.fullmatch(r"(?:search(?: the web| google)?(?: for)?|google(?! chrome)|look up) (.+)", t)
    if m:
        query = m[1].strip()
        if "youtube" in query or re.search(r"\b(?:for|the|a|an|and|to|of|some|on)$", query):
            return None
        return {"kind": "search", "query": query}
    m = re.fullmatch(r"(?:close|quit|exit) (?:the |%s |l')?(?:app )?(.+?)(?: app)?" % FR_ARTICLES.replace("|", " |"), t)
    if m:
        if m[1] in DEICTIC or NOT_SIMPLE.search(m[1]):
            return None
        app = resolve_open(m[1]) or resolve(m[1])
        return {"kind": "close", "app": app} if app else None
    # "Open a new window on the left", "open a new Chrome window on the right", "another window of Notes":
    # new window of the named app (otherwise the foreground one), then placed if a side is given.
    m = re.fullmatch(r"(?:open(?: up)?|make|create|give me) (?:(?:a |an )?(?:new|another)|a) (?:(.+?) )?window"
                     r"(?: (?:of|for|in) (.+?))?(?: (.+))?", t)
    if m:
        place, screen, rest = _where(m[3] or "")
        name = (m[1] or m[2] or "").strip()
        if rest and not name:   # French: "une nouvelle fenêtre Chrome à droite"
            name, rest = rest, ""
        if rest or (name and NOT_SIMPLE.search(name)):
            return None
        current = "" if name else front()
        app = resolve(name or current) if (name or current) else None
        if not app:
            return None
        return {"kind": "new_window", "app": app, "place": place, "screen": screen or "current"}
    m = re.fullmatch(r"(?:open(?: up)?|launch|start|pull up|bring up|switch to|go to|show me|show) (.+)", t)
    if m:
        place, screen, name = _where(m[1])
        name = re.sub(r"^(?:the |my |le |la |les |mon |ma |l')?(?:app )?", "", name).removesuffix(" app").strip()
        site = site_for(name)
        if site and not place and not screen:   # "open YouTube", "open github.com": the site, without the LLM
            return {"kind": "site", "url": site}
        if not name or NOT_SIMPLE.search(name) or "." in name:
            return None
        app = resolve(name)
        if not app:
            return None
        return {"kind": "open", "app": app, "place": place, "screen": screen or "current"}
    place, screen, name = _where(t)
    if not (place or screen) or not name:
        return None
    if NOT_SIMPLE.search(name):
        # "Put the email on the left": placing an already open app (Mail) is safe; otherwise the LLM decides.
        app = resolve_open(name)
        return {"kind": "place", "app": app, "place": place or "full", "screen": screen or "current"} if app else None
    app = None
    if name in DEICTIC:
        current = front()
        app = resolve(current) if current else None
    else:
        app = resolve(name) or resolve_open(name)
        # "Put the VS Code <project> on the left": a project name or a mistranscribed word after the app.
        # Placing a window is undone in one gesture: keep the app found at the start (at most 2 extra words).
        words = name.split()
        for cut in range(len(words) - 1, max(len(words) - 3, 0), -1):
            if app:
                break
            app = resolve(" ".join(words[:cut]))
    if not app:
        return None
    return {"kind": "place", "app": app, "place": place or "full", "screen": screen or "current"}


def parse(text: str, resolve: Callable[[str], Path | None], front: Callable[[], str] = lambda: "") -> list[dict] | None:
    """"Chrome on the left and Terminal on the right" -> two actions. None as soon as one part is uncertain:
    then the LLM does everything."""
    t = plain(text)
    if not t or len(t.split()) > 16:
        return None
    one = parse_one(t, resolve, front) if "," not in t else None
    if one:
        return [one]
    parts = [p.strip() for p in re.split(r"\s*(?:,|\band then\b|\bthen\b|\band\b)\s*", t) if p.strip()]
    if len(parts) < 2:
        return None
    actions: list[dict] = []
    for part in parts:
        action = parse_one(part, resolve, front)
        if action is None and actions and actions[-1]["kind"] == "open":   # "open Chrome and Notes"
            action = parse_one("open " + part, resolve, front)
        if action is None:
            return None
        actions.append(action)
    return actions


async def run(actions: list[dict]) -> Outcome:
    """Runs the actions with the usual tools. -> a short reply, with the status of the least certain one."""
    import tools

    replies: list[Outcome] = []
    for a in actions:
        kind = a["kind"]
        if kind == "media":
            reply = await tools.media_control(None, action=a["action"])
        elif kind == "youtube_play":
            reply = await tools.play_youtube_video(None, query=a["query"])
        elif kind == "youtube_search":
            reply = await tools.search_youtube(None, query=a["query"])
        elif kind == "search":
            reply = await tools.search_web(None, query=a["query"])
        elif kind == "close":
            reply = await tools.quit_app(None, name=a["app"].stem)
        elif kind == "site":
            reply = await tools._open_url(a["url"])
        elif kind == "new_window":
            reply = await tools._new_window(a["app"], None)
            if a.get("place") and not reply.failed:
                await asyncio.sleep(0.8)   # the new window must be in the foreground before it is placed
                placed = await tools.place_app(a["app"], a["place"], a["screen"])
                reply = placed.with_message(placed.replace(" is ", " (new window) is ", 1))
        elif kind == "open" and a.get("place"):
            reply = await tools.place_app(a["app"], a["place"], a["screen"])
        elif kind == "open":   # without counting windows before / after like open_app: ~0.1 s
            code, out = await tools.run("open", "-a", str(a["app"]))
            stem = a["app"].stem
            reply = succeeded(f"Opened {stem}.", stem) if code == 0 else failed(f"Could not open {stem}: {out}", stem)
        else:
            reply = await tools.place_app(a["app"], a["place"], a["screen"])
        replies.append(reply)
    return combine(replies)


def site_for(name: str) -> str | None:
    """Site requested by its name (tools.SITES, if no app has that name) or by its domain."""
    import tools

    name = name.strip().removeprefix("the ")
    if re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|ch|fr|io|ai|dev|app|so|co|de|me)(?:/\S*)?", name):
        return "https://" + name
    if name in tools.SITES and resolve_strict(name) is None:
        return "https://" + tools.SITES[name]
    return None


def resolve_open(name: str) -> Path | None:
    """Already open app, approximate name accepted ("whisper flow" -> Wispr Flow): close, place."""
    import tools

    return tools.resolve_running(name)


def resolve_strict(name: str) -> Path | None:
    """Like tools.resolve_app, without approximate matching: "open the door" must open no app."""
    import tools

    apps = tools.installed_apps()
    wanted = tools.ALIASES.get(name.strip(), name.strip()).lower()
    if wanted in apps:
        return apps[wanted]
    starts = [n for n in apps if n.startswith(wanted)]
    if len(wanted) >= 4 and len(starts) == 1:
        return apps[starts[0]]
    return None


async def front_app() -> str:
    import tools

    front, _ = await asyncio.to_thread(tools.screen_snapshot)
    return front


def signature(action: dict) -> tuple:
    return (action["kind"], str(action.get("app", "")), action.get("place"), action.get("screen"),
            action.get("action"), action.get("query"))


class Early:
    """Early execution on the live transcript: "Chrome on the left" runs as soon as the text stays
    stable for 250 ms, without waiting for the end of turn (detector ~0.4-0.9 s). Only open and place: redoing these
    actions has no effect, whereas close or pause / play (which toggles) must never run twice.
    At the end of the sentence, llm_node does not redo what is already done (already)."""

    SAFE = {"open", "place"}
    STABLE = 0.25
    KEEP = 8.0

    def __init__(self, resolve: Callable[[str], Path | None] = resolve_strict, front=None, publish=None) -> None:
        self.resolve = resolve
        self.front = front or front_app
        self.publish = publish
        self.done: dict[tuple, tuple[float, str]] = {}
        self._task: asyncio.Task | None = None

    def heard(self, partial: str) -> None:
        """New partial transcript: restarts the stability delay."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.ensure_future(self._settle(partial))

    async def _settle(self, partial: str) -> None:
        await asyncio.sleep(self.STABLE)
        t = plain(partial)
        if re.search(r"\b(?:and|then|to|on|the|my|a|in|at)$", t):   # more is coming
            return
        current = await self.front() if re.search(r"\b(it|this|that)\b", t) else ""
        actions = parse(partial, self.resolve, lambda: current)
        if not actions or any(a["kind"] not in self.SAFE for a in actions):
            return
        actions = [a for a in actions if not self._recent(a)]
        if not actions:
            return
        reply = await run(actions)
        now = time.monotonic()
        for a in actions:
            self.done[signature(a)] = (now, reply)
        if self.publish:
            await self.publish(reply)

    def _recent(self, action: dict) -> bool:
        hit = self.done.get(signature(action))
        return bool(hit) and time.monotonic() - hit[0] < self.KEEP

    def already(self, actions: list[dict]) -> tuple[list[dict], list[str]]:
        """End of sentence: (actions still to do, replies of those already done early)."""
        todo, replies = [], []
        for a in actions:
            if self._recent(a):
                reply = self.done[signature(a)][1]
                if reply not in replies:
                    replies.append(reply)
            else:
                todo.append(a)
        return todo, replies
