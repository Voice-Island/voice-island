#!/bin/zsh
# Fixed local address so the assistant works with Wi-Fi off.
#
# The WebRTC engine of the LiveKit SDKs (Mac app, agent) ignores 127.0.0.1: without Wi-Fi, the Mac has no other
# address left and the connection fails ("connection failed timeout"). We add a private address, 10.254.77.1, on the
# Thunderbolt bridge (bridge0, present on Apple Silicon Macs). It never leaves the Mac.
#
#   sudo web/scripts/local-address.sh test        adds the address until the next restart
#   sudo web/scripts/local-address.sh install     adds it at every startup (system service)
#   sudo web/scripts/local-address.sh uninstall   removes the service and the address
set -eu

ADDR=10.254.77.1
IFACE=bridge0
LABEL=dev.voiceisland.local-address
PLIST=/Library/LaunchDaemons/$LABEL.plist

if [[ $EUID -ne 0 ]]; then
  echo "À lancer avec sudo." >&2
  exit 1
fi
if ! ifconfig "$IFACE" > /dev/null 2>&1; then
  echo "Interface $IFACE absente sur ce Mac." >&2
  exit 1
fi

add() { ifconfig "$IFACE" alias "$ADDR" netmask 255.255.255.255; }

case "${1:-}" in
  test)
    add
    echo "Adresse $ADDR ajoutée sur $IFACE (jusqu'au prochain redémarrage)."
    ;;
  install)
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/sbin/ifconfig</string><string>$IFACE</string><string>alias</string>
    <string>$ADDR</string><string>netmask</string><string>255.255.255.255</string>
  </array>
  <key>RunAtLoad</key><true/>
</dict>
</plist>
EOF
    chown root:wheel "$PLIST"
    chmod 644 "$PLIST"
    launchctl bootout system "$PLIST" 2> /dev/null || true
    launchctl bootstrap system "$PLIST"
    echo "Service installé : $ADDR sur $IFACE à chaque démarrage."
    ;;
  uninstall)
    launchctl bootout system "$PLIST" 2> /dev/null || true
    rm -f "$PLIST"
    ifconfig "$IFACE" -alias "$ADDR" 2> /dev/null || true
    echo "Service et adresse retirés."
    ;;
  *)
    sed -n 2,11p "$0"
    exit 1
    ;;
esac
