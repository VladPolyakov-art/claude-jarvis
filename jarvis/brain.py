"""Мозг: локальная Ollama превращает ответы Claude в короткую устную речь."""
import json
import re
import urllib.request

from . import config
from .log import log

STYLE = (
    "Ты — Джарвис, голосовой напарник разработчика, свой парень. Говоришь по-русски, на «ты», "
    "неформально, как друг-коллега: можешь подколоть, вставить шутку или живое словечко "
    "(«погнали», «красота», «есть нюанс»), но коротко и по делу — шутка не длиннее полфразы. "
    "Без канцелярита и «уважаемый пользователь». Твой текст будет озвучен синтезатором, поэтому: "
    "никакого markdown, списков, кода, путей к файлам и ссылок; английские слова и названия пиши "
    "кириллицей так, как их произносят (Claude → Клод, React → Реакт, hook → хук, Figma → Фигма)."
)


def _chat(system: str, user: str, schema: dict) -> dict:
    body = {
        "model": config.OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "options": {"temperature": 0.3},
    }
    req = urllib.request.Request(
        f"{config.OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(json.loads(r.read())["message"]["content"])


def _clip(text: str, head: int = 1500, tail: int = 4500) -> str:
    return text if len(text) <= head + tail else text[:head] + "\n…\n" + text[-tail:]


def _plain(text: str, limit: int = 220) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[`*_#>|\[\]]", "", text)
    return " ".join(text.split())[:limit]


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "speech": {"type": "string"},
        "question": {"type": ["string", "null"]},
        "look": {
            "type": ["object", "null"],
            "properties": {"what": {"type": "string"}, "target": {"type": ["string", "null"]}},
            "required": ["what", "target"],
        },
    },
    "required": ["speech", "question", "look"],
}


def summarize(message: str, project: str) -> dict:
    """Последнее сообщение Claude → {speech, question, look}."""
    system = STYLE + (
        "\nТебе дают последнее сообщение ИИ-агента Клода, который работает над проектом. Верни JSON:\n"
        "speech — 1–2 коротких предложения: что сделано и в каком состоянии работа; вопрос сюда не включай;\n"
        "question — только если в сообщении есть прямой вопрос к пользователю (со знаком «?»), "
        "переформулируй его одним коротким предложением для голоса (с вариантами, если они есть), иначе null;\n"
        "look — если агент просит пользователя что-то посмотреть или проверить (страницу, артефакт, "
        "приложение, браузер, файл): {what: что посмотреть, по-русски, коротко; target: URL или путь "
        "из сообщения как есть, или null}, иначе null."
    )
    try:
        out = _chat(system, f"Проект: {project}\n\nСообщение агента:\n{_clip(message)}", SUMMARY_SCHEMA)
        # маленькая модель додумывает — сверяем с исходным текстом
        if "?" not in message:
            out["question"] = None
        look = out.get("look")
        if look:
            look["what"] = (look.get("what") or "").strip(" \"'”“«»,.")
            target = look.get("target") or ""
            if not target or target not in message:
                urls = re.findall(r"https?://[^\s)\]>\"'`]+", message)
                target = urls[-1].rstrip(".,;") if urls else None
            look["target"] = target
            if not target:  # смотреть нечего — не дёргаем
                out["look"] = None
        if out.get("speech"):
            return out
    except Exception as e:
        log(f"brain.summarize: {e}")
    return {"speech": _plain(message), "question": None, "look": None}


def speak_question(q: dict) -> str:
    """Вопрос AskUserQuestion с вариантами → фраза для озвучки."""
    system = STYLE + (
        "\nПерескажи вопрос с вариантами ответа для озвучки. Варианты называй по порядку: "
        "«первый — …, второй — …», каждый в 2–5 словах. Если у варианта пометка Recommended — скажи, "
        "что советуешь его. Верни JSON {speech}."
    )
    try:
        return _chat(system, json.dumps(q, ensure_ascii=False), {
            "type": "object", "properties": {"speech": {"type": "string"}}, "required": ["speech"],
        })["speech"]
    except Exception as e:
        log(f"brain.speak_question: {e}")
        opts = "; ".join(f"{i + 1} — {o['label']}" for i, o in enumerate(q.get("options", [])))
        return f"{q.get('question', '')} Варианты: {opts}"


def pick(q: dict, said: str) -> dict:
    """Устный ответ пользователя → {answer, skip} для вопроса с вариантами."""
    system = (
        "Пользователь голосом ответил на вопрос с вариантами. Верни JSON {answer, skip}.\n"
        "Если он выбрал вариант (номером, названием или по смыслу) — answer = точный label варианта; "
        "при multiSelect несколько label через «, ».\n"
        "Если сказал «как советуешь», «на твой выбор» — label варианта с пометкой Recommended (или первого).\n"
        "Если хочет ответить сам на экране, пропустить, «руками», «потом» — skip = true.\n"
        "Иначе answer = его ответ своими словами, очищенный от слов-паразитов. skip по умолчанию false."
    )
    user = json.dumps({"question": q, "user_said": said}, ensure_ascii=False)
    try:
        return _chat(system, user, {
            "type": "object",
            "properties": {"answer": {"type": "string"}, "skip": {"type": "boolean"}},
            "required": ["answer", "skip"],
        })
    except Exception as e:
        log(f"brain.pick: {e}")
        return {"answer": said, "skip": False}


COMMAND_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["reply", "route", "list"]},
        "chat": {"type": ["integer", "null"]},
        "has_message": {"type": "boolean"},
    },
    "required": ["intent", "chat", "has_message"],
}


def command(said: str, chats: list[str]) -> dict:
    """Фраза со словом «чат» → {intent: reply|route|list, chat: номер|None, has_message}. Текст не трогаем."""
    listing = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(chats)) or "(чатов нет)"
    system = (
        "Разбери голосовую фразу пользователя ассистенту Джарвису. Открытые чаты:\n" + listing + "\n"
        "Верни JSON {intent, chat, has_message}:\n"
        "route — хочет обратиться к другому чату («чат Икс, …», «переключись на Икс», «передай в Икс», "
        "«скажи Иксу»): chat — номер из списка (сопоставляй по смыслу и звучанию, названия могут быть "
        "по-английски, а сказаны по-русски), null — если такого нет;\n"
        "list — спрашивает, какие чаты есть, кто ждёт, кто что делает;\n"
        "reply — обычный ответ текущему чату; обращение «Джарвис» не делает фразу командой.\n"
        "has_message — true, если кроме самой команды и названия чата во фразе есть содержательное "
        "сообщение (просьба, ответ, задача); «чат Икс» или «переключись на Икс» — false."
    )
    try:
        out = _chat(system, said, COMMAND_SCHEMA)
        if out.get("chat") is not None and not 1 <= out["chat"] <= len(chats):
            out["chat"] = None
        return out
    except Exception as e:
        log(f"brain.command: {e}")
        return {"intent": "reply", "chat": None, "has_message": True}
