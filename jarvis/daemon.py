"""Фоновый сервис Jarvis: HTTP-сервер для хуков Claude Code, голосовые команды, горячая клавиша."""
import json
import random
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import brain, config, sessions, status, voice
from .log import log
from .sessions import Chat

dialog = threading.Lock()          # одновременно говорит и слушает только один диалог
_screen_handoff: dict[str, float] = {}   # чат → когда отправили отвечать на экране

# ── Фразы «своего парня» ─────────────────────────────────────────────────

SAY = {
    "ack": ["Принял, передаю в «{}».", "Понял, несу в «{}».", "Окей, отнёс в «{}».", "Записал, передаю в «{}»."],
    "cancel": ["Понял, молчу.", "Ок, ничего не передаю.", "Отбой, принято."],
    "wait": ["Ладно, жду тебя в чате.", "Ок, вернёшься — разберёмся.", "Понял, не горит."],
    "deaf": ["Не расслышал. Отвечай на экране.", "Тишина в эфире. Ладно, на экране ответишь."],
    "mute": ["Всё, молчу. Включишь меня в меню или хоткеем.", "Ушёл в тень. Зови, если что."],
    "hello": ["Джарвис на связи. Погнали.", "Я тут. Работаем.", "Джарвис в эфире."],
    "bye": ["Давай, на связи.", "Ок, если что — зови.", "Погнал работать."],
}


def say(kind: str, *args) -> str:
    return random.choice(SAY[kind]).format(*args)


# ── Голосовые команды ────────────────────────────────────────────────────

WAKE = re.compile(r"^\W*джарвис\w*\W*", re.I)
STOP_WORDS = ("стоп", "отбой", "отмена", "хватит", "не передавай", "ничего не передавай", "забудь")
SHORT_NO = re.compile(r"^\W*(ничего|нет|не надо|потом|не сейчас|всё|все|пока всё|пока ничего)\W*$", re.I)
MUTE = re.compile(r"^\W*(помолчи|замолчи|выключись|спи|отключись|уйди)\b", re.I)
UNMUTE = re.compile(r"^\W*(включись|проснись|вернись)\b", re.I)
REPEAT = re.compile(r"^\W*(повтори\w*|ещё раз|еще раз|не расслышал\w*|что\W*$|а\W*$|какой вопрос|что ты сказал)", re.I)
YES = re.compile(r"^\W*(да|давай|говори|ага|угу|рассказывай|расскажи|можно|могу|валяй|ок|окей|слушаю|"
                 r"ну давай|конечно|го|погнали|поехали)\b", re.I)
NO = re.compile(r"^\W*(нет|потом|не сейчас|не могу|позже|занят|подожди|погоди|не надо|отстань)\b", re.I)
DICTATE = re.compile(r"^\W*(запиши|в буфер|продиктую|диктовка)\W*", re.I)
COMMANDISH = re.compile(r"\b(чат\w*|переключи\w*|передай\w*)\b", re.I)


def interpret(said: str) -> dict:
    """Ответ голосом → {intent: cancel|mute|unmute|reply|route|list, chat: Chat|None, text}."""
    text = said.strip()
    bare = WAKE.sub("", text)
    low = bare.lower()
    if any(low.startswith(w) for w in STOP_WORDS) or SHORT_NO.match(bare):
        return {"intent": "cancel"}
    if MUTE.match(bare):
        return {"intent": "mute"}
    if UNMUTE.match(bare):
        return {"intent": "unmute"}
    if REPEAT.match(bare) or re.search(r"повтор\w* вопрос", low):
        return {"intent": "repeat"}
    if not COMMANDISH.search(text):
        return {"intent": "reply", "text": bare if WAKE.match(text) else text}
    chats = sessions.recent()
    cmd = brain.command(text, [c.title for c in chats])
    cmd["chat"] = chats[cmd["chat"] - 1] if cmd.get("chat") else None
    # модель только классифицирует — передаём фразу дословно
    cmd["text"] = bare if cmd.get("has_message", True) else ""
    if cmd["chat"] is not None and cmd["intent"] == "reply":
        cmd["intent"] = "route"          # назвал чат — значит, адресует ему
    if cmd["intent"] == "route" and cmd["chat"] is None and re.search(r"\bсюда\b", low):
        cmd["intent"], cmd["text"] = "reply", bare
    log(f"команда: {cmd['intent']} → {cmd['chat'].title if cmd['chat'] else '—'} «{cmd.get('text', '')}»")
    return cmd


