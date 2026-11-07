#!/usr/bin/env python3
"""Adapt the Omarchy bridge to Herdr's bounded command runtime."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time

sys.dont_write_bytecode = True
import bridge

ROOT = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
OWN_STATE = ROOT / "herdr-lazyagent/limits.json"
OMARCHY_STATE = ROOT / "omarchy-lazyagent/limits.json"
original_load = bridge.load_state


def read_state(path):
    os.environ["OMARCHY_LAZYAGENT_STATE"] = str(path)
    try:
        return original_load()
    finally:
        os.environ["OMARCHY_LAZYAGENT_STATE"] = str(OWN_STATE)


def load_best():
    candidates = [read_state(OWN_STATE), read_state(OMARCHY_STATE)]
    candidates = [s for s in candidates if s.get("agent") == "all"]
    if not candidates:
        return {}
    latest = dict(max(candidates, key=lambda s: s.get("fetchedAt", 0)))
    latest["backoff"] = max(
        (s.get("backoff", bridge.sanitize_backoff(None)) for s in candidates),
        key=lambda b: b.get("until", 0),
    )
    return latest


def locate():
    # Avoid discovery subprocesses: reserve the command budget for the providers.
    found = shutil.which("lazyagent")
    if found:
        return found
    for candidate in [Path.home() / ".local/bin/lazyagent", Path("/usr/bin/lazyagent"),
                      Path("/opt/homebrew/bin/lazyagent"), Path("/usr/local/bin/lazyagent")]:
        if bridge.executable(candidate):
            return str(candidate)
    return None


def cached_payload():
    state = load_best()
    return bridge.payload_from(state, state.get("backoff", bridge.sanitize_backoff(None)),
                               True, installed=bool(locate()))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    if mode not in {"auto", "refresh", "cached"}:
        return bridge.emit({"ok": False, "error": "Unknown refresh mode"})
    os.environ["OMARCHY_LAZYAGENT_STATE"] = str(OWN_STATE)
    bridge.LIMITS_TIMEOUT = 22
    bridge.load_state = load_best
    bridge.locate_lazyagent = locate
    bridge.version_of = lambda _binary: ""
    if mode == "cached":
        payload = cached_payload()
    else:
        OWN_STATE.parent.mkdir(parents=True, exist_ok=True)
        with (OWN_STATE.parent / "refresh.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                payload = cached_payload()
                payload["busy"] = True
            else:
                # An explicit refresh respects provider backoff too.
                payload = bridge.snapshot("all", max_age=0 if mode == "refresh" else 300)
    payload["machine"] = socket.gethostname()
    payload["readAt"] = time.time()
    return bridge.emit(payload)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        raise SystemExit(bridge.emit({"ok": False, "error": bridge.compact(str(error))}))
