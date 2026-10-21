import time

from . import config


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(config.LOG_FILE, "a") as f:
        f.write(line + "\n")
