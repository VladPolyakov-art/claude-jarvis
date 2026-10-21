"""Голос (Silero TTS) и слух (Silero VAD + Whisper на MLX)."""
import collections
import queue
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import sounddevice as sd
import torch

from . import config, status
from .log import log

torch.set_num_threads(4)

# ── Голос ────────────────────────────────────────────────────────────────

_tts = None
_tts_lock = threading.Lock()

WORDS = {
    "claude": "клод", "code": "код", "jarvis": "джарвис", "react": "реакт", "figma": "фигма",
    "github": "гитхаб", "json": "джейсон", "ollama": "оллама", "whisper": "виспер", "silero": "силеро",
    "storybook": "сторибук", "astro": "астро", "notion": "ноушн", "python": "пайтон", "hook": "хук",
    "eugene": "юджин", "aidar": "айдар", "xenia": "ксения", "kseniya": "ксения", "baya": "бая",
    "piper": "пайпер", "dmitri": "дмитрий", "denis": "денис", "ruslan": "руслан",
    "ok": "окей", "okay": "окей", "project": "проджект", "builder": "билдер", "build": "билд",
    "artifact": "артефакт", "landing": "лендинг", "frontend": "фронтенд", "backend": "бэкенд",
    "deploy": "деплой", "commit": "коммит", "push": "пуш", "test": "тест", "tests": "тесты",
    "lint": "линт", "vite": "вайт", "typescript": "тайпскрипт", "playwright": "плейрайт", "prd": "пи ар ди", "ui": "ю ай", "ux": "ю икс", "api": "эй пи ай",
    "npm": "эн пи эм", "css": "си эс эс", "html": "эйч ти эм эл", "mvp": "эм ви пи", "qa": "кью эй",
}
LETTER_NAMES = dict(zip(
    "abcdefghijklmnopqrstuvwxyz",
    "эй би си ди и эф джи эйч ай джей кей эл эм эн оу пи кью ар эс ти ю ви дабл-ю экс уай зед".split(),
))
DIGRAPHS = [("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"), ("oo", "у"), ("ee", "и"),
            ("ck", "к"), ("qu", "кв"), ("ui", "и"), ("zh", "ж"), ("kh", "х"), ("ya", "я"), ("yu", "ю")]
LETTERS = dict(zip("abcdefghijklmnopqrstuvwxyz",
                   ["а", "б", "к", "д", "е", "ф", "г", "х", "и", "дж", "к", "л", "м",
                    "н", "о", "п", "к", "р", "с", "т", "у", "в", "в", "кс", "й", "з"]))


def _latin_word(m: re.Match) -> str:
    w = m.group(0)
    low = w.lower()
    if low in WORDS:
        return WORDS[low]
    if w.isupper() and len(w) <= 4:
        return " ".join(LETTER_NAMES[c] for c in low)
    for a, b in DIGRAPHS:
        low = low.replace(a, b)
    return "".join(LETTERS.get(c, c) for c in low)


def _number(m: re.Match) -> str:
    from num2words import num2words
    try:
        return " " + num2words(int(m.group(0)), lang="ru") + " "
    except Exception:
        return m.group(0)


