"""Что Jarvis делает прямо сейчас — для иконки в меню-баре."""
mode = "loading"   # loading · idle · speaking · listening · thinking
chat = ""          # с каким чатом идёт разговор


def set(new_mode: str, new_chat: str | None = None) -> None:
    global mode, chat
    mode = new_mode
    if new_chat is not None:
        chat = new_chat
