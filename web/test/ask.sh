#!/bin/zsh
# Restarts the agent, waits until it is ready, plays a clip of questions and shows the turns.
cd "${0:A:h}/.."
pkill -f "agent.py dev"; sleep 3
(cd agent && uv run agent.py dev > ../logs/agent.log 2>&1 &)
for i in $(seq 1 120); do [ $(strings logs/agent.log | grep -c "process initialized") -ge 2 ] && break; sleep 2; done
# Keys of this install (P0-4, web/livekit.keys), created if needed.
read LK_KEY LK_SECRET <<< "$(cd agent && uv run python lk_keys.py)"
lk room join --url ws://127.0.0.1:7880 --api-key "$LK_KEY" --api-secret "$LK_SECRET" --identity tester \
  --publish "${1:-test/two.ogg}" --exit-after-publish "${2:-test}-$(date +%s)" > logs/lk_join.log 2>&1
sleep ${WAIT:-15}
python3 -I test/turns.py