def speakable(text: str) -> str:
    """Чистит текст под Silero: без markdown, ссылок, цифр и латиницы."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", " ссылка ", text)
    text = re.sub(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+(?:=\S+)?", " настройка ", text)   # JARVIS_TALK_MODEL=…
    text = re.sub(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b", " ", text)  # хеши коммитов
    for sym, word in (("⌃", "контрол"), ("⌥", "опшн"), ("⌘", "команд"), ("⇧", "шифт")):
        text = text.replace(sym, f" {word} ")
    text = re.sub(r"(\w):(\w)", r"\1 \2", text)  # qwen3:8b
    text = re.sub(r"(?:~|\.{1,2})?(?:/[\w.-]+){2,}", " файл ", text)
    text = re.sub(r"[`*_#>|\[\]{}<>]", " ", text)
    text = text.replace("%", " процентов").replace("&", " и ").replace("→", ", ")
    text = text.replace("+", " плюс ")  # у Silero «+» — знак ударения
    text = re.sub(r"(\d)[.,](\d)", r"\1 точка \2", text)
    text = re.sub(r"\d+", _number, text)
    text = re.sub(r"[A-Za-z]+", _latin_word, text)
    return " ".join(text.split())


def _load_tts():
    global _tts
    if _tts is None:
        for pack in ("v5_ru", "v4_ru"):
            try:
                _tts, _ = torch.hub.load("snakers4/silero-models", "silero_tts",
                                         language="ru", speaker=pack, trust_repo=True)
                log(f"Silero {pack} загружен, голос сейчас: {config.speaker()}")
                break
            except Exception as e:
                log(f"Silero {pack} не загрузился: {e}")
    return _tts


_piper: dict = {}


def _silero(part: str, name: str) -> np.ndarray:
    model = _load_tts()
    speaker, _, tone = name.partition("-")
    if tone == "low":  # ниже и чуть медленнее — «по-джарвисовски»
        ssml = f"<speak><prosody pitch='x-low' rate='slow'>{part}</prosody></speak>"
        audio = model.apply_tts(ssml_text=ssml, speaker=speaker, sample_rate=config.TTS_RATE)
    else:
        audio = model.apply_tts(text=part, speaker=speaker, sample_rate=config.TTS_RATE,
                                put_accent=True, put_yo=True)
    return audio.numpy()


def _piper_say(part: str, name: str) -> np.ndarray:
    from piper import PiperVoice, SynthesisConfig
    if name not in _piper:
        _piper[name] = PiperVoice.load(str(config.PIPER_DIR / f"ru_RU-{name}-medium.onnx"))
    v = _piper[name]
    chunks = [c.audio_float_array for c in v.synthesize(part, syn_config=SynthesisConfig(length_scale=1.05))]
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    # Piper отдаёт 22 кГц — растягиваем до общей частоты
    n = int(len(audio) * config.TTS_RATE / v.config.sample_rate)
    return np.interp(np.linspace(0, len(audio), n, endpoint=False), np.arange(len(audio)), audio).astype(np.float32)


def synth(text: str, name: str | None = None) -> np.ndarray:
    name = name or config.speaker()
    engine, _, voice = name.partition(":")
    parts = [p for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    audio = []
    for p in parts:
        try:
            audio.append(_piper_say(p, voice) if engine == "piper" else _silero(p[:900], name))
        except Exception as e:
            log(f"tts пропустил «{p}»: {e}")
    return np.concatenate(audio) if audio else np.zeros(0, dtype=np.float32)


def speak(text: str, name: str | None = None) -> None:
    text = speakable(text)
    if not text:
        return
    log(f"🗣  {text}")
    with _tts_lock:
        status.set("speaking")
        try:
            audio = synth(text, name)
            if len(audio):
                sd.play(audio, config.TTS_RATE)
                sd.wait()
        finally:
            status.set("idle")


def cue(name: str = "Tink") -> None:
    subprocess.run(["afplay", f"/System/Library/Sounds/{name}.aiff"], check=False)


# ── Слух ─────────────────────────────────────────────────────────────────

SR = 16000
BLOCK = 512  # 32 мс — размер окна Silero VAD на 16 кГц
_vad = None
# MLX держит GPU-стрим в потоке, где загрузилась модель, — весь Whisper гоняем в одном потоке
_mlx = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

HALLUCINATIONS = ("продолжение следует", "субтитры", "спасибо за просмотр", "редактор субтитров",
                  "подписывайтесь", "dimatorzok")


def _load_vad():
    global _vad
    if _vad is None:
        from silero_vad import load_silero_vad
        _vad = load_silero_vad()
    return _vad


def _whisper(audio: np.ndarray) -> str:
    import mlx_whisper
    out = mlx_whisper.transcribe(audio, path_or_hf_repo=config.WHISPER_MODEL, language="ru",
                                 condition_on_previous_text=False)
    return out["text"].strip()


def transcribe(audio: np.ndarray) -> str:
    text = _mlx.submit(_whisper, audio.astype(np.float32)).result()
    if any(h in text.lower() for h in HALLUCINATIONS):
        return ""
    return text


def listen(start_timeout: float = config.ANSWER_TIMEOUT, beep: bool = True) -> str:
    """Ждёт речь до start_timeout сек, пишет до паузы SILENCE_END, возвращает текст ('' — молчание)."""
    vad = _load_vad()
    q: queue.Queue = queue.Queue()
    preroll = collections.deque(maxlen=16)  # ~0.5 с до начала речи, чтобы не съесть первый слог
    frames, run, silence, peak = [], 0, 0.0, 0.0
    speaking, started, talk_start = False, time.time(), 0.0
    if beep:
        cue("Tink")
    status.set("listening")
    with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK,
                        callback=lambda d, *_: q.put(d[:, 0].copy())):
        while True:
            chunk = q.get(timeout=3)
            peak = max(peak, float(np.abs(chunk).max()))
            p = vad(torch.from_numpy(chunk), SR).item()
            if not speaking:
                preroll.append(chunk)
                run = run + 1 if p > 0.5 else 0
                if run >= 3:
                    speaking, talk_start, frames = True, time.time(), list(preroll)
                elif time.time() - started > start_timeout:
                    break
            else:
                frames.append(chunk)
                silence = 0.0 if p > 0.35 else silence + BLOCK / SR
                if silence >= config.SILENCE_END or time.time() - talk_start > config.MAX_UTTERANCE:
                    break
    vad.reset_states()
    if not speaking:
        status.set("idle")
        log("👂 тишина" if peak > 1e-5 else
            "👂 микрофон отдаёт нули — дай доступ: Настройки → Конфиденциальность → Микрофон → Jarvis")
        return ""
    cue("Pop")
    status.set("thinking")
    text = transcribe(np.concatenate(frames))
    status.set("idle")
    log(f"👂 {text or '(не разобрал)'}")
    return text


def warmup() -> None:
    _load_tts()
    _load_vad()
    transcribe(np.zeros(SR, dtype=np.float32))