def chat_list() -> str:
    chats = sessions.recent()
    if not chats:
        return "Пока ни одного чата не видел."
    parts = []
    for c in chats[:6]:
        mark = ", ждёт очереди" if c.waiting else ""
        mark += ", для него есть сообщение" if c.pending else ""
        parts.append(f"{c.title}{mark}")
    return "Чаты: " + "; ".join(parts) + "."


def route(target: Chat, text: str) -> None:
    if not text:
        voice.speak(f"Что передать в «{target.title}»?")
        text = voice.listen(config.ANSWER_TIMEOUT)
        if not text or interpret(text)["intent"] == "cancel":
            voice.speak(say("cancel"))
            return
    with sessions.lock:
        target.pending.append(text)
    notify(target.title, f"Отложено: {text}")
    if target.waiting:
        voice.speak(f"Закинул в «{target.title}», он как раз в очереди.")
    else:
        voice.speak(f"Закинул в «{target.title}». Передам, как только он закончит текущий шаг.")


def converse(chat: Chat | None, prompt_timeout: float, replay: str = "") -> str | None:
    """Слушает и выполняет команды. Возвращает текст для текущего чата или None.
    replay — что повторить по команде «повтори»."""
    timeout = prompt_timeout
    for _ in range(4):  # список чатов / переспрос — и слушаем дальше
        said = voice.listen(timeout) if timeout > 0 else ""
        if not said:
            return None
        cmd = interpret(said)
        intent = cmd["intent"]
        if intent == "cancel":
            voice.speak(say("cancel"))
            return None
        if intent == "mute":
            config.OFF_FLAG.touch()
            voice.speak(say("mute"))
            return None
        if intent == "unmute":
            config.OFF_FLAG.unlink(missing_ok=True)
            voice.speak("Я снова в деле.")
            return None
        if intent == "repeat":
            voice.speak(replay or "Да я ничего и не спрашивал. Говори, что передать.")
        elif intent == "list":
            voice.speak(chat_list())
        elif intent == "route" and cmd["chat"] is None:
            voice.speak("Не понял, в какой чат. Скажи название, или «сюда», если это этому чату.")
        elif intent == "route" and chat and cmd["chat"].id == chat.id:
            return cmd.get("text") or None
        elif intent == "route":
            route(cmd["chat"], cmd.get("text", ""))
            return None
        else:
            return cmd.get("text") or said
        timeout = config.ANSWER_TIMEOUT
    return None


# ── Хуки ─────────────────────────────────────────────────────────────────

NOTIFY_PHRASES = {
    "permission_prompt": "Клоду нужно твоё разрешение. Сходи глянь.",
    "elicitation_dialog": "Клод ждёт от тебя данные в форме. Сходи глянь.",
    "elicitation_url_dialog": "Клод просит открыть ссылку и подтвердить. Сходи глянь.",
    "agent_needs_input": "Агенту нужен твой ввод. Сходи глянь.",
}


def notify(title: str, message: str, target: str | None = None) -> None:
    cmd = ["terminal-notifier", "-title", f"Jarvis · {title}", "-message", message,
           "-sound", "Glass", "-group", f"jarvis-{title}"]
    if target:
        url = target if re.match(r"^\w+://", target) else "file://" + str(Path(target).expanduser())
        cmd += ["-open", url]
    try:
        subprocess.run(cmd, check=False, timeout=5)
    except FileNotFoundError:
        subprocess.run(["osascript", "-e", f'display notification {json.dumps(message)} '
                                           f'with title {json.dumps("Jarvis · " + title)}'], check=False)


