#!/bin/zsh
# Starts the local voice stack: LiveKit, LLM (mlx_lm), voice (tts_server), agent.
#   ./dev.sh          starts for the Voice Island Mac app (logs in logs/)
#   ./dev.sh web      also starts the web interface (Next.js, ~4 GB of memory, http://localhost:3010)
#   ./dev.sh stop     stops everything
set -u
cd "${0:A:h}"
mkdir -p logs

LLM_MODEL=$(cd agent && uv run python -c "from settings import load_settings, resolve_model; print(resolve_model(load_settings()['llm']['model']))")
# Vision model ("Look at the screen" tool): empty if the tool is disabled, and then no vision server.
VISION_MODEL=$(cd agent && uv run python -c "
from settings import load_settings
s = load_settings()
print(s['vision']['model'] if 'look_at_screen' in s['tools']['enabled'] else '')")
# Mac memory (GB): sets the LLM cache and vision. Measured Nov 4: the stack took ~20 GB out of 24, it must
# fit in 16 GB ("Your Mac" page of the dashboard, MachineCheck.swift).
RAM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
if [[ -n "$VISION_MODEL" && $RAM_GB -lt 24 ]]; then
  echo "Vision désactivée : $RAM_GB Go de mémoire (24 Go conseillés pour regarder l'écran)."
  VISION_MODEL=""
fi
# LLM prompt cache: a quarter of the memory, 6 GB at most (24 GB -> 6, 16 GB -> 4, 8 GB -> 2).
LLM_CACHE_BYTES=$(( RAM_GB / 4 < 6 ? RAM_GB / 4 * 1000000000 : 6000000000 ))
# LiveKit only listens on this Mac, and the assistant controls the Mac.
# LIVEKIT_BIND=0.0.0.0 ./dev.sh to open it to the local network, only on a trusted network.
LIVEKIT_BIND=${LIVEKIT_BIND:-127.0.0.1}
# Local network mode is not supported (no pairing).
# The keys are specific to this install (web/livekit.keys), but nothing else protects the rooms: refused
# without explicit confirmation.
if [[ "$LIVEKIT_BIND" != 127.0.0.1 && "${LIVEKIT_ALLOW_INSECURE_LAN:-}" != 1 ]]; then
  echo "LIVEKIT_BIND=$LIVEKIT_BIND refusé : le mode réseau local n'est pas pris en charge." >&2
  echo "Sur un réseau de confiance seulement : LIVEKIT_ALLOW_INSECURE_LAN=1 LIVEKIT_BIND=$LIVEKIT_BIND ./dev.sh" >&2
  exit 1
fi
# Web interface: this Mac only. Open to the network, it let any device on the Wi-Fi write the settings
# (MCP servers = commands run on the Mac) and sign tokens (Oct 31). Its /api routes reject requests that
# don't come from this Mac anyway (lib/local-only.ts).
WEB_BIND=${WEB_BIND:-127.0.0.1}

# Offline as soon as the chosen models are on disk: otherwise the LLM and the voice ask Hugging Face at every
# start whether a newer version exists. HF_HUB_OFFLINE=0 ./dev.sh forces the check.
if [[ -z "${HF_HUB_OFFLINE:-}" ]] && (cd agent && uv run python -c "
from huggingface_hub import try_to_load_from_cache
from settings import load_settings
from models_mlx import ENGINES
s = load_settings()
repos = [ENGINES[s['tts']['engine']]] + ([] if s['llm']['model'].startswith('local:') else [s['llm']['model']])
if s.get('dictation', {}).get('engine') == 'parakeet': repos.append(s['dictation']['model'])
if 'look_at_screen' in s['tools']['enabled']: repos.append(s['vision']['model'])
assert all(isinstance(try_to_load_from_cache(r, 'config.json'), str) for r in repos)
" > /dev/null 2>&1); then
  export HF_HUB_OFFLINE=1
  echo "Modèles sur le disque : mode hors ligne."
fi

stop_all() {
  pkill -f "livekit-server --dev" 2>/dev/null
  pkill -f "mlx_lm.server" 2>/dev/null
  pkill -f "mlx_vlm.server" 2>/dev/null
  pkill -f "tts_server.py" 2>/dev/null
  pkill -f "agent.py dev" 2>/dev/null
  pkill -f "next dev --turbopack -p 3010" 2>/dev/null
}

if [[ "${1:-}" == "stop" ]]; then
  stop_all
  echo "Arrêté."
  exit 0
fi

stop_all
sleep 2

# Keeps the 3 previous logs of each service (*.log.1 to .3), no more: they contain the conversations.
# A single one (.1) was not enough: two restarts close together erased the previous day's logs (Nov 3).
for f in logs/*.log(N); do
  rm -f "$f.3"
  [[ -f "$f.2" ]] && mv -f "$f.2" "$f.3"
  [[ -f "$f.1" ]] && mv -f "$f.1" "$f.2"
  mv -f "$f" "$f.1"
done

echo "LiveKit (ws://$LIVEKIT_BIND:7880)…"
# Keys specific to this install (P0-4, web/agent/lk_keys.py), randomly generated the first time: livekit-server
# then rejects devkey / secret. The agent and the web interface get them from the environment, the Mac app reads
# the file.
read LK_KEY LK_SECRET <<< "$(cd agent && uv run python lk_keys.py)"
export LIVEKIT_API_KEY="$LK_KEY" LIVEKIT_API_SECRET="$LK_SECRET"
# Locally, audio (WebRTC) only goes through addresses internal to the Mac: 127.0.0.1, and 10.254.77.1 set by
# scripts/local-address.sh. WebRTC clients ignore 127.0.0.1: without this 2nd address, turning off Wi-Fi gave
# "connection failed timeout" (Oct 24).
LIVEKIT_RTC=()
if [[ "$LIVEKIT_BIND" == 127.0.0.1 ]]; then
  LIVEKIT_RTC=(--node-ip 127.0.0.1 --config-body
    "rtc: {enable_loopback_candidate: true, ips: {includes: [127.0.0.1/32, 10.254.77.1/32]}}")
fi
livekit-server --dev --key-file livekit.keys --bind "$LIVEKIT_BIND" "${LIVEKIT_RTC[@]}" > logs/livekit.log 2>&1 &

echo "LLM $LLM_MODEL (http://127.0.0.1:8080)…"
# Capped prompt cache: without a limit, mlx_lm.server climbed to 17 GB in a day and made the Mac swap
# (answers in 60 s instead of 1 s). The byte cap (6 GB: a Qwen3 4B prompt weighs ~0.5 GB, 4 GB only kept
# 7 of them and the command mode was evicted, Oct 31) protects memory; the number of entries must stay
# large: at 12, the cache was always full (each start prewarms 3 modes x 2 processes) and the voice
# prompt was evicted right before the question, reread twice in 12 s (Oct 31, only 2.2 GB used).
# One request at a time: in parallel, each request rereads the whole prompt (~2,700 tokens) without using the cache,
# and the first answer of a conversation came after more than a minute (Oct 24). In series, the next one
# finds it in the cache.
(cd agent && uv run mlx_lm.server --model "$LLM_MODEL" --host 127.0.0.1 --port 8080 \
  --prompt-cache-size 32 --prompt-cache-bytes $LLM_CACHE_BYTES \
  --prompt-concurrency 1 --decode-concurrency 1 > ../logs/llm.log 2>&1 &)

if [[ -n "$VISION_MODEL" ]]; then
  echo "Vision $VISION_MODEL (http://127.0.0.1:8081)…"
  # Separate server (web/vision, mlx-vlm): the same Qwen3.5 2B scores 16/20 on tools there versus 18/20 on
  # mlx_lm.server (Nov 2), so it is only used to look at the screen, on demand. Prefix cache in memory
  # only (APC_DISK_ENABLED=0): what it sees of the screen is never written to disk.
  (cd vision && APC_ENABLED=1 APC_DISK_ENABLED=0 uv run python -m mlx_vlm.server --model "$VISION_MODEL" \
    --host 127.0.0.1 --port 8081 --max-tokens 512 --log-progress-interval 0 > ../logs/vision.log 2>&1 &)
fi

echo "Serveur de voix (http://127.0.0.1:8090)…"
(cd agent && uv run tts_server.py > ../logs/tts.log 2>&1 &)
until curl -s -m 2 http://127.0.0.1:8090/health > /dev/null 2>&1; do sleep 2; done

echo "Agent…"
# LiveKit turn detector disabled (the default): no preloading of its bundled model
# (~244 MB, LiveKit Model License). If enabled later, it loads at the next conversation.
TURN_DETECTOR=$(cd agent && uv run python -c "from settings import load_settings; print(int(bool(load_settings()['turn']['turn_detector'])))")
[[ "$TURN_DETECTOR" == 0 ]] && export LIVEKIT_AGENTS_PRELOAD_LOCAL_INFERENCE=0
(cd agent && uv run agent.py dev > ../logs/agent.log 2>&1 &)

if [[ "${1:-}" == "web" ]]; then
  echo "Interface web (http://localhost:3010)…"
  (cd frontend && source ~/.nvm/nvm.sh > /dev/null && nvm use 22 > /dev/null \
    && npx -y pnpm@10 exec next dev --turbopack -p 3010 -H "$WEB_BIND" > ../logs/frontend.log 2>&1 &)
fi

until [ "$(strings logs/agent.log 2>/dev/null | grep -c 'process initialized')" -ge 2 ]; do sleep 2; done
if [[ "${1:-}" == "web" ]]; then echo "Prêt : ouvre http://localhost:3010"; else echo "Prêt : ⌥ Espace pour parler."; fi
