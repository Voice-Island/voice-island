"""Chat mode: "chat_" room, written messages (topic lk.chat), text replies, tools via a fake Voice Island.

  cd web/agent && uv run python ../test/chat_check.py ["message 1" "message 2" ...]
"""

import asyncio
import json
import sys
from pathlib import Path
import time

from livekit import api, rtc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
from lk_keys import ensure  # noqa: E402  keys of this install (P0-4)

URL, (KEY, SIGNING) = "ws://127.0.0.1:7880", ensure()


async def main() -> None:
    room_name = f"chat_check_{int(time.time())}"
    token = (api.AccessToken(KEY, SIGNING).with_identity("checker").with_name("checker")
             .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt())
    room = rtc.Room()
    texts: list[str] = []
    mac: list[dict] = []
    audio = {"tracks": 0}

    def _on_text(reader, participant_identity) -> None:
        async def read() -> None:
            text = await reader.read_all()
            if participant_identity != "checker" and text.strip():
                texts.append(text.strip())
        asyncio.ensure_future(read())

    room.register_text_stream_handler("lk.transcription", _on_text)
    room.on("track_subscribed", lambda *a: audio.__setitem__("tracks", audio["tracks"] + 1))
    await room.connect(URL, token)

    @room.local_participant.register_rpc_method("mac.keys")
    async def _keys(data: rtc.RpcInvocationData) -> str:
        mac.append(json.loads(data.payload))
        return json.dumps({"ok": True})

    @room.local_participant.register_rpc_method("mac.windows")
    async def _windows(data: rtc.RpcInvocationData) -> str:
        return json.dumps({"front": "Code", "windows": {"Code": 2, "Terminal": 1}})

    for _ in range(60):
        if any(p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT for p in room.remote_participants.values()):
            break
        await asyncio.sleep(0.5)
    await asyncio.sleep(2)
    messages = [(m, 30) for m in sys.argv[1:]] or [("What time is it?", 20), ("Pause the music please.", 20),
                                                   ("merci", 10)]
    for message, wait in messages:
        before = len(texts)
        await room.local_participant.send_text(message, topic="lk.chat")
        await asyncio.sleep(wait)
        print(f"ÉCRIT {message!r:32} -> {texts[before:]}", flush=True)
    print(f"appels au Mac : {mac} ; pistes audio reçues de l'agent : {audio['tracks']}")
    await room.disconnect()


asyncio.run(main())