def _last_message(transcript: str | None) -> str:
    """Запасной путь, если хук не прислал last_assistant_message."""
    if not transcript or not Path(transcript).exists():
        return ""
    texts = []
    for line in Path(transcript).read_text().splitlines()[-200:]:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "user" and isinstance(content, str):
            texts = []
        elif e.get("type") == "assistant" and isinstance(content, list):
            texts += [c["text"] for c in content if c.get("type") == "text"]
    return "\n".join(texts)


def _continue(text: str) -> dict:
    return {"decision": "block",
            "reason": f"Пользователь ответил голосом через Jarvis: «{text}». Продолжай работу с учётом этого."}


def knock(phrase: str) -> str:
    """Постучаться: звук + короткая фраза, ждём согласия. → yes | no | none.
    Любая другая речь — ты говоришь не с Джарвисом: молчим и ничего не забираем."""
    voice.cue()
    voice.speak(phrase)
    said = voice.listen(config.KNOCK_WINDOW, beep=False)
    if not said:
        return "none"
    bare = WAKE.sub("", said)
    if YES.match(bare):
        return "yes"
    if NO.match(bare) or SHORT_NO.match(bare) or any(bare.lower().startswith(w) for w in STOP_WORDS):
        return "no"
    log(f"не ответ на стук, пропускаю: «{said}»")
    return "none"


def wait_deferred(chat: Chat) -> bool:
    """Держит хук, пока не позовёшь (⌃⌥J или клик в меню), но не дольше DEFER. Диалог свободен."""
    with sessions.lock:
        chat.resume = threading.Event()
    notify(chat.title, "Ждёт твоего ответа — жми ⌃⌥J или выбери чат в меню Jarvis")
    try:
        return chat.resume.wait(config.DEFER)
    finally:
        with sessions.lock:
            chat.resume = None


def resume(chat: Chat | None = None) -> bool:
    """Вернуться к отложенному вопросу: указанного чата или самого старого."""
    waiting = sessions.deferred()
    if chat is not None:
        waiting = [c for c in waiting if c.id == chat.id]
    if not waiting:
        return False
    waiting[0].resume.set()
    return True


def on_stop(data: dict) -> dict:
    chat = sessions.touch(data)
    message = data.get("last_assistant_message") or _last_message(data.get("transcript_path"))
    if not message.strip() and not chat.pending:
        return {}
    status.set("thinking", chat.title)
    s = brain.summarize(message, chat.title) if message.strip() else {"speech": "", "question": None, "look": None}
    look = s.get("look")
    if look:
        notify(chat.title, look.get("what") or "Нужно глянуть", look.get("target"))

    mode = config.mode()
    question = s.get("question")
    chat.last = s["speech"]
    if mode == "quiet" and not chat.pending:
        chat.unread = True
        voice.cue()
        notify(chat.title, "Нужен твой ответ" if question else "Готово")
        return {}

    with sessions.lock:
        chat.waiting = True
    dialog.acquire()
    held = True
    try:
        with sessions.lock:
            chat.waiting = False
            queued = chat.pending.pop(0) if chat.pending else None
        status.set("speaking", chat.title)
        if queued:  # ты уже сказал, что передать, — спрашивать незачем
            voice.speak(f"«{chat.title}» освободился, передаю твоё: {queued}")
            return _continue(queued)
        if mode == "knock":
            answer = knock(f"«{chat.title}»: нужен твой ответ. Можешь говорить?" if question
                           else f"«{chat.title}» — готово. Рассказать?")
            if answer == "no":
                voice.speak("Ок, позже." if question else "Ок, спросишь — расскажу.")
            if answer != "yes":
                chat.unread = True
                if not question:
                    return {}
                dialog.release()
                held = False
                status.set("idle", "")
                if not wait_deferred(chat):
                    log(f"«{chat.title}»: ответа не дождался, вопрос остался на экране")
                    return {}
                dialog.acquire()
                held = True
                status.set("speaking", chat.title)
        chat.unread = False
        voice.speak(f"Чат «{chat.title}». {s['speech']}")
        if look:
            voice.speak(f"Сходи глянь: {look.get('what', '')}. Кинул уведомление.")
        if question:
            voice.speak(question)
        replay = " ".join(x for x in (s["speech"], question or "") if x)
        said = converse(chat, config.ANSWER_TIMEOUT if question else config.FOLLOWUP_WINDOW, replay)
        if not said:
            if question:
                voice.speak(say("wait"))
            return {}
        voice.speak(say("ack", chat.title))
        return _continue(said)
    finally:
        if held:
            dialog.release()
        with sessions.lock:
            chat.waiting = False
        status.set("idle", "")


