"""Чаты Claude, которые видел Jarvis: название из приложения, проект, отложенные сообщения."""
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Chat:
    id: str
    title: str
    project: str
    waiting: bool = False                     # стоит в очереди к Jarvis прямо сейчас
    pending: list[str] = field(default_factory=list)  # «чат X, …» — уйдёт при его остановке
    seen: float = 0.0
    last: str = ""                            # последняя выжимка — о чём чат сейчас
    unread: bool = False                      # закончил шаг, а ты ещё не слушал
    resume: threading.Event | None = None     # вопрос отложен «потом» — ждёт ⌃⌥J или клика в меню


_chats: dict[str, Chat] = {}
lock = threading.Lock()


def _title(transcript: str | None) -> str | None:
    """Название чата, как в приложении Claude (custom-title в файле сессии)."""
    if not transcript or not Path(transcript).exists():
        return None
    title = None
    with open(transcript, errors="ignore") as f:
        for line in f:
            if '"custom-title"' in line or '"ai-title"' in line:
                try:
                    e = json.loads(line)
                    title = e.get("customTitle") or e.get("aiTitle") or title
                except ValueError:
                    pass
    return title


def touch(data: dict) -> Chat:
    sid = data.get("session_id") or "?"
    project = Path(data.get("cwd") or ".").name
    title = _title(data.get("transcript_path")) or project
    with lock:
        chat = _chats.get(sid) or Chat(sid, title, project)
        chat.title, chat.project, chat.seen = title, project, time.time()
        _chats[sid] = chat
        return chat


def recent(hours: float = 12) -> list[Chat]:
    edge = time.time() - hours * 3600
    with lock:
        return sorted((c for c in _chats.values() if c.seen > edge), key=lambda c: -c.seen)


def any_waiting() -> bool:
    with lock:
        return any(c.waiting for c in _chats.values())


def deferred() -> list[Chat]:
    with lock:
        return [c for c in _chats.values() if c.resume is not None]
