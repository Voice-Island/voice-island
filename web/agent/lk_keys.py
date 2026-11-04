"""LiveKit keys specific to this installation (P0-4).

Before, every installation used the public keys of livekit-server's --dev mode (devkey / secret):
any program that reached port 7880 could sign a token, enter a room and make the assistant act
on the Mac. Now a random pair is drawn on first launch, written to web/livekit.keys
(never in git, readable by the user only), and livekit-server knows only that pair (`--key-file`): devkey is
refused ("invalid API key: devkey", checked on Nov 2).

Read by: dev.sh (server, agent, web interface), the Mac app (LocalTokenSource.swift) and the web/test scripts.
Format: that of `livekit-server --key-file`, one line "key: secret".

  uv run python lk_keys.py     creates the file if missing, then prints "key secret" to stdout
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

KEYS_FILE = Path(__file__).resolve().parent.parent / "livekit.keys"


def load(path: Path = KEYS_FILE) -> tuple[str, str] | None:
    try:
        line = path.read_text().strip().splitlines()[0]
    except (OSError, IndexError):
        return None
    key, _, secret = line.partition(":")
    key, secret = key.strip(), secret.strip()
    return (key, secret) if key and secret else None


def ensure(path: Path = KEYS_FILE) -> tuple[str, str]:
    """This installation's keys; drawn at random and saved the first time."""
    if found := load(path):
        return found
    key, secret = f"VI{secrets.token_hex(6)}", secrets.token_urlsafe(32)
    # Created directly as 0600: livekit-server refuses a key file readable by others.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(f"{key}: {secret}\n")
    return key, secret


def apply_env(path: Path = KEYS_FILE) -> bool:
    """Puts the installation's keys in the environment (taking priority over an old devkey from the .env file)."""
    found = load(path)
    if found:
        os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"] = found
    return bool(found)


if __name__ == "__main__":
    print(*ensure())