def on_ask(data: dict) -> dict:
    """AskUserQuestion: озвучиваем вопросы и отвечаем за пользователя через updatedInput."""
    tool_input = data.get("tool_input") or {}
    questions = tool_input.get("questions") or []
    if not questions:
        return {}
    chat = sessions.touch(data)
    notify(chat.title, "Вопрос с вариантами — отвечай вслух или на экране")
    mode = config.mode()
    if mode == "quiet":
        voice.cue()
        return {}
    answers = {}
    dialog.acquire()
    held = True
    try:
        status.set("speaking", chat.title)
        if mode == "knock":
            # карточку не держим: пока хук ждёт, на экране её тоже нет
            answer = knock(f"«{chat.title}»: вопрос с вариантами. Можешь говорить?")
            if answer != "yes":
                if answer == "no":
                    voice.speak("Ок, карточка на экране.")
                _screen_handoff[chat.id] = time.time()
                return {}
        else:
            voice.speak(f"Чат «{chat.title}». Есть вопрос.")
        for q in questions:
            voice.speak(brain.speak_question(q))
            said = voice.listen(config.ANSWER_TIMEOUT)
            cmd = interpret(said) if said else {"intent": "cancel"}
            if not said or cmd["intent"] in ("cancel", "mute"):
                voice.speak(say("deaf") if not said else "Ок, ответишь на экране.")
                _screen_handoff[chat.id] = time.time()
                return {}
            r = brain.pick(q, cmd.get("text") or said)
            if r.get("skip"):
                voice.speak("Ок, на экране.")
                _screen_handoff[chat.id] = time.time()
                return {}
            answers[q["question"]] = r["answer"]
            log(f"ответ: {q['question']} → {r['answer']}")
        voice.speak(say("ack", chat.title))
    finally:
        if held:
            dialog.release()
        status.set("idle", "")
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {**tool_input, "answers": answers},
    }}


def on_notify(data: dict) -> dict:
    phrase = NOTIFY_PHRASES.get(data.get("notification_type", ""))
    if not phrase:
        return {}
    chat = sessions.touch(data)
    if time.time() - _screen_handoff.get(chat.id, 0) < 60:
        return {}  # только что сами отправили отвечать на экране — не дублируем
    notify(chat.title, data.get("message") or phrase)
    if config.mode() == "quiet":
        voice.cue()
        return {}

    def speak():
        with dialog:
            voice.speak(f"Чат «{chat.title}». {phrase}")
    threading.Thread(target=speak, daemon=True).start()
    return {}


def on_say(data: dict) -> dict:
    with dialog:
        voice.speak(data.get("text", ""))
    return {}


def _chats_context() -> str:
    chats = sessions.recent()
    if not chats:
        return "Пока ни одного чата не видел."
    return "\n".join(f"— «{c.title}»: {c.last or 'пока ничего не рассказывал'}"
                     + (", ждёт очереди" if c.waiting else "")
                     + (f", для него отложено: {'; '.join(c.pending)}" if c.pending else "")
                     for c in chats[:8])


