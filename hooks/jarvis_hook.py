#!/usr/bin/env python3
"""Хук Claude Code → демон Jarvis. Только stdlib. Если Jarvis выключен или не запущен — молча выходит."""
import json
import os
import sys
import urllib.request
from pathlib import Path

PORT = int(os.environ.get("JARVIS_PORT", 8765))
TIMEOUTS = {"stop": 590, "ask": 590, "notify": 3}


def main() -> None:
    event = sys.argv[1] if len(sys.argv) > 1 else "stop"
    if (Path.home() / ".jarvis" / "off").exists() or os.environ.get("JARVIS_SKIP"):
        return
    data = sys.stdin.read()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/{event}", data=data.encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUTS.get(event, 30)) as r:
            out = json.loads(r.read() or b"{}")
    except Exception:
        return
    if out:
        print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
