"""Настройки Jarvis. Всё переопределяется переменными окружения JARVIS_*."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path.home() / ".jarvis"
HOME.mkdir(exist_ok=True)
OFF_FLAG = HOME / "off"          # есть файл → хуки молчат
LOG_FILE = HOME / "jarvis.log"

HOST = "127.0.0.1"
PORT = int(os.environ.get("JARVIS_PORT", 8765))

# Мозг: локальная Ollama
OLLAMA_URL = os.environ.get("JARVIS_OLLAMA", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("JARVIS_MODEL", "qwen3:4b-instruct")
TALK_MODEL = os.environ.get("JARVIS_TALK_MODEL", "qwen3:8b")  # болтовня по ⌃⌥J — поумнее, но медленнее

# Голос. Silero: мужские aidar, eugene (+ «-low» — ниже и медленнее), женские baya, kseniya, xenia.
# Piper: мужские piper:dmitri, piper:denis, piper:ruslan (модели в ~/.jarvis/piper)
VOICES = ("aidar", "eugene", "aidar-low", "eugene-low", "baya", "kseniya", "xenia",
          "piper:dmitri", "piper:denis", "piper:ruslan")
PIPER_DIR = HOME / "piper"
VOICE_FILE = HOME / "voice"      # `jarvis.sh voice NAME` — меняется на лету
TTS_RATE = 48000


def mode() -> str:
    try:
        name = MODE_FILE.read_text().strip()
        if name in MODES:
            return name
    except OSError:
        pass
    return os.environ.get("JARVIS_MODE", "knock")


def speaker() -> str:
    try:
        name = VOICE_FILE.read_text().strip()
        if name in VOICES:
            return name
    except OSError:
        pass
    return os.environ.get("JARVIS_VOICE", "aidar")

# Слух: Whisper на MLX (Apple Silicon)
WHISPER_MODEL = os.environ.get("JARVIS_WHISPER", "mlx-community/whisper-large-v3-turbo")

# Диалог
MODES = {
    "knock": "Сначала спрашивать «можешь говорить?»",
    "talk": "Сразу рассказывать",
    "quiet": "Только звук и уведомление",
}
MODE_FILE = HOME / "mode"
KNOCK_WINDOW = float(os.environ.get("JARVIS_KNOCK", 6))     # сколько ждать «да» на «можешь говорить?», с
DEFER = float(os.environ.get("JARVIS_DEFER", 180))           # сколько держать отложенный вопрос, с
CHAT_WINDOW = float(os.environ.get("JARVIS_CHAT_WINDOW", "8"))  # сколько ждать реплику в болтовне
HOTKEY = os.environ.get("JARVIS_HOTKEY", "<ctrl>+<alt>+j")
ANSWER_TIMEOUT = float(os.environ.get("JARVIS_ANSWER_TIMEOUT", 30))   # ждать ответа на вопрос, с
FOLLOWUP_WINDOW = float(os.environ.get("JARVIS_FOLLOWUP", 6))         # «есть что добавить?» без вопроса, с; 0 — не слушать
SILENCE_END = float(os.environ.get("JARVIS_SILENCE", 1.6))            # пауза, после которой фраза закончена, с
MAX_UTTERANCE = 120