def on_hotkey() -> None:
    """Заговорить первым: болтовня с Джарвисом; «чат Икс, …» — отложить в чат; «запиши …» — в буфер."""
    if resume():  # висит отложенный вопрос — ⌃⌥J и есть «да, говори»
        return
    if not dialog.acquire(blocking=False):
        return
    try:
        unread = [c for c in sessions.recent() if c.unread]
        for c in unread:
            c.unread = False
        names = ", ".join(f"«{c.title}»" for c in unread[:4])
        voice.speak(f"Слушаю. Кстати, готовы: {names} — спроси, что там." if unread else "Слушаю.")
        history: list[dict] = []
        last = ""
        timeout = 10
        for _ in range(12):
            said = voice.listen(timeout)
            if not said:
                if history:
                    voice.speak(say("bye"))
                return
            timeout = config.CHAT_WINDOW
            m = DICTATE.match(WAKE.sub("", said))
            if m:
                text = WAKE.sub("", said)[m.end():].strip() or said
                subprocess.run(["pbcopy"], input=text.encode(), check=False)
                voice.speak("Записал в буфер, вставляй.")
                return
            cmd = interpret(said)
            intent = cmd["intent"]
            if intent == "cancel":
                voice.speak(say("bye") if history else say("cancel"))
                return
            if intent == "mute":
                config.OFF_FLAG.touch()
                voice.speak(say("mute"))
                return
            if intent == "unmute":
                config.OFF_FLAG.unlink(missing_ok=True)
                voice.speak("Я снова в деле.")
                continue
            if intent == "repeat":
                voice.speak(last or "Да я ещё ничего не сказал. Говори.")
                continue
            if intent == "list":
                voice.speak(chat_list())
                continue
            if intent == "route":
                if cmd["chat"]:
                    route(cmd["chat"], cmd.get("text", ""))
                else:
                    voice.speak("Не понял, какой чат. " + chat_list())
                continue
            status.set("thinking")
            history.append({"role": "user", "content": cmd.get("text") or said})
            out = brain.talk(history, _chats_context())
            last = out["speech"]
            history.append({"role": "assistant", "content": last})
            voice.speak(last)
            if out.get("bye"):
                return
            if sessions.any_waiting():
                voice.speak("Погоди, там Клод закончил шаг — переключаюсь.")
                return
    finally:
        dialog.release()
        status.set("idle", "")


# ── HTTP ─────────────────────────────────────────────────────────────────

ROUTES = {"/stop": on_stop, "/ask": on_ask, "/notify": on_notify, "/say": on_say}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send(200, {"ok": True, "enabled": not config.OFF_FLAG.exists(), "mode": status.mode,
                         "chats": [{"title": c.title, "waiting": c.waiting, "pending": len(c.pending)}
                                   for c in sessions.recent()]})

    def do_POST(self):
        route_fn = ROUTES.get(self.path)
        if not route_fn:
            return self._send(404, {})
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        log(f"→ {self.path} {Path(data.get('cwd') or '.').name}")
        try:
            self._send(200, route_fn(data))
        except Exception as e:
            log(f"ошибка {self.path}: {e!r}")
            self._send(200, {})

    def log_message(self, *_):
        pass


def start_hotkey() -> None:
    """Хоткей для запуска без меню-бара (./jarvis.sh start). Нужен «Универсальный доступ» для терминала."""
    try:
        from pynput import keyboard
        keyboard.GlobalHotKeys({config.HOTKEY: lambda: threading.Thread(
            target=on_hotkey, daemon=True).start()}).start()
        log(f"хоткей {config.HOTKEY} — заговорить первым")
    except Exception as e:
        log(f"хоткей не включился ({e}). Дай доступ: Настройки → Конфиденциальность → Универсальный доступ.")


def serve(greet: bool = True, hotkey: bool = True) -> None:
    status.set("loading")
    log("загружаю голос, VAD и Whisper (первый запуск качает модели)…")
    voice.warmup()
    try:
        brain.summarize("Тест.", "jarvis")
    except Exception as e:
        log(f"Ollama не отвечает: {e}")
    if hotkey:
        start_hotkey()
    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    log(f"Jarvis слушает http://{config.HOST}:{config.PORT}")
    status.set("idle")
    if greet:
        voice.speak(say("hello"))
    server.serve_forever()


def main() -> None:
    serve()
