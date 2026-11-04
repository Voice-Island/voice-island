#!/bin/zsh
# Builds the audio clips for the end-to-end tests with the macOS synthetic voice (say).
#   ./make_clips.sh     -> q_*.wav (live_switch_check.py) and two.ogg (ask.sh, requires ffmpeg)
set -eu
cd "${0:A:h}"
tmp=$(mktemp -d)
for pair in "What time is it?:q_time" "What day is it today?:q_day" "Play the first video.:q_play" \
            "Pause the video.:q_pause" "Thank you.:q_thanks"; do
  say -v Samantha -o "$tmp/${pair#*:}.aiff" "${pair%%:*}"
  afconvert -f WAVE -d LEI16@48000 -c 1 "$tmp/${pair#*:}.aiff" "${pair#*:}.wav"
done
if command -v ffmpeg > /dev/null; then
  say -v Samantha -o "$tmp/two.aiff" "Can you give me a quick dinner idea for tonight? [[slnc 9000]] Explain in two sentences what a neural network is."
  ffmpeg -loglevel error -y -i "$tmp/two.aiff" -ac 1 -ar 48000 -c:a libopus two.ogg
fi
rm -rf "$tmp"
ls *.wav *.ogg 2>/dev/null
