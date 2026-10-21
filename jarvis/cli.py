"""jarvis app | start | on | off | status | say ТЕКСТ | voice [ИМЯ] | get-piper | test | install-hooks | uninstall-hooks"""
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from . import config

SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK = config.ROOT / "hooks" / "jarvis_hook.py"
PY = config.ROOT / ".venv" / "bin" / "python"
MARK = "jarvis_hook.py"


def _cmd(event: str) -> str:
    return f"{PY} {HOOK} {event}"


HOOKS = {
    "Stop": [{"hooks": [{"type": "command", "command": _cmd("stop"), "timeout": 600}]}],
    "PreToolUse": [{"matcher": "AskUserQuestion",
                    "hooks": [{"type": "command", "command": _cmd("ask"), "timeout": 600}]}],
    "Notification": [{"hooks": [{"type": "command", "command": _cmd("notify"), "timeout": 10}]}],
}


def _strip(settings: dict) -> dict:
    hooks = settings.get("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event]
                        if not any(MARK in h.get("command", "") for h in g.get("hooks", []))]
        if not hooks[event]:
            del hooks[event]
    return settings


def install(remove: bool = False) -> None:
    settings = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    if SETTINGS.exists():
        backup = SETTINGS.with_name(f"settings.backup-{time.strftime('%Y%m%d-%H%M%S')}.json")
        shutil.copy(SETTINGS, backup)
        print(f"бэкап: {backup}")
    settings = _strip(settings)
    if not remove:
        hooks = settings.setdefault("hooks", {})
        for event, groups in HOOKS.items():
            hooks.setdefault(event, []).extend(groups)
    SETTINGS.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    print("хуки удалены" if remove else "хуки Stop, PreToolUse(AskUserQuestion), Notification установлены")


def _post(path: str, body: dict):
    req = urllib.request.Request(f"http://{config.HOST}:{config.PORT}{path}",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())


def main() -> None:
    cmd, *rest = sys.argv[1:] or ["status"]
    if cmd == "start":
        from . import daemon
        daemon.main()
    elif cmd == "on":
        config.OFF_FLAG.unlink(missing_ok=True)
        print("Jarvis включён")
    elif cmd == "off":
        config.OFF_FLAG.touch()
        print("Jarvis выключен — хуки молчат")
    elif cmd == "status":
        try:
            r = json.loads(urllib.request.urlopen(f"http://{config.HOST}:{config.PORT}/", timeout=2).read())
            print(f"демон работает, {'включён' if r['enabled'] else 'выключен'}, ждут очереди: {r['waiting']}")
        except Exception:
            print(f"демон не запущен{' (и выключен)' if config.OFF_FLAG.exists() else ''}")
    elif cmd == "say":
        _post("/say", {"text": " ".join(rest)})
    elif cmd == "app":
        from . import app
        app.main()
    elif cmd == "get-piper":
        import subprocess
        config.PIPER_DIR.mkdir(exist_ok=True)
        for name in ("dmitri", "denis", "ruslan"):
            subprocess.run([sys.executable, "-m", "piper.download_voices", f"ru_RU-{name}-medium"],
                           cwd=config.PIPER_DIR, check=True)
    elif cmd == "voice":
        if rest and rest[0] in config.VOICES:
            config.VOICE_FILE.write_text(rest[0])
        print(f"голос: {config.speaker()} (доступны: {', '.join(config.VOICES)})")
    elif cmd == "mode":
        if rest and rest[0] in config.MODES:
            config.MODE_FILE.write_text(rest[0])
        print(f"режим: {config.mode()} — {config.MODES[config.mode()]} (доступны: {', '.join(config.MODES)})")
    elif cmd == "test":
        from . import voice
        voice.speak("Проверка связи. Скажи что-нибудь после сигнала.")
        said = voice.listen(15)
        voice.speak(f"Ты сказал: {said}" if said else "Ничего не услышал.")
    elif cmd == "install-hooks":
        install()
    elif cmd == "uninstall-hooks":
        install(remove=True)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
