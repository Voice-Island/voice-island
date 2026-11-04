"""End to end: "command" room (no voice), then voice turned back on live via RPC, then simulated Chrome bridge.

Acts like the Mac app: publishes the mic (q_*.wav clips), answers "mac.chrome" with a fake YouTube tab,
and counts the audio received from the agent. Opens nothing in Chrome: the fake bridge just records the URL.
  cd web/agent && uv run python ../test/live_switch_check.py
"""

import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
from livekit import api, rtc

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
from lk_keys import ensure  # noqa: E402  keys of this install (P0-4)

URL, (KEY, SECRET) = "ws://127.0.0.1:7880", ensure()
FAKE_TAB = {"running": True, "front": True, "title": "lofi hip hop - YouTube",
            "url": "https://www.youtube.com/results?search_query=lofi+hip+hop"}


async def main() -> None:
    room_name = f"command_check_{int(time.time())}"
    token = (api.AccessToken(KEY, SECRET).with_identity("checker").with_name("checker")
             .with_grants(api.VideoGrants(room_join=True, room=room_name)).to_jwt())
    room = rtc.Room()
    agent_energy = {"frames": 0, "loud": 0}
    texts: list[str] = []
    chrome_calls: list[dict] = []

    @room.on("track_subscribed")
    def _on_track(track, pub, participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            async def read() -> None:
                async for ev in rtc.AudioStream(track):
                    samples = np.frombuffer(ev.frame.data, dtype=np.int16)
                    agent_energy["frames"] += 1
                    agent_energy["loud"] += int(np.abs(samples).mean() > 200)
            asyncio.ensure_future(read())

    def _on_text(reader, participant_identity) -> None:
        async def read() -> None:
            text = await reader.read_all()
            if participant_identity != "checker" and text.strip():
                texts.append(text.strip())
        asyncio.ensure_future(read())

    room.register_text_stream_handler("lk.transcription", _on_text)
    await room.connect(URL, token)

    @room.local_participant.register_rpc_method("mac.chrome")
    async def _chrome(data: rtc.RpcInvocationData) -> str:
        req = json.loads(data.payload)
        chrome_calls.append(req)
        if req["action"] == "count":
            return json.dumps({"running": True, "windows": 2, "front_window_tabs": 7})
        return json.dumps(FAKE_TAB if req["action"] == "active_tab" else {"ok": True})

    # Fake Voice Island: no key is sent, no window is actually counted.
    @room.local_participant.register_rpc_method("mac.keys")
    async def _keys(data: rtc.RpcInvocationData) -> str:
        chrome_calls.append({"keys": json.loads(data.payload)})
        return json.dumps({"ok": True})

    @room.local_participant.register_rpc_method("mac.windows")
    async def _windows(data: rtc.RpcInvocationData) -> str:
        return json.dumps({"front": "Google Chrome", "windows": {"Google Chrome": 2, "Terminal": 1, "Code": 3}})

    actions: list[str] = []

    @room.on("data_received")
    def _on_data(packet) -> None:
        if packet.topic == "voice.actions":
            actions.append(json.loads(packet.data)["text"])

    source = rtc.AudioSource(48000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    async def speak(name: str) -> None:
        with wave.open(str(HERE / f"{name}.wav")) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        pcm = np.concatenate([pcm, np.zeros(48000 * 2, dtype=np.int16)])  # 2 s of silence: end of turn
        for i in range(0, len(pcm) - 480, 480):
            await source.capture_frame(rtc.AudioFrame(pcm[i:i + 480].tobytes(), 48000, 1, 480))

    async def turn(label: str, clip: str, wait: float) -> None:
        before_loud, before_texts = agent_energy["loud"], len(texts)
        await speak(clip)
        await asyncio.sleep(wait)
        print(f"{label}: réponse texte={texts[before_texts:]!r} audio de l'agent (trames fortes)="
              f"{agent_energy['loud'] - before_loud}", flush=True)

    for _ in range(60):  # the agent arrives
        if any(p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT for p in room.remote_participants.values()):
            break
        await asyncio.sleep(0.5)
    agent = next(p for p in room.remote_participants.values() if p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)
    await asyncio.sleep(2)

    await turn("1. sans voix", "q_time", 30)
    reply = await room.local_participant.perform_rpc(destination_identity=agent.identity,
                                                     method="agent.set_voice", payload="on")
    print(f"   RPC voix -> {reply}")
    await turn("2. voix rallumée", "q_day", 20)
    reply = await room.local_participant.perform_rpc(destination_identity=agent.identity,
                                                     method="agent.set_voice", payload="off")
    print(f"   RPC voix -> {reply}")
    await turn("3. sans voix + pont Chrome", "q_play", 18)
    await turn("4. pause", "q_pause", 15)
    await turn("5. « Thank you » seul (bruit)", "q_thanks", 10)
    print(f"   appels au Mac : {[c for c in chrome_calls if c.get('action') != 'active_tab']}")
    print(f"   lignes d'action affichées : {actions}")
    await room.disconnect()


asyncio.run(main())
