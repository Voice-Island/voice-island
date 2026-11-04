#!/bin/zsh
# One-command install from source: checks the Mac, installs the tools, builds the app, starts everything.
#   ./install.sh          install, or update after a `git pull` (safe to run again)
# Needs: a Mac with Apple Silicon, macOS 14+, Xcode (App Store) and Homebrew (https://brew.sh).
set -eu
cd "${0:A:h}"
ROOT=$PWD
LOG="$ROOT/web/logs/install.log"
mkdir -p web/logs
: > "$LOG"

step() { print -P "\n%B==> $1%b"; }
fail() { print -P "\n%F{red}%BStopped:%b $1%f"; exit 1; }

step "Checking this Mac"
[[ "$(uname -m)" == arm64 ]] || fail "Voice Island needs a Mac with Apple Silicon (M1 or newer)."
macos=$(sw_vers -productVersion)
(( ${macos%%.*} >= 14 )) || fail "macOS 14 or newer is needed (this Mac has $macos)."
ram=$(( $(sysctl -n hw.memsize) / 1073741824 ))
free=$(df -g "$HOME" | awk 'NR==2 {print $4}')
print "  $(sysctl -n machdep.cpu.brand_string), $ram GB of memory, $free GB free on disk, macOS $macos"
(( free >= 10 )) || fail "Keep at least 10 GB free: the models take about 5 GB."
if (( ram < 16 )); then
  print "  $ram GB: dictation works well; voice commands and the voice assistant will be slow."
fi

step "Checking Xcode and Homebrew"
xcodebuild -version > /dev/null 2>&1 || fail "Install Xcode from the App Store, open it once, then run ./install.sh again."
command -v brew > /dev/null || fail 'Install Homebrew first (one line, from https://brew.sh):
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
then run ./install.sh again.'

step "Installing tools: livekit, xcodegen, uv (Homebrew)"
brew install livekit xcodegen uv >> "$LOG" 2>&1 || fail "brew install failed, see web/logs/install.log"

step "Installing the assistant's Python packages (first time: a few minutes)"
(cd web/agent && uv sync && uv run agent.py download-files) >> "$LOG" 2>&1 \
  || fail "Python setup failed, see web/logs/install.log"

step "Building the Mac app"
(cd mac && xcodegen generate && xcodebuild -project VoiceIsland.xcodeproj -scheme VoiceIsland \
  -configuration Debug -derivedDataPath build build) >> "$LOG" 2>&1 \
  || fail "The build failed, see web/logs/install.log"
APP="$HOME/Applications/Voice Island.app"
mkdir -p "$HOME/Applications"
pkill -x VoiceIsland 2> /dev/null || true
rm -rf "$APP"
cp -R mac/build/Build/Products/Debug/VoiceIsland.app "$APP"
print "  Installed in ~/Applications (find it with Spotlight: \"Voice Island\")."

step "Starting the local services (first time: downloads the models, about 5 GB)"
web/dev.sh

open "$APP"
print -P "\n%F{green}%BDone.%b%f Voice Island is in the menu bar."
print "  - macOS asks for the microphone, Accessibility and Input Monitoring: allow them."
print "  - Hold Fn to dictate (set System Settings › Keyboard › \"Press 🌐 key to\" to \"Do Nothing\")."
print "  - Dashboard: ⌥ ⇧ Space. The \"Your Mac\" page shows what this Mac runs well."
print "  - After a restart of the Mac: open Voice Island, then Dashboard › Status › Start all."
